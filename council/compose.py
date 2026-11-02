from __future__ import annotations

from council.types import Member

_K_BY_CAPABILITY = {"fast": 3, "code": 4, "general": 4, "reasoning": 5}


def adaptive_k(capability: str) -> int:
    return _K_BY_CAPABILITY.get(capability, 4)


def _score(member: Member, capability: str) -> int:
    # A member that does not list the capability scores 0 for it — never its best
    # unrelated score — so a "fast" specialist cannot outrank reasoners on a reasoning task.
    return member.scores.get(capability, 0)


def compose_council(members: list[Member], *, k: int, capability: str) -> list[Member]:
    if not members or k <= 0:
        return []
    ranked = sorted(
        members, key=lambda m: (_score(m, capability), m.strength, m.rpm), reverse=True
    )
    picked: list[Member] = []
    families: set[str] = set()
    for m in ranked:  # pass 1 — one per distinct family among members that HAVE the capability
        if len(picked) >= k:
            break
        if _score(m, capability) > 0 and m.provider_family not in families:
            picked.append(m)
            families.add(m.provider_family)
    for m in ranked:  # pass 2 — fill remaining slots by score (capable members sort first)
        if len(picked) >= k:
            break
        if m not in picked:
            picked.append(m)
    return picked[:k]
