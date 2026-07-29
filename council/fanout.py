from __future__ import annotations

import asyncio
from collections.abc import Callable

from council.errors import MemberCallError
from council.types import AsyncCaller, Member, MemberAnswer

OnMember = Callable[[str, bool], None]


async def _call_one(
    member: Member, prompt: str, caller: AsyncCaller, sem: asyncio.Semaphore, timeout: float,
    on_member: OnMember | None = None,
) -> MemberAnswer:
    async with sem:
        try:
            answer = await asyncio.wait_for(caller(member.alias, prompt), timeout)
        except MemberCallError as exc:
            ans = MemberAnswer(member.alias, ok=False, answer=None, detail=exc.detail)
        except (asyncio.TimeoutError, TimeoutError):
            ans = MemberAnswer(member.alias, ok=False, answer=None, detail="timeout")
        except Exception as exc:  # noqa: BLE001 - a misbehaving member must not crash the council
            ans = MemberAnswer(
                member.alias, ok=False, answer=None, detail=f"error: {exc.__class__.__name__}"
            )
        else:
            ans = MemberAnswer(member.alias, ok=True, answer=answer, detail="ok")
    if on_member is not None:
        on_member(member.alias, ans.ok)
    return ans


async def fan_out(
    prompt: str, members: list[Member], caller: AsyncCaller, *, timeout: float = 30.0,
    on_member: OnMember | None = None,
) -> list[MemberAnswer]:
    # Per-member semaphore (sized to rpm) guards against saturating a member when the
    # same member is called concurrently; harmless at one-call-per-member.
    sems = {m.alias: asyncio.Semaphore(max(1, m.rpm)) for m in members}
    tasks = [
        asyncio.create_task(_call_one(m, prompt, caller, sems[m.alias], timeout, on_member))
        for m in members
    ]
    return list(await asyncio.gather(*tasks))
