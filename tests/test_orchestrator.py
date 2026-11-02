import asyncio
import json

import pytest

from council.errors import AllMembersFailed, MemberCallError, PrivacyRefusal, ProxyError
from council.health import HealthStore
from council.orchestrator import Orchestrator
from council.runlog import RunLog
from council.types import CallResult, Member

GLM = Member(
    "council/cerebras-glm-4.7", "A", {"reasoning": 5, "general": 5, "code": 5}, 5, "cerebras"
)
GROQ = Member(
    "council/groq-gpt-oss-120b",
    "A",
    {"reasoning": 4, "code": 4, "general": 4, "fast": 4},
    30,
    "groq",
)
CF = Member("council/cloudflare-llama-70b", "A", {"general": 3, "fast": 3}, 10, "cloudflare")
TIERB = Member("council/some-b", "B", {"general": 2}, 10, "someb")
ALL = [GLM, GROQ, CF, TIERB]


class Recorder:
    def __init__(self, answer="A detailed multi sentence answer explaining the tradeoffs."):
        self.answer = answer
        self.calls = []

    async def __call__(self, alias, prompt):
        self.calls.append((alias, prompt))
        if "DISAGREEMENTS" in prompt:
            return "Merged.\nDISAGREEMENTS: none"
        if "Classify" in prompt:
            return "reasoning"
        return self.answer


def _orch(caller):
    return Orchestrator(ALL, caller)


def test_ask_direct_model_skips_classify():
    rec = Recorder()
    r = asyncio.run(_orch(rec).ask("hi", model="council/groq-gpt-oss-120b"))
    assert r.model_used == "council/groq-gpt-oss-120b" and r.note == "direct"
    assert all("Classify" not in p for _, p in rec.calls)


def test_ask_auto_classifies_then_selects_strongest():
    rec = Recorder()
    r = asyncio.run(_orch(rec).ask("prove a theorem"))
    # classify -> "reasoning" -> strongest reasoning member is GLM (strength 5)
    assert r.capability == "reasoning" and r.model_used == "council/cerebras-glm-4.7"


def test_ask_sensitive_refuses_tier_b_model():
    with pytest.raises(PrivacyRefusal):
        asyncio.run(_orch(Recorder()).ask("hi", model="council/some-b", sensitivity="sensitive"))


def test_council_auto_composes_vendor_diverse():
    rec = Recorder()
    r = asyncio.run(_orch(rec).council("explain the tradeoffs in depth please"))
    # sensitive -> only Tier-A (GLM/GROQ/CF); TIERB excluded
    assert {a.alias for a in r.per_member} == {
        "council/cerebras-glm-4.7", "council/groq-gpt-oss-120b", "council/cloudflare-llama-70b",
    }
    assert r.mode == "judge" and r.judge_used == "council/cerebras-glm-4.7"


def test_council_passes_confidence_through():
    class ConfCaller:
        calls = []

        async def __call__(self, alias, prompt):
            self.calls.append((alias, prompt))
            if "Classify" in prompt:
                return "reasoning"
            if "DISAGREEMENTS" in prompt:  # the judge prompt
                return "Merged.\nDISAGREEMENTS: none\nCONFIDENCE: high"
            return "A detailed multi sentence answer explaining the tradeoffs."

    r = asyncio.run(_orch(ConfCaller()).council("explain the tradeoffs in depth please"))
    assert r.confidence == "high"


def test_council_peer_rank_mode_reaches_aggregate():
    class RankCaller:
        calls = []

        async def __call__(self, alias, prompt):
            self.calls.append((alias, prompt))
            if "Classify" in prompt:
                return "reasoning"
            if "RANKING:" in prompt:  # the peer-rank ranker prompt
                return "RANKING: Aardvark, Basilisk, Cheetah"
            return "A detailed multi sentence answer explaining the tradeoffs."

    r = asyncio.run(_orch(RankCaller()).council("explain the tradeoffs", mode="peer-rank"))
    assert r.mode == "peer-rank"


