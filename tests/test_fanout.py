import asyncio

import pytest

from council import fanout
from council.errors import MemberCallError, ProxyError
from council.types import CallResult, Member

M1 = Member("m1", "A", {"general": 3}, 5, "m1")
M2 = Member("m2", "A", {"general": 3}, 5, "m2")


def test_all_ok():
    async def caller(alias, prompt, **_params):
        return CallResult(f"ans-{alias}", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].ok and res["m1"].answer == "ans-m1"
    assert res["m2"].ok


def test_one_abstains_on_call_error():
    async def caller(alias, prompt, **_params):
        if alias == "m2":
            raise MemberCallError("m2", "429 rate-limited")
        return CallResult("ok", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].ok
    assert not res["m2"].ok and res["m2"].answer is None and "429" in res["m2"].detail


def test_one_abstains_on_unexpected_error():
    async def caller(alias, prompt, **_params):
        if alias == "m2":
            raise ValueError("boom")
        return CallResult("ok", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].ok
    assert res["m2"].ok is False and "error" in res["m2"].detail


def test_slow_member_times_out():
    async def caller(alias, prompt, **_params):
        if alias == "m2":
            await asyncio.sleep(1)
        return CallResult("ok", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller, timeout=0.05))}
    assert res["m1"].ok
    assert not res["m2"].ok and res["m2"].detail == "timeout"


def test_fan_out_reports_progress_per_member():
    seen = []

    async def caller(alias, prompt, **_params):
        return CallResult("ok", alias)

    members = [Member("a", "A", {"general": 3}, 5, "a"), Member("b", "A", {"general": 3}, 5, "b")]
    asyncio.run(fanout.fan_out("q", members, caller,
                               on_member=lambda alias, ok: seen.append((alias, ok))))
    assert set(seen) == {("a", True), ("b", True)}


def test_fanout_waits_member_timeout_not_default():
    patient = Member("p", "A", {"general": 3}, 5, "p", timeout=1.0)

    async def caller(alias, prompt, **_params):
        await asyncio.sleep(0.1)
        return CallResult("ok", alias)

    (ans,) = asyncio.run(fanout.fan_out("q", [patient], caller, timeout=0.05))
    assert ans.ok and ans.answer == "ok" and ans.detail == "ok"


def test_fanout_sets_served_by_only_when_different():
    async def caller(alias, prompt, **_params):
        return CallResult("ok", "m-other" if alias == "m2" else alias, 1 if alias == "m2" else 0)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].served_by is None
    assert res["m2"].served_by == "m-other"


def test_fanout_marks_permanent_failures():
    async def caller(alias, prompt, **_params):
        if alias == "m2":
            raise MemberCallError("m2", "404 model not found", permanent=True)
        if alias == "m1":
            raise MemberCallError("m1", "429 rate-limited")
        return CallResult("ok", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m2"].ok is False and res["m2"].permanent is True
    assert res["m2"].detail == "404 model not found"
    assert res["m1"].ok is False and res["m1"].permanent is False


def test_fanout_reraises_proxy_error():
    async def caller(alias, prompt, **_params):
        if alias == "m2":
            raise ProxyError("proxy rejected the request: HTTP 401")
        return CallResult("ok", alias)

    with pytest.raises(ProxyError):
        asyncio.run(fanout.fan_out("q", [M1, M2], caller))


# --- Wave 3 fix round 2: voters get a per-member effort, not the provider default ------

GROQ_VOTER = Member("groq-voter", "A", {"general": 4}, 30, "groq", accepts_reasoning_effort=True)
CF_VOTER = Member("cf-voter", "A", {"general": 3}, 10, "cloudflare")


def test_fan_out_resolves_effort_and_budget_per_member():
    """A Groq voter gets "low"; a Cloudflare voter, which 400s on the parameter, gets none.

    Sending nothing is not the same as sending "low": probed live on a hard prompt with
    no effort, groq-gpt-oss-120b truncated at 2048 and groq-gpt-oss-20b answered EMPTY.
    """
    seen = {}

    async def caller(alias, prompt, *, reasoning_effort=None, max_tokens=None):
        seen[alias] = (reasoning_effort, max_tokens)
        return CallResult("ok", alias)

    answers = asyncio.run(fanout.fan_out("q", [GROQ_VOTER, CF_VOTER], caller))
    assert all(a.ok for a in answers)
    assert seen["groq-voter"] == ("low", 8192)
    assert seen["cf-voter"] == (None, 4096)


def test_fan_out_never_asks_a_voter_for_medium_effort():
    """Voters are not reasoning-intent work even when the intent flag is set by mistake."""
    seen = []

    async def caller(alias, prompt, *, reasoning_effort=None, max_tokens=None):
        seen.append(reasoning_effort)
        return CallResult("ok", alias)

    asyncio.run(fanout.fan_out("q", [GROQ_VOTER, CF_VOTER], caller))
    assert "medium" not in seen and "high" not in seen
