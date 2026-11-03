"""Per-member call policy: how much effort to ask for, and how many tokens to allow.

One module so that every path that reaches a member -- the ``ask`` route, the
classifier, the aggregation chair and the council fan-out -- resolves the same two
parameters from the same rules. It is deliberately dependency-light (``client`` for the
pool-wide default, ``types`` for ``Member``) so both ``orchestrator`` and ``fanout`` can
import it without a cycle.
"""

from __future__ import annotations

from council import client
from council.types import Member

# Completion budgets. max_tokens is a CEILING and the free tiers charge only the tokens
# actually generated, so unused headroom costs nothing -- while too small a ceiling
# truncates an answer mid-sentence, or spends the whole budget on hidden thinking and
# returns NOTHING (observed live at 2048: truncated groq-gpt-oss-120b, empty
# groq-gpt-oss-20b, empty cloudflare-nemotron-120b).
#
# Bound to the transport's own default ON PURPOSE, not duplicated: a call that resolves
# no budget falls through to ``client.DEFAULT_MAX_TOKENS``, so two independent literals
# would let two paths silently drift apart on the same member.
DEFAULT_MAX_TOKENS = client.DEFAULT_MAX_TOKENS
# A member that reasons with an explicit effort spends part of its budget on hidden
# thinking tokens, so it needs a bigger ceiling than a plain completion.
REASONING_MAX_TOKENS = 8192


def resolve_call_params(member: Member, *, reasoning: bool) -> tuple[str | None, int]:
    """One member call's (reasoning_effort, max_tokens).

    Effort reaches only a member whose config flags it as accepting the parameter --
    support is a property of the MEMBER, not of the weights or the vendor (the same
    gpt-oss-120b accepts the parameter on Groq and answers HTTP 400
    ``UnsupportedParamsError`` behind Cloudflare's OpenAI shim).

    The effort is never ``"high"``: probed on groq-gpt-oss-120b, ``high`` spends the
    whole budget on thinking and returns an EMPTY answer below 8192 tokens, while
    ``medium`` at 4096 returns a complete answer of the same length.
    """
    effort = ("medium" if reasoning else "low") if member.accepts_reasoning_effort else None
    if member.max_tokens is not None:
        budget = member.max_tokens  # an explicit per-member budget is honoured verbatim
    elif member.accepts_reasoning_effort:
        budget = REASONING_MAX_TOKENS
    else:
        budget = DEFAULT_MAX_TOKENS
    return effort, budget