def test_council_manual_roster_used():
    rec = Recorder()
    r = asyncio.run(_orch(rec).council("x", members=["council/groq-gpt-oss-120b"]))
    assert [a.alias for a in r.per_member] == ["council/groq-gpt-oss-120b"]


def test_council_manual_tier_b_dropped_on_sensitive():
    rec = Recorder()
    r = asyncio.run(_orch(rec).council(
        "x", members=["council/groq-gpt-oss-120b", "council/some-b"], sensitivity="sensitive"))
    aliases = {a.alias for a in r.per_member}
    assert "council/some-b" not in aliases
    assert "council/groq-gpt-oss-120b" in aliases
    assert "some-b" in r.note and "drop" in r.note.lower()
    assert all("council/some-b" != alias for alias, _ in rec.calls)  # never called


def test_council_manual_tier_b_allowed_on_public():
    rec = Recorder()
    r = asyncio.run(_orch(rec).council("x", members=["council/some-b"], sensitivity="public"))
    assert [a.alias for a in r.per_member] == ["council/some-b"]


def test_council_size_override():
    rec = Recorder()
    r = asyncio.run(_orch(rec).council("deep reasoning task", size=2))
    assert len(r.per_member) == 2


def test_council_unknown_alias_dropped_falls_back_to_auto():
    rec = Recorder()
    r = asyncio.run(_orch(rec).council("x", members=["council/nope"]))
    assert len(r.per_member) >= 1  # empty roster -> auto-composed
    assert "nope" in r.note


def test_ask_auto_falls_back_on_rate_limit():
    class FB:
        async def __call__(self, alias, prompt):
            if "Classify" in prompt:
                return "reasoning"
            if alias == "council/cerebras-glm-4.7":
                raise MemberCallError(alias, "429 rate-limited")
            return "fallback answer"

    r = asyncio.run(_orch(FB()).ask("prove a theorem"))
    assert r.model_used == "council/groq-gpt-oss-120b"
    assert "429" in r.note


def test_ask_raises_all_members_failed_when_all_rate_limited():
    class AllFail:
        async def __call__(self, alias, prompt):
            if "Classify" in prompt:
                return "reasoning"
            raise MemberCallError(alias, "429 rate-limited")

    with pytest.raises(AllMembersFailed):
        asyncio.run(_orch(AllFail()).ask("x"))


def test_classifier_never_a_tier_filtered_member():
    tierb_classifier = Member("council/tierb-classifier", "B", {"general": 2}, 99, "tierbc")
    o = Orchestrator([GLM, GROQ, CF, tierb_classifier], Recorder(),
                     classifier_alias="council/tierb-classifier")
    asyncio.run(o.ask("a reasoning task"))
    classify_calls = [alias for alias, prompt in o._caller.calls if "Classify" in prompt]
    assert classify_calls
    assert all(a != "council/tierb-classifier" for a in classify_calls)


def test_judge_order_excludes_chair_not_in_chosen():
    order = _orch(Recorder())._judge_order([GROQ, CF])
    assert "council/cerebras-glm-4.7" not in order
    assert order[0] == "council/groq-gpt-oss-120b"


def test_judge_order_puts_chair_first_when_present():
    order = _orch(Recorder())._judge_order([GROQ, CF, GLM])
    assert order[0] == "council/cerebras-glm-4.7"


def test_judge_order_derives_chair_from_reasoning_score():
    # No pin: the strongest "reasoning" member chairs even when listed last and weaker overall.
    thinker = Member("council/thinker", "A", {"reasoning": 5, "general": 2}, 5, "x")
    generalist = Member("council/generalist", "A", {"general": 5, "code": 5}, 30, "y")
    order = Orchestrator([generalist, thinker], Recorder())._judge_order([generalist, thinker])
    assert order == ["council/thinker", "council/generalist"]


