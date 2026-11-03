from __future__ import annotations

import asyncio
from collections.abc import Callable

from council import budget
from council.errors import MemberCallError, ProxyError
from council.types import DetailedCaller, Member, MemberAnswer

OnMember = Callable[[str, bool], None]


async def _call_one(
    member: Member, prompt: str, caller: DetailedCaller, sem: asyncio.Semaphore,
    timeout: float, on_member: OnMember | None = None, reasoning: bool = False,
) -> MemberAnswer:
    effort, max_tokens = budget.resolve_call_params(member, reasoning=reasoning)
    async with sem:
        try:
            res = await asyncio.wait_for(
                caller(member.alias, prompt, reasoning_effort=effort, max_tokens=max_tokens),
                member.timeout or timeout,
            )
        except ProxyError:
            raise  # the proxy itself is broken: abort the council, it is not a member failure
        except MemberCallError as exc:
            ans = MemberAnswer(
                member.alias, ok=False, answer=None, detail=exc.detail, permanent=exc.permanent
            )
        except (asyncio.TimeoutError, TimeoutError):
            ans = MemberAnswer(member.alias, ok=False, answer=None, detail="timeout")
        except Exception as exc:  # noqa: BLE001 - a misbehaving member must not crash the council
            ans = MemberAnswer(
                member.alias, ok=False, answer=None, detail=f"error: {exc.__class__.__name__}"
            )
        else:
            served_by = res.served_alias if res.served_alias != member.alias else None
            ans = MemberAnswer(
                member.alias, ok=True, answer=res.content, detail="ok", served_by=served_by
            )
    if on_member is not None:
        on_member(member.alias, ans.ok)
    return ans


async def fan_out(
    prompt: str, members: list[Member], caller: DetailedCaller, *, timeout: float = 30.0,
    on_member: OnMember | None = None, reasoning: bool = False,
) -> list[MemberAnswer]:
    # `reasoning` is the INTENT of the fan-out: each voter answers the question itself,
    # which is ordinary work, so the default False asks for the LOW effort on a member
    # that accepts one. Sending nothing is not the same as sending "low": probed live on
    # a hard prompt with no effort, groq-gpt-oss-120b truncated at 2048 and
    # groq-gpt-oss-20b returned an EMPTY answer, while "low" at 4096 completed both.
    #
    # Every chosen voter is awaited up to its own timeout (member.timeout, else `timeout`);
    # there is no quorum / early exit. Per-member semaphore (sized to rpm) guards against
    # saturating a member when the same member is called concurrently.
    sems = {m.alias: asyncio.Semaphore(max(1, m.rpm)) for m in members}
    tasks = [
        asyncio.create_task(
            _call_one(m, prompt, caller, sems[m.alias], timeout, on_member, reasoning)
        )
        for m in members
    ]
    try:
        return list(await asyncio.gather(*tasks))
    except BaseException:
        # A ProxyError (or cancellation) aborts the council: do not leave siblings running.
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        raise
