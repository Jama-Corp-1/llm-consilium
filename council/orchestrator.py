from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from pathlib import Path

from council import aggregate as agg
from council import client, compose, fanout, heuristics, privacy, registry, router, usage
from council import health as health_module
from council import runlog as runlog_module
from council.errors import AllMembersFailed, MemberCallError, NoEligibleMember, PrivacyRefusal
from council.types import (
    AskResult,
    AsyncCaller,
    CallResult,
    CouncilResult,
    DetailedCaller,
    Member,
    MemberAnswer,
)

DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"
# Absolute so the user-scope MCP server finds the config no matter which project
# directory Claude Code spawned it from (a relative path breaks cross-project use).
DEFAULT_CONFIG_PATH = str(Path(__file__).resolve().parents[1] / "proxy" / "config.yaml")
# Optional pins. By default both roles are DERIVED from the member scores so a catalog
# change never leaves the council pointing at a withdrawn model: the classifier is the
# strongest "fast" member of the allowed set, the chair the strongest "reasoning" member
# of the chosen council. A pin is honoured only when it survived the privacy gate.
CLASSIFIER_ALIAS: str | None = None
CHAIR_ALIAS: str | None = None
ALL_DOWN_NOTE = "all members marked down; retrying"


class Orchestrator:
    def __init__(
        self,
        members: list[Member],
        caller: AsyncCaller | DetailedCaller,
        *,
        classifier_alias: str | None = CLASSIFIER_ALIAS,
        chair_alias: str | None = CHAIR_ALIAS,
        store: usage.UsageStore | None = None,
        runlog: runlog_module.RunLog | None = None,
        call_timeout: float = 30.0,
        health: health_module.HealthStore | None = None,
        local_classify: bool = True,
    ) -> None:
        self._members = members
        self._caller = caller  # as given: plain-text (AsyncCaller) or DetailedCaller
        self._detailed: DetailedCaller = self._call_detailed
        self._plain: AsyncCaller = self._call_plain  # aggregate consumes plain text
        self._health = health
        self._classifier_alias = classifier_alias
        self._chair_alias = chair_alias
        self._store = store
        self._runlog = runlog
        self._call_timeout = call_timeout
        self._local_classify = local_classify

    def _by_alias(self, alias: str) -> Member | None:
        return next((m for m in self._members if m.alias == alias), None)

    def _tier_of(self, alias: str) -> str:
        member = self._by_alias(alias)
        return member.privacy_tier if member else "?"

    def _log_council(self, entry: dict[str, object], chosen: list[Member]) -> None:
        if self._runlog is None:
            return
        redact = any(m.privacy_tier == "B" for m in chosen)
        self._runlog.record(entry, redact=redact)

    def _counts(self) -> dict[str, tuple[int, int]]:
        return self._store.counts() if self._store is not None else {}

    def usage_summary(self) -> list[dict[str, object]]:
        return usage.summary(self._members, self._counts(), self._health)

    def health_probed_at(self) -> str | None:
        return self._health.probed_at() if self._health is not None else None

    # -- caller adaptation ----------------------------------------------

    async def _call_detailed(self, alias: str, prompt: str) -> CallResult:
        res = await self._caller(alias, prompt)
        if isinstance(res, str):  # plain-text caller: it can only report the requested alias
            return CallResult(res, alias)
        return res

    async def _call_plain(self, alias: str, prompt: str) -> str:
        return (await self._detailed(alias, prompt)).content

    # -- health / timeouts ----------------------------------------------

    def _down(self) -> set[str]:
        return self._health.down_aliases() if self._health is not None else set()

    def _pool(self, allowed: list[Member]) -> tuple[list[Member], str | None]:
        """Tier-gated ``allowed`` -> (pool, note): healthy and under cap first.

        Every fallback stays inside ``allowed``, so the privacy gate is never widened.
        """
        down = self._down()
        candidates = usage.available(allowed, self._counts())
        healthy = [m for m in candidates if m.alias not in down]
        if healthy:
            return healthy, None
        if candidates:
            return candidates, ALL_DOWN_NOTE
        # every member is exhausted: existing behaviour -- fall back to the whole gated set
        if any(m.alias in down for m in allowed):
            return allowed, ALL_DOWN_NOTE
        return allowed, None

    def _timeout_for(self, m: Member) -> float:
        return m.timeout or self._call_timeout

    def _agg_timeout(self, chosen: list[Member]) -> float:
        return max((self._timeout_for(m) for m in chosen), default=self._call_timeout)

    def _record(self, answer: MemberAnswer) -> None:
        """Feed one member outcome into the shared health store (no-op without one)."""
        if self._health is None:
            return
        if not answer.ok:
            if answer.permanent:
                self._health.mark_down(
                    answer.alias, answer.detail, ttl_s=health_module.CALL_TTL_S
                )
            return
        # A proxy fallback means the requested alias did not serve; credit the one that did.
        served = answer.served_by or answer.alias
        if served in self._health.down_aliases():
            self._health.mark_up(served)

    async def _call_member(self, member: Member, prompt: str) -> CallResult:
        """One ``ask`` call under the member's own timeout, with health bookkeeping.

        A timeout surfaces as a transient ``MemberCallError("timeout")``; ``ProxyError``
        propagates untouched (no health change).
        """
        try:
            res = await asyncio.wait_for(
                self._detailed(member.alias, prompt), self._timeout_for(member)
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            raise MemberCallError(member.alias, "timeout") from exc
        except MemberCallError as exc:
            self._record(
                MemberAnswer(member.alias, False, None, exc.detail, permanent=exc.permanent)
            )
            raise
        served_by = res.served_alias if res.served_alias != member.alias else None
        self._record(MemberAnswer(member.alias, True, res.content, "ok", served_by=served_by))
        return res

    @staticmethod
    def _served_note(requested: str, served: str) -> str:
        return "" if served == requested else f"; served by {served} (proxy fallback)"

    def usage_history(self, days: int = 7) -> list[dict[str, object]]:
        return self._store.history(days) if self._store is not None else []

    def _classifier_for(self, allowed: list[Member]) -> str:
        # The classifier receives the prompt, so it must satisfy the privacy gate too:
        # honour a pinned classifier only if it survived the tier filter, else pick the
        # strongest "fast" member (ties -> higher rpm) of the allowed set.
        if not allowed:
            raise NoEligibleMember("no members available for the requested sensitivity")
        pin = self._classifier_alias
        if pin is not None and any(m.alias == pin for m in allowed):
            return pin
        return max(allowed, key=lambda m: (m.scores.get("fast", 0), m.rpm)).alias

    async def _classify(
        self, prompt: str, allowed: list[Member], pool: list[Member]
    ) -> tuple[str, str | None, bool]:
        """Classify a prompt -> (capability, note, decided_locally).

        The local heuristic goes first (unless ``local_classify=False``): it makes no
        network and no member call, so it can neither touch the health store nor widen
        the privacy gate, and a confident verdict saves a whole round-trip.

        When the heuristic is unsure, the LLM classifier runs as before: the strongest
        healthy "fast" member of the health-filtered ``pool`` (``allowed`` only if the
        pool is empty), under its own timeout with health bookkeeping; a member failure
        degrades to "general" instead of failing the call. ProxyError propagates.
        """
        if self._local_classify:
            capability, confidence = heuristics.classify_local(prompt)
            if confidence >= heuristics.CONFIDENCE_THRESHOLD:
                return capability, None, True
        alias = self._classifier_for(pool or allowed)
        member = self._by_alias(alias)
        if member is None:  # pragma: no cover - _classifier_for only returns known members
            raise NoEligibleMember(f"classifier {alias} is not a council member")

        async def call(_alias: str, text: str) -> str:
            return (await self._call_member(member, text)).content

        try:
            return await router.classify(prompt, caller=call, classifier_alias=alias), None, False
        except MemberCallError as exc:
            return "general", f"classifier failed ({exc.detail}); defaulting to general", False

    async def ask(
        self, prompt: str, *, model: str | None = None, capability: str | None = None,
        sensitivity: str = "sensitive",
    ) -> AskResult:
        privacy.scan_secrets(prompt)
        allowed = privacy.allowed_members(self._members, sensitivity)
        if model is not None:
            member = self._by_alias(model)
            if member is None or member not in allowed:
                raise PrivacyRefusal(
                    f"model {model} is not available for sensitivity={sensitivity}"
                )
            res = await self._call_member(member, prompt)
            note = "direct" + self._served_note(member.alias, res.served_alias)
            return AskResult(
                answer=res.content, model_used=res.served_alias, capability=None, note=note
            )
        pool, pool_note = self._pool(allowed)
        auto = capability is None
        classify_note: str | None = None
        local = False
        if capability is None:
            capability, classify_note, local = await self._classify(prompt, allowed, pool)
            if classify_note:
                pool, pool_note = self._pool(allowed)  # the classifier may just have gone down
        errors: list[str] = []
        for member in router.rank(pool, capability):
            try:
                res = await self._call_member(member, prompt)
            except MemberCallError as exc:
                errors.append(f"{member.alias}[{exc.detail}]")
                continue
            trail = " -> ".join([*errors, member.alias])
            how = ("auto-routed (local)" if local else "auto-routed") if auto else "routed"
            note = f"{how}: {capability} -> {trail}"
            for extra in (classify_note, pool_note):
                if extra:
                    note += f"; {extra}"
            note += self._served_note(member.alias, res.served_alias)
            return AskResult(
                answer=res.content, model_used=res.served_alias, capability=capability, note=note
            )
        raise AllMembersFailed(f"all '{capability}' members failed: {', '.join(errors)}")

    async def council(
        self, prompt: str, *, members: list[str] | None = None,
        size: int | None = None, mode: str | None = None, sensitivity: str = "sensitive",
        on_progress: Callable[[dict[str, object]], None] | None = None,
    ) -> CouncilResult:
        privacy.scan_secrets(prompt)
        allowed = privacy.allowed_members(self._members, sensitivity)
        if not allowed:
            raise NoEligibleMember("no members available for the requested sensitivity")
        pool, pool_note = self._pool(allowed)
        notes: list[str] = [pool_note] if pool_note else []

        if members is not None:
            down = self._down()
            allowed_by_alias = {m.alias for m in allowed}
            pool_by_alias = {m.alias: m for m in pool}
            roster: list[Member] = []
            for alias in members:
                known = self._by_alias(alias)
                if alias in pool_by_alias:
                    roster.append(pool_by_alias[alias])
                elif known is None:
                    notes.append(f"dropped {alias} (unknown)")
                elif alias not in allowed_by_alias:
                    tier = known.privacy_tier
                    notes.append(f"dropped {alias} (tier {tier} blocked on {sensitivity})")
                elif alias in down and self._health is not None:
                    reason = self._health.status_of(alias)[1]
                    notes.append(f"dropped {alias} (down: {reason})")
                else:
                    notes.append(f"dropped {alias} (exhausted)")
            if roster:
                chosen = roster
            else:
                notes.append("manual roster empty after gate; auto-composed")
                chosen = await self._auto_roster(prompt, allowed, pool, size, notes)
        else:
            chosen = await self._auto_roster(prompt, allowed, pool, size, notes)

        if on_progress is not None:
            on_progress({"event": "roster", "members": [m.alias for m in chosen]})
        on_member = (
            (lambda alias, ok: on_progress({"event": "member", "alias": alias, "ok": ok}))
            if on_progress is not None else None
        )
        answers = await fanout.fan_out(
            prompt, chosen, self._detailed, timeout=self._call_timeout,
            **({"on_member": on_member} if on_progress is not None else {}),
        )
        for answer in answers:
            self._record(answer)
        served = [f"{a.alias}->{a.served_by}" for a in answers if a.ok and a.served_by]
        if served:
            notes.append(f"fallback-served: {', '.join(served)}")
        if on_progress is not None:
            on_progress({"event": "aggregating"})
        result = await agg.aggregate(
            prompt, answers, caller=self._plain,
            judge_aliases=self._judge_order(chosen), mode=mode,
            timeout=self._agg_timeout(chosen),
        )
        self._log_council(
            {
                "tool": "council",
                "sensitivity": sensitivity,
                "mode": result.mode,
                "confidence": result.confidence,
                "judge_used": result.judge_used,
                "disagreements": result.disagreements,
                "note": "; ".join(notes),
                "roster": [a.alias for a in answers],
                "per_member": [
                    {
                        "alias": a.alias,
                        "ok": a.ok,
                        "tier": self._tier_of(a.alias),
                        "answer": a.answer,
                        "served_by": a.served_by,
                    }
                    for a in answers
                ],
                "prompt": prompt,
                "answer": result.answer,
            },
            chosen,
        )
        return CouncilResult(
            answer=result.answer, per_member=answers, disagreements=result.disagreements,
            judge_used=result.judge_used, mode=result.mode, note="; ".join(notes),
            confidence=result.confidence,
        )

    async def _auto_roster(
        self, prompt: str, allowed: list[Member], pool: list[Member],
        size: int | None, notes: list[str],
    ) -> list[Member]:
        # council()'s notes do not distinguish the two classify paths (no "auto-routed"
        # prefix there), so the local flag is irrelevant to its note assembly.
        capability, classify_note, _local = await self._classify(prompt, allowed, pool)
        if classify_note:
            notes.append(classify_note)
            pool, pool_note = self._pool(allowed)  # the classifier may just have gone down
            if pool_note and pool_note not in notes:
                notes.append(pool_note)
        k = size if size is not None else compose.adaptive_k(capability)
        chosen = compose.compose_council(pool, k=k, capability=capability)
        notes.append(f"auto: {capability}, k={len(chosen)}")
        return chosen

    def _judge_order(self, chosen: list[Member]) -> list[str]:
        # Judges come only from the already-tier-filtered chosen members: strongest
        # "reasoning" member first (ties -> overall strength, then rpm). A pinned chair
        # leads only when it survived the gate and is part of this council.
        ordered = [
            m.alias
            for m in sorted(
                chosen,
                key=lambda m: (m.scores.get("reasoning", 0), m.strength, m.rpm),
                reverse=True,
            )
        ]
        pin = self._chair_alias
        if pin is not None and pin in ordered:
            ordered.remove(pin)
            ordered.insert(0, pin)
        return ordered


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH, *, base_url: str = DEFAULT_BASE_URL,
    api_key: str, local_classify: bool = True,
) -> Orchestrator:
    members = registry.load_members(config_path, available_keys=registry.available_env_keys())
    store = usage.UsageStore()
    resolve = functools.partial(registry.served_alias, members, env=registry.env_values())
    caller = client.make_caller(base_url, api_key, recorder=store.record, resolve=resolve)
    return Orchestrator(
        members, caller, store=store, runlog=runlog_module.RunLog(),
        health=health_module.HealthStore(), local_classify=local_classify,
    )