def test_judge_order_honours_pinned_chair_when_in_council():
    o = Orchestrator(ALL, Recorder(), chair_alias=CF.alias)
    assert o._judge_order([GLM, GROQ, CF])[0] == CF.alias
    assert o._judge_order([GLM, GROQ])[0] == GLM.alias  # pin absent -> derived


def test_classifier_derived_from_fast_score_when_unpinned():
    sprinter = Member("council/sprinter", "A", {"fast": 5, "general": 2}, 5, "x")
    o = Orchestrator([GLM, GROQ, sprinter], Recorder())
    assert o._classifier_for([GLM, GROQ, sprinter]) == "council/sprinter"
    assert o._classifier_for([GLM, GROQ]) == GROQ.alias  # fast 4 beats GLM's missing fast


def test_classifier_pin_honoured_only_when_allowed():
    o = Orchestrator(ALL, Recorder(), classifier_alias=CF.alias)
    assert o._classifier_for([GLM, GROQ, CF]) == CF.alias
    assert o._classifier_for([GLM, GROQ]) == GROQ.alias


class _FakeStore:
    def __init__(self, counts):
        self._counts = counts

    def counts(self, day=None):
        return self._counts


def test_council_skips_exhausted_member():
    # GLM exhausted by tpd; council should fall back to the remaining trio members
    store = _FakeStore({"council/cerebras-glm-4.7": (0, 10**9)})
    members = [
        Member("council/cerebras-glm-4.7", "A", {"reasoning": 5}, 5, "cerebras", tpd=1000000),
        GROQ, CF,
    ]
    o = Orchestrator(members, Recorder(), store=store)
    r = asyncio.run(o.council("explain the tradeoffs in depth please"))
    assert "council/cerebras-glm-4.7" not in {a.alias for a in r.per_member}


def test_council_falls_back_when_all_exhausted():
    store = _FakeStore({m.alias: (0, 10**9) for m in [GLM, GROQ, CF]})
    members = [
        Member(GLM.alias, "A", GLM.scores, 5, "cerebras", tpd=1),
        Member(GROQ.alias, "A", GROQ.scores, 30, "groq", tpd=1),
        Member(CF.alias, "A", CF.scores, 10, "cloudflare", tpd=1),
    ]
    o = Orchestrator(members, Recorder(), store=store)
    r = asyncio.run(o.council("explain the tradeoffs in depth please"))
    assert len(r.per_member) == 3  # fell back to the full gated trio


def test_usage_summary_returns_rows():
    o = Orchestrator(ALL, Recorder(), store=_FakeStore({"council/cerebras-glm-4.7": (3, 500)}))
    rows = {row["alias"]: row for row in o.usage_summary()}
    assert rows["council/cerebras-glm-4.7"]["requests"] == 3


def test_build_default_config_path_is_absolute_and_cwd_independent(monkeypatch, tmp_path):
    from pathlib import Path

    from council import orchestrator, registry
    # The user-scope MCP server is spawned from arbitrary project directories; the config
    # path must resolve regardless of the current working directory.
    monkeypatch.chdir(tmp_path)
    assert Path(orchestrator.DEFAULT_CONFIG_PATH).is_absolute()
    members = registry.load_members(orchestrator.DEFAULT_CONFIG_PATH)  # no key filter -> all
    assert len(members) >= 10  # config found despite the foreign CWD


def test_council_passes_call_timeout_to_aggregate(monkeypatch):
    from council import aggregate as agg_mod
    from council.types import AggregateResult
    captured = {}

    async def fake_aggregate(prompt, answers, *, caller, judge_aliases, rng=None,
                             mode=None, timeout=30.0):
        captured["timeout"] = timeout
        return AggregateResult("x", "judge", "", "chair", "high")

    monkeypatch.setattr(agg_mod, "aggregate", fake_aggregate)
    o = Orchestrator(ALL, Recorder(), call_timeout=12.0)
    asyncio.run(o.council("explain the tradeoffs in depth please"))
    assert captured["timeout"] == 12.0


