from pathlib import Path

import pytest

from consilium import env_file
from consilium_mcp import server
from council.types import AskResult, CouncilResult, MemberAnswer


def test_load_master_key_delegates_to_env_file(monkeypatch):
    monkeypatch.setattr(env_file, "master_key", lambda: "sk-x")
    assert server._load_master_key() == "sk-x"


def test_load_master_key_missing_raises(monkeypatch):
    monkeypatch.setattr(env_file, "master_key", lambda: None)
    with pytest.raises(RuntimeError):
        server._load_master_key()


def test_shape_ask():
    r = AskResult(answer="a", model_used="council/x", capability="code", note="routed: code")
    assert server._shape_ask(r) == {
        "answer": "a", "model_used": "council/x", "capability": "code", "note": "routed: code",
    }


def test_shape_council():
    r = CouncilResult(
        answer="merged",
        per_member=[MemberAnswer("council/x", True, "hi", "ok")],
        disagreements="none",
        judge_used="council/x",
        mode="judge",
    )
    out = server._shape_council(r)
    assert out["answer"] == "merged" and out["mode"] == "judge"
    assert out["per_member"][0] == {
        "alias": "council/x", "ok": True, "detail": "ok", "answer": "hi", "served_by": None,
    }


def test_server_inserts_repo_root_before_council_import():
    text = Path(server.__file__).read_text()
    insert = text.find("sys.path.insert")
    council = text.find("from council")
    assert insert != -1, "server.py must insert the repo root on sys.path"
    assert council != -1
    assert insert < council, "the sys.path insert must precede the council import"


def test_stats_returns_today_and_total_cost(monkeypatch):
    class FakeOrch:
        def usage_summary(self):
            return [{"alias": "council/x", "requests": 2, "tokens": 10,
                     "exhausted": False, "cost_usd": 0.0}]

        def health_probed_at(self):
            return None

    monkeypatch.setattr(server, "_orch", FakeOrch())
    import asyncio

    out = asyncio.run(server.stats())
    assert out["today"][0]["alias"] == "council/x" and out["today"][0]["requests"] == 2
    assert out["total_cost_usd"] == 0.0
    assert "history" not in out  # days=1 -> no history block


def test_stats_days_includes_history(monkeypatch):
    class FakeOrch:
        def usage_summary(self):
            return [{"alias": "council/x", "requests": 1, "tokens": 5, "cost_usd": 0.0}]

        def usage_history(self, days):
            return [{"day": "2026-07-23", "alias": "council/x", "requests": 1, "tokens": 5}]

        def health_probed_at(self):
            return None

    monkeypatch.setattr(server, "_orch", FakeOrch())
    import asyncio

    out = asyncio.run(server.stats(days=7))
    assert out["history"][0]["day"] == "2026-07-23"
    assert out["total_cost_usd"] == 0.0


def test_shape_council_includes_note():
    r = CouncilResult(
        answer="m", per_member=[MemberAnswer("council/x", True, "hi", "ok")],
        disagreements="none", judge_used="council/x", mode="judge", note="auto: code, k=4",
    )
    assert server._shape_council(r)["note"] == "auto: code, k=4"


def test_shape_council_includes_confidence():
    r = CouncilResult(
        answer="m", per_member=[], disagreements="none", judge_used="council/x",
        mode="judge", note="", confidence="high",
    )
    assert server._shape_council(r)["confidence"] == "high"


def test_council_tool_passes_members_and_size(monkeypatch):
    captured = {}

    class FakeOrch:
        async def council(
            self, prompt, *, members=None, size=None, mode=None, sensitivity="sensitive",
        ):
            captured.update(prompt=prompt, members=members, size=size, sensitivity=sensitivity)
            return CouncilResult("a", [], "none", None, "judge", note="ok")

    monkeypatch.setattr(server, "_orch", FakeOrch())
    import asyncio
    asyncio.run(server.council("q", sensitivity="public", members=["council/x"], size=3))
    assert captured == {
        "prompt": "q", "members": ["council/x"], "size": 3, "sensitivity": "public",
    }


def test_council_tool_passes_mode(monkeypatch):
    captured = {}

    class FakeOrch:
        async def council(
            self, prompt, *, members=None, size=None, mode=None, sensitivity="sensitive",
        ):
            captured.update(mode=mode, members=members, size=size, sensitivity=sensitivity)
            return CouncilResult("a", [], "none", None, "peer-rank", note="ok", confidence="high")

    monkeypatch.setattr(server, "_orch", FakeOrch())
    import asyncio
    out = asyncio.run(server.council("q", mode="peer-rank"))
    assert captured["mode"] == "peer-rank" and out["mode"] == "peer-rank"


def test_council_tool_passes_debate_mode(monkeypatch):
    captured = {}

    class FakeOrch:
        async def council(self, prompt, *, members=None, size=None, mode=None,
                          sensitivity="sensitive"):
            captured["mode"] = mode
            return CouncilResult("a", [], "none", "council/x", "debate", note="",
                                 confidence="high")

    monkeypatch.setattr(server, "_orch", FakeOrch())
    import asyncio
    out = asyncio.run(server.council("q", mode="debate"))
    assert captured["mode"] == "debate" and out["mode"] == "debate"


def test_shape_council_exposes_served_by():
    r = CouncilResult(
        answer="m",
        per_member=[MemberAnswer("council/x", True, "hi", "ok", served_by="council/x2")],
        disagreements="none", judge_used="council/x", mode="judge",
    )
    assert server._shape_council(r)["per_member"][0]["served_by"] == "council/x2"


def test_stats_includes_health_probed_at(monkeypatch):
    class FakeOrch:
        def usage_summary(self):
            return [{"alias": "council/x", "requests": 0, "tokens": 0, "cost_usd": 0.0,
                     "health": "ok", "health_reason": "", "timeout": 30}]

        def health_probed_at(self):
            return "2026-11-02T10:00:00+00:00"

    monkeypatch.setattr(server, "_orch", FakeOrch())
    import asyncio

    out = asyncio.run(server.stats())
    assert out["health_probed_at"] == "2026-11-02T10:00:00+00:00"
    assert out["today"][0]["health"] == "ok" and out["today"][0]["timeout"] == 30


def test_stats_without_health_file(monkeypatch):
    class FakeOrch:
        def usage_summary(self):
            return [{"alias": "council/x", "requests": 0, "tokens": 0, "cost_usd": 0.0,
                     "health": "unknown", "health_reason": "", "timeout": 30}]

        def health_probed_at(self):
            return None

    monkeypatch.setattr(server, "_orch", FakeOrch())
    import asyncio

    out = asyncio.run(server.stats())
    assert out["health_probed_at"] is None
    assert out["today"][0]["health"] == "unknown"
