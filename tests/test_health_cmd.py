import asyncio
import json

import httpx

from consilium import health_cmd
from council.health import HealthStore
from council.types import Member

BASE = "http://proxy.test/v1"
KEY = "sk-test-secret"


def _m(alias, vendor, rpm=30, tier="A", timeout=None):
    return Member(alias=alias, privacy_tier=tier, scores={"general": 1}, rpm=rpm,
                  vendor=vendor, timeout=timeout)


def test_classify_probe_table():
    assert health_cmd.classify_probe(200, "", None) == ("up", "")
    assert health_cmd.classify_probe(None, "", "timeout") == ("unknown", "timeout")
    assert health_cmd.classify_probe(401, "bad key", None) == ("unknown", "proxy auth")
    assert health_cmd.classify_probe(404, "NotFoundError: GroqException - x", None) == (
        "down", "404 model not found")
    assert health_cmd.classify_probe(429, "GroqException rate", None) == (
        "unknown", "429 rate-limited")
    assert health_cmd.classify_probe(503, "", None) == ("unknown", "HTTP 503")


def _transport(statuses, calls):
    def handler(request):
        if request.method == "GET":
            calls.append(("GET", request.url.path))
            return httpx.Response(statuses.get("/models", 200), json={"data": []})
        payload = json.loads(request.content)
        calls.append(("POST", payload["model"]))
        assert request.url.path == "/v1/chat/completions"
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert payload["max_tokens"] == 1
        assert payload["messages"] == [{"role": "user", "content": "ping"}]
        code, body = statuses.get(payload["model"], (200, '{"choices": []}'))
        return httpx.Response(code, text=body)

    return httpx.MockTransport(handler)


def test_probe_members_sequential_within_vendor_with_gap():
    members = [_m("m1", "mistral", rpm=1, tier="B"), _m("g1", "groq"),
               _m("m2", "mistral", rpm=1, tier="B"), _m("g2", "groq")]
    calls = []
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    outcomes = asyncio.run(health_cmd.probe_members(
        members, BASE, KEY, transport=_transport({}, calls), sleep=fake_sleep))
    assert slept == [1.0]  # one gap between the two mistral members; none for groq
    assert [o.alias for o in outcomes] == ["m1", "g1", "m2", "g2"]
    assert all(o.status == "up" and o.reason == "" for o in outcomes)
    assert [o.tier for o in outcomes] == ["B", "A", "B", "A"]
    assert all(isinstance(o.ms, int) and o.ms >= 0 for o in outcomes)
    assert len(calls) == 4
    assert [c[1] for c in calls if c[1].startswith("m")] == ["m1", "m2"]


def test_probe_members_single_slow_vendor_member_no_sleep():
    slept = []

    async def fake_sleep(s):
        slept.append(s)

    asyncio.run(health_cmd.probe_members(
        [_m("m1", "mistral", rpm=1)], BASE, KEY, transport=_transport({}, []),
        sleep=fake_sleep))
    assert slept == []


def test_probe_members_timeout_is_unknown():
    async def handler(request):
        await asyncio.sleep(5)
        return httpx.Response(200)

    outcomes = asyncio.run(health_cmd.probe_members(
        [_m("slow", "groq", timeout=0.01)], BASE, KEY,
        transport=httpx.MockTransport(handler)))
    assert outcomes[0].status == "unknown" and outcomes[0].reason == "timeout"


def test_probe_members_transport_error_is_unknown():
    def handler(request):
        raise httpx.ConnectError("boom")

    outcomes = asyncio.run(health_cmd.probe_members(
        [_m("g1", "groq")], BASE, KEY, transport=httpx.MockTransport(handler)))
    assert outcomes[0].status == "unknown" and "ConnectError" in outcomes[0].reason


