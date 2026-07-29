from __future__ import annotations

from collections.abc import Mapping, Sequence

_PREAMBLE = (
    "You are a helpful assistant answering within an ongoing conversation. "
    "Use the prior turns for context; answer the latest user message.\n\n"
)


def build_prompt(
    history: Sequence[Mapping[str, object]],
    user_message: str,
    *,
    turns: int,
    char_budget: int,
) -> str:
    recent = history[-turns:] if turns > 0 else []
    lines = [f"{'User' if m['role'] == 'user' else 'Assistant'}: {m['content']}"
             for m in recent]
    tail = f"\nUser: {user_message}"
    # drop oldest lines until within budget (preamble + lines + tail)
    while lines and len(_PREAMBLE) + len("\n".join(lines)) + len(tail) > char_budget:
        lines.pop(0)
    body = "\n".join(lines)
    return f"{_PREAMBLE}{body}{tail}" if body else f"{_PREAMBLE}{user_message}"