def test_usage_history_delegates_to_store():
    class HStore:
        def history(self, days=7):
            return [{"day": "2026-07-22", "alias": "council/x", "requests": 1, "tokens": 5}]

    o = Orchestrator(ALL, Recorder(), store=HStore())
    assert o.usage_history(3)[0]["day"] == "2026-07-22"


def test_usage_history_empty_without_store():
    assert Orchestrator(ALL, Recorder()).usage_history() == []


def test_council_logs_run_to_runlog(tmp_path):
    log_path = tmp_path / "runs.jsonl"
    o = Orchestrator(ALL, Recorder(), runlog=RunLog(log_path, enabled=True))
    asyncio.run(o.council("explain the tradeoffs in depth please"))
    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    entry = json.loads(lines[0])
    assert entry["tool"] == "council"
    assert "mode" in entry and "confidence" in entry and "per_member" in entry
    # sensitive (all-Tier-A) run keeps content and is not redacted
    assert entry["redacted"] is False
    assert entry["prompt"] == "explain the tradeoffs in depth please"
    assert entry["answer"] is not None


def test_council_passes_call_timeout_to_fanout(monkeypatch):
    from council import aggregate as agg_mod
    from council import fanout as fanout_mod
    from council.types import AggregateResult, MemberAnswer
    captured = {}

    async def fake_fan_out(prompt, members, caller, *, timeout=30.0):
        captured["timeout"] = timeout
        return [MemberAnswer(m.alias, True, "answer", "ok") for m in members]

    async def fake_aggregate(prompt, answers, *, caller, judge_aliases, rng=None,
                             mode=None, timeout=30.0):
        return AggregateResult("x", "judge", "", "chair", "high")

    monkeypatch.setattr(fanout_mod, "fan_out", fake_fan_out)
    monkeypatch.setattr(agg_mod, "aggregate", fake_aggregate)
    o = Orchestrator(ALL, Recorder(), call_timeout=9.0)
    asyncio.run(o.council("explain the tradeoffs in depth please"))
    assert captured["timeout"] == 9.0


def test_council_emits_progress_events():
    events = []
    asyncio.run(_orch(Recorder()).council("explain the tradeoffs in depth please",
                                           on_progress=events.append))
    kinds = [e["event"] for e in events]
    assert "roster" in kinds and "member" in kinds and "aggregating" in kinds


# --- Wave 2: health-aware pool, per-member timeouts, served-model attribution ---------



class Detailed:
    """DetailedCaller test double: per-alias overrides, else a long answer from the alias."""

    def __init__(self, served=None, fail=None, answer="A detailed multi sentence answer here."):
        self.served = served or {}
        self.fail = fail or {}
        self.answer = answer
        self.calls = []

    async def __call__(self, alias, prompt):
        self.calls.append(alias)
        if "Classify" in prompt:
            return CallResult("reasoning", alias)
        if "DISAGREEMENTS" in prompt:
            return CallResult("Merged.\nDISAGREEMENTS: none", alias)
        if alias in self.fail:
            raise self.fail[alias]
        return CallResult(self.answer, self.served.get(alias, alias))


def test_plain_caller_adapted_to_detailed():
    rec = Recorder()
    o = _orch(rec)
    r = asyncio.run(o.ask("hi", model=GROQ.alias))
    assert r.model_used == GROQ.alias and r.note == "direct"
    adapted = asyncio.run(o._detailed(GROQ.alias, "hi"))
    assert adapted == CallResult(rec.answer, GROQ.alias)
    assert asyncio.run(o._plain(GROQ.alias, "hi")) == rec.answer