def test_apply_marks_store(tmp_path):
    store = HealthStore(tmp_path / "health.json")
    outcomes = [
        health_cmd.ProbeOutcome("a", "A", "up", "", 10),
        health_cmd.ProbeOutcome("b", "A", "down", "404 model not found", 20),
        health_cmd.ProbeOutcome("c", "B", "unknown", "timeout", 30),
    ]
    health_cmd.apply(outcomes, store)
    snap = store.snapshot()
    assert store.status_of("a") == ("up", "")
    assert snap["a"]["source"] == "probe"
    assert snap["a"]["last_probe"]["status"] == "up"
    assert store.status_of("b") == ("down", "404 model not found")
    assert snap["b"]["source"] == "probe"
    assert snap["b"]["last_probe"]["reason"] == "404 model not found"
    assert "status" not in snap["c"]  # unknown never changes the up/down state
    assert snap["c"]["last_probe"]["status"] == "unknown"
    assert snap["c"]["last_probe"]["reason"] == "timeout"
    assert store.probed_at() is not None


def test_render_table_and_summary():
    outcomes = [
        health_cmd.ProbeOutcome("council/a", "A", "up", "", 12),
        health_cmd.ProbeOutcome("council/b", "B", "down", "404 model not found", 7),
        health_cmd.ProbeOutcome("council/c", "A", "unknown", "timeout", 30000),
    ]
    text = health_cmd.render(outcomes, "2026-11-02T10:00:00+00:00")
    assert "2026-11-02T10:00:00+00:00" in text
    assert "council/b" in text and "404 model not found" in text
    assert text.splitlines()[-1] == "1 up, 1 down, 1 unknown"


def _health(tmp_path, statuses, members, argv=None):
    calls, out = [], []
    store = HealthStore(tmp_path / "health.json")
    rc = health_cmd.health(argv or [], api_key=KEY, base_url=BASE, store=store,
                           members=members, transport=_transport(statuses, calls),
                           echo=out.append)
    return rc, calls, out, store


def test_health_exit_codes(tmp_path, monkeypatch):
    members = [_m("g1", "groq"), _m("g2", "groq")]
    rc, calls, out, _ = _health(tmp_path, {}, members)
    assert rc == 0 and out[-1].splitlines()[-1] == "2 up, 0 down, 0 unknown"

    rc, _, _, _ = _health(tmp_path, {"g2": (404, "GroqException - model not found")}, members)
    assert rc == 1

    rc, _, _, _ = _health(tmp_path, {"g2": (429, "GroqException - rate")}, members)
    assert rc == 0  # 429 is never "down"

    rc, calls, out, _ = _health(tmp_path, {"/models": 401}, members)
    assert rc == 2
    assert calls == [("GET", "/v1/models")]  # no probes sent
    assert out == ["ERROR: proxy rejected the master key (HTTP 401)"]

    monkeypatch.setattr(health_cmd.env_file, "master_key", lambda *a, **k: None)
    out = []
    assert health_cmd.health([], base_url=BASE, members=members, echo=out.append,
                             store=HealthStore(tmp_path / "h2.json")) == 2
    assert out == ["ERROR: LITELLM_MASTER_KEY not found"]


def test_health_proxy_unreachable(tmp_path):
    def handler(request):
        raise httpx.ConnectError("refused")

    out = []
    rc = health_cmd.health([], api_key=KEY, base_url=BASE, members=[_m("g1", "groq")],
                           store=HealthStore(tmp_path / "h.json"),
                           transport=httpx.MockTransport(handler), echo=out.append)
    assert rc == 2 and out == [f"ERROR: proxy unreachable at {BASE}"]


def test_health_json_flag(tmp_path):
    rc, _, out, _ = _health(tmp_path, {}, [_m("g1", "groq")], argv=["--json"])
    assert rc == 0
    assert len(out) == 1
    data = json.loads(out[0])
    assert data["g1"]["status"] == "up"
    assert data["g1"]["last_probe"]["status"] == "up"


def test_health_never_echoes_key(tmp_path):
    for statuses in ({}, {"/models": 401}, {"g1": (404, "GroqException - gone")}):
        _, _, out, _ = _health(tmp_path, statuses, [_m("g1", "groq")])
        assert all(KEY not in line for line in out)
