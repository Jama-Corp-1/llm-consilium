import asyncio

import pytest

from council import fanout
from council.errors import MemberCallError, ProxyError
from council.types import CallResult, Member

M1 = Member("m1", "A", {"general": 3}, 5, "m1")
M2 = Member("m2", "A", {"general": 3}, 5, "m2")


def test_all_ok():
    async def caller(alias, prompt):
        return CallResult(f"ans-{alias}", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].ok and res["m1"].answer == "ans-m1"
    assert res["m2"].ok


def test_one_abstains_on_call_error():
    async def caller(alias, prompt):
        if alias == "m2":
            raise MemberCallError("m2", "429 rate-limited")
        return CallResult("ok", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].ok
    assert not res["m2"].ok and res["m2"].answer is None and "429" in res["m2"].detail


def test_one_abstains_on_unexpected_error():
    async def caller(alias, prompt):
        if alias == "m2":
            raise ValueError("boom")
        return CallResult("ok", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].ok
    assert res["m2"].ok is False and "error" in res["m2"].detail


def test_slow_member_times_out():
    async def caller(alias, prompt):
        if alias == "m2":
            await asyncio.sleep(1)
        return CallResult("ok", alias)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller, timeout=0.05))}
    assert res["m1"].ok
    assert not res["m2"].ok and res["m2"].detail == "timeout"


def test_fan_out_reports_progress_per_member():
    seen = []

    async def caller(alias, prompt):
        return CallResult("ok", alias)

    members = [Member("a", "A", {"general": 3}, 5, "a"), Member("b", "A", {"general": 3}, 5, "b")]
    asyncio.run(fanout.fan_out("q", members, caller,
                               on_member=lambda alias, ok: seen.append((alias, ok))))
    assert set(seen) == {("a", True), ("b", True)}


def test_fanout_waits_member_timeout_not_default():
    patient = Member("p", "A", {"general": 3}, 5, "p", timeout=1.0)

    async def caller(alias, prompt):
        await asyncio.sleep(0.1)
        return CallResult("ok", alias)

    (ans,) = asyncio.run(fanout.fan_out("q", [patient], caller, timeout=0.05))
    assert ans.ok and ans.answer == "ok" and ans.detail == "ok"


def test_fanout_sets_served_by_only_when_different():
    async def caller(alias, prompt):
        return CallResult("ok", "m-other" if alias == "m2" else alias, 1 if alias == "m2" else 0)

    res = {a.alias: a for a in asyncio.run(fanout.fan_out("q", [M1, M2], caller))}
    assert res["m1"].served_by is None
    assert res["m2"].served_by == "m-other"


def test_fanout_marks_permanent_failures():
    async def caller(alias, prompt):
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
    async def caller(alias, prompt):
        if alias == "m2":
            raise ProxyError("proxy rejected the request: HTTP 401")
        return CallResult("ok", alias)

    with pytest.raises(ProxyError):
        asyncio.run(fanout.fan_out("q", [M1, M2], caller))