def test_ask_waits_member_timeout():
    slow = Member("council/slow", "A", {"general": 5}, 5, "slowv")  # no own timeout
    patient = Member("council/patient", "A", {"general": 4}, 5, "patientv", timeout=0.5)

    async def caller(alias, prompt):
        await asyncio.sleep(0.2)
        return "answer"

    o = Orchestrator([slow, patient], caller, call_timeout=0.05)
    r = asyncio.run(o.ask("q", capability="general"))
    assert r.model_used == "council/patient"
    assert "council/slow[timeout]" in r.note
    direct = asyncio.run(o.ask("q", model="council/patient"))
    assert direct.answer == "answer"
    with pytest.raises(MemberCallError):
        asyncio.run(o.ask("q", model="council/slow"))


def test_pool_excludes_down_members(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    health.mark_down(GROQ.alias, "404 model not found", ttl_s=600)
    det = Detailed()
    o = Orchestrator(ALL, det, health=health)
    r = asyncio.run(o.council("explain the tradeoffs in depth please", size=3))
    assert GROQ.alias not in {a.alias for a in r.per_member}
    assert r.per_member  # the healthy members still answer
    assert "retrying" not in r.note
    pool, note = o._pool([GLM, GROQ, CF])
    assert [m.alias for m in pool] == [GLM.alias, CF.alias] and note is None


def test_pool_last_resort_when_all_down(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    for m in (GLM, GROQ, CF):
        health.mark_down(m.alias, "402 x", ttl_s=600)
    o = Orchestrator(ALL, Detailed(), health=health)
    r = asyncio.run(o.council("explain the tradeoffs in depth please"))
    assert r.per_member
    assert all(a.alias != TIERB.alias for a in r.per_member)  # never widens the tier gate
    assert "all members marked down; retrying" in r.note
    # the retried members answered, so they were marked back up
    assert health.down_aliases() == {m.alias for m in (GLM, GROQ, CF)} - {
        a.alias for a in r.per_member
    }
    for m in (GLM, GROQ, CF):
        health.mark_down(m.alias, "402 x", ttl_s=600)
    a = asyncio.run(o.ask("q", capability="general"))
    assert "all members marked down; retrying" in a.note


def test_pool_all_exhausted_and_down_falls_back_to_allowed_with_note(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    health.mark_down(GROQ.alias, "402 x", ttl_s=600)
    capped = [Member(m.alias, "A", m.scores, m.rpm, m.provider_family, tpd=1) for m in (GROQ, CF)]
    store = _FakeStore({m.alias: (0, 10) for m in capped})
    o = Orchestrator(capped, Detailed(), store=store, health=health)
    pool, note = o._pool(capped)
    assert pool == capped and note == "all members marked down; retrying"
    healthy = HealthStore(tmp_path / "h2.json")
    o2 = Orchestrator(capped, Detailed(), store=store, health=healthy)
    assert o2._pool(capped) == (capped, None)


def test_manual_roster_drops_down_member_with_reason(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    health.mark_down(GROQ.alias, "402 x", ttl_s=600)
    det = Detailed()
    o = Orchestrator(ALL, det, health=health)
    r = asyncio.run(
        o.council("explain the tradeoffs in depth please", members=[GROQ.alias, CF.alias])
    )
    assert "dropped council/groq-gpt-oss-120b (down: 402 x)" in r.note
    assert [a.alias for a in r.per_member] == [CF.alias]
    assert GROQ.alias not in det.calls


def test_permanent_failure_marks_down_and_success_marks_up(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    gone = MemberCallError(GROQ.alias, "404 model not found", permanent=True)
    det = Detailed(fail={GROQ.alias: gone, CF.alias: MemberCallError(CF.alias, "429 rate-limited")})
    o = Orchestrator(ALL, det, health=health)
    asyncio.run(o.council("explain the tradeoffs in depth please",
                          members=[GLM.alias, GROQ.alias, CF.alias]))
    assert health.status_of(GROQ.alias) == ("down", "404 model not found")
    assert health.status_of(CF.alias) == ("unknown", "")  # transient: no mark
    assert health.status_of(GLM.alias) == ("unknown", "")  # ok and was not down: no write
    # ask walk: a permanent failure marks the member down and the walk continues
    health2 = HealthStore(tmp_path / "h2.json")
    o2 = Orchestrator(ALL, Detailed(fail={GLM.alias: MemberCallError(
        GLM.alias, "402 payment required", permanent=True)}), health=health2)
    r = asyncio.run(o2.ask("q", capability="reasoning"))
    assert r.model_used == GROQ.alias
    assert health2.status_of(GLM.alias) == ("down", "402 payment required")
    # success for a down member (direct model= path) marks it back up
    health.mark_down(CF.alias, "402 x", ttl_s=600)
    o3 = Orchestrator(ALL, Detailed(), health=health)
    asyncio.run(o3.ask("q", model=CF.alias))
    assert health.status_of(CF.alias)[0] == "up"


def test_ask_reports_served_alias_and_note():
    det = Detailed(served={GROQ.alias: CF.alias})
    o = Orchestrator(ALL, det)
    r = asyncio.run(o.ask("what is 2+2", capability="fast"))
    assert det.calls[0] == GROQ.alias
    assert r.model_used == CF.alias
    assert "served by council/cloudflare-llama-70b (proxy fallback)" in r.note
    direct = asyncio.run(o.ask("what is 2+2", model=GROQ.alias))
    assert direct.model_used == CF.alias and "served by" in direct.note
    plain = asyncio.run(Orchestrator(ALL, Detailed()).ask("q", capability="fast"))
    assert "served by" not in plain.note


def test_council_notes_fallback_served_and_runlog_served_by(tmp_path):
    log_path = tmp_path / "runs.jsonl"
    det = Detailed(served={GROQ.alias: CF.alias})
    o = Orchestrator(ALL, det, runlog=RunLog(log_path, enabled=True))
    r = asyncio.run(o.council("explain the tradeoffs in depth please",
                              members=[GLM.alias, GROQ.alias]))
    assert "fallback-served: council/groq-gpt-oss-120b->council/cloudflare-llama-70b" in r.note
    by_alias = {a.alias: a for a in r.per_member}
    assert by_alias[GROQ.alias].served_by == CF.alias and by_alias[GLM.alias].served_by is None
    entry = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    per = {p["alias"]: p for p in entry["per_member"]}
    assert per[GROQ.alias]["served_by"] == CF.alias and per[GLM.alias]["served_by"] is None


def test_agg_timeout_is_max_of_chosen(monkeypatch):
    from council import aggregate as agg_mod
    from council.types import AggregateResult

    captured = {}

    async def fake_aggregate(prompt, answers, *, caller, judge_aliases, rng=None,
                             mode=None, timeout=30.0):
        captured["timeout"] = timeout
        captured["caller"] = caller
        return AggregateResult("x", "judge", "", "chair", "high")

    monkeypatch.setattr(agg_mod, "aggregate", fake_aggregate)
    quick = Member("council/quick", "A", {"general": 3}, 5, "q1")
    slow = Member("council/slow", "A", {"general": 3}, 5, "q2", timeout=60.0)
    o = Orchestrator([quick, slow], Detailed(), call_timeout=30.0)
    asyncio.run(o.council("explain please", members=[quick.alias, slow.alias]))
    assert captured["timeout"] == 60.0
    assert captured["caller"] == o._plain
    assert o._agg_timeout([quick]) == 30.0
    assert o._timeout_for(slow) == 60.0 and o._timeout_for(quick) == 30.0


def test_usage_summary_includes_health(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    health.mark_down(GROQ.alias, "402 x", ttl_s=600)
    o = Orchestrator(ALL, Recorder(), health=health)
    rows = {row["alias"]: row for row in o.usage_summary()}
    assert rows[GROQ.alias]["health"] == "down" and rows[GROQ.alias]["health_reason"] == "402 x"
    assert rows[CF.alias]["health"] == "unknown"
    assert o.health_probed_at() is None
    health.note_probe(CF.alias, "up", "")
    assert o.health_probed_at() is not None
    assert Orchestrator(ALL, Recorder()).health_probed_at() is None


def test_proxy_error_aborts_ask_without_health_change(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    boom = ProxyError("proxy rejected the request: HTTP 401")
    det = Detailed(fail={m.alias: boom for m in ALL})
    o = Orchestrator(ALL, det, health=health)
    with pytest.raises(ProxyError):
        asyncio.run(o.ask("q", capability="general"))
    assert len(det.calls) == 1  # no walking to the next member
    with pytest.raises(ProxyError):
        asyncio.run(o.council("explain please", members=[GLM.alias, CF.alias]))
    assert health.snapshot() == {}


def test_fallback_success_marks_up_served_alias_not_requested(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    health.mark_down(GROQ.alias, "402 x", ttl_s=600)
    health.mark_down(CF.alias, "402 y", ttl_s=600)
    o = Orchestrator(ALL, Detailed(served={GROQ.alias: CF.alias}), health=health)
    r = asyncio.run(o.ask("q", model=GROQ.alias))
    assert r.model_used == CF.alias
    assert CF.alias not in health.down_aliases()
    assert GROQ.alias in health.down_aliases()


def test_classifier_skips_down_member(tmp_path):
    # unpinned classifier = strongest "fast": GROQ (4) > CF (3); GROQ is down -> CF classifies
    health = HealthStore(tmp_path / "h.json")
    health.mark_down(GROQ.alias, "402 x", ttl_s=600)
    det = Detailed()
    o = Orchestrator(ALL, det, health=health)
    r = asyncio.run(o.ask("prove a theorem"))
    assert det.calls[0] == CF.alias  # the classify call
    assert GROQ.alias not in det.calls
    assert r.capability == "reasoning" and r.model_used == GLM.alias


def test_classifier_permanent_failure_defaults_to_general(tmp_path):
    health = HealthStore(tmp_path / "h.json")
    gone = MemberCallError(GROQ.alias, "404 model not found", permanent=True)

    class ClassifierGone(Detailed):
        async def __call__(self, alias, prompt):
            if alias == GROQ.alias and "Classify" in prompt:
                self.calls.append(alias)
                raise gone
            return await super().__call__(alias, prompt)

    det = ClassifierGone()
    o = Orchestrator(ALL, det, health=health)
    r = asyncio.run(o.ask("q"))
    assert r.capability == "general" and r.model_used == GLM.alias
    assert "classifier failed (404 model not found); defaulting to general" in r.note
    assert health.status_of(GROQ.alias) == ("down", "404 model not found")
    assert det.calls.count(GROQ.alias) == 1  # not retried in the walk once marked down
    # council: same degradation lands in the notes; the next classify skips GROQ
    c = asyncio.run(Orchestrator(ALL, ClassifierGone(), health=HealthStore(tmp_path / "h2.json"))
                    .council("explain the tradeoffs in depth please"))
    assert "classifier failed (404 model not found); defaulting to general" in c.note
    assert "auto: general" in c.note
    assert GROQ.alias not in {a.alias for a in c.per_member}


def test_classifier_timeout_defaults_to_general():
    slowfast = Member("council/slowfast", "A", {"fast": 5, "general": 1}, 5, "sf")
    gen = Member("council/gen", "A", {"general": 4}, 5, "gv")

    async def caller(alias, prompt):
        if alias == slowfast.alias:
            await asyncio.sleep(0.5)
        return "reasoning" if "Classify" in prompt else "answer"

    o = Orchestrator([slowfast, gen], caller, call_timeout=0.05)
    r = asyncio.run(o.ask("q"))
    assert r.capability == "general" and r.model_used == gen.alias
    assert "classifier failed (timeout); defaulting to general" in r.note


def test_classifier_proxy_error_propagates():
    class ProxyDown(Detailed):
        async def __call__(self, alias, prompt):
            raise ProxyError("proxy rejected the request: HTTP 401")

    with pytest.raises(ProxyError):
        asyncio.run(Orchestrator(ALL, ProxyDown()).ask("q"))
