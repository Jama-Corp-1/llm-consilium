from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from council import aggregate as agg
from council import client, compose, fanout, privacy, registry, router, usage
from council import runlog as runlog_module
from council.errors import AllMembersFailed, MemberCallError, NoEligibleMember, PrivacyRefusal
from council.types import AskResult, AsyncCaller, CouncilResult, Member

DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"
# Absolute so the user-scope MCP server finds the config no matter which project
# directory Claude Code spawned it from (a relative path breaks cross-project use).
DEFAULT_CONFIG_PATH = str(Path(__file__).resolve().parents[1] / "proxy" / "config.yaml")
CLASSIFIER_ALIAS = "council/groq-llama-70b"  # must be Tier-A; used only if within the allowed set
# must be Tier-A; used as judge only if within the chosen set
CHAIR_ALIAS = "council/cerebras-glm-4.7"


class Orchestrator:
    def __init__(
        self,
        members: list[Member],
        caller: AsyncCaller,
        *,
        classifier_alias: str = CLASSIFIER_ALIAS,
        chair_alias: str = CHAIR_ALIAS,
        store: usage.UsageStore | None = None,
        runlog: runlog_module.RunLog | None = None,
        call_timeout: float = 30.0,
    ) -> None:
        self._members = members
        self._caller = caller
        self._classifier_alias = classifier_alias
        self._chair_alias = chair_alias
        self._store = store
        self._runlog = runlog
        self._call_timeout = call_timeout

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
        return usage.summary(self._members, self._counts())

    def usage_history(self, days: int = 7) -> list[dict[str, object]]:
        return self._store.history(days) if self._store is not None else []

    def _classifier_for(self, allowed: list[Member]) -> str:
        # The classifier receives the prompt, so it must satisfy the privacy gate too:
        # use the configured classifier only if it survived the tier filter, else the
        # fastest allowed member.
        if not allowed:
            raise NoEligibleMember("no members available for the requested sensitivity")
        if any(m.alias == self._classifier_alias for m in allowed):
            return self._classifier_alias
        return max(allowed, key=lambda m: m.rpm).alias

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
            answer = await self._caller(member.alias, prompt)
            return AskResult(answer=answer, model_used=member.alias, capability=None, note="direct")
        pool = usage.available(allowed, self._counts()) or allowed
        auto = capability is None
        if capability is None:
            capability = await router.classify(
                prompt, caller=self._caller, classifier_alias=self._classifier_for(allowed)
            )
        errors: list[str] = []
        for member in router.rank(pool, capability):
            try:
                answer = await self._caller(member.alias, prompt)
            except MemberCallError as exc:
                errors.append(f"{member.alias}[{exc.detail}]")
                continue
            trail = " -> ".join([*errors, member.alias])
            note = f"{'auto-routed' if auto else 'routed'}: {capability} -> {trail}"
            return AskResult(
                answer=answer, model_used=member.alias, capability=capability, note=note
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
        pool = usage.available(allowed, self._counts()) or allowed
        notes: list[str] = []

        if members is not None:
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
            prompt, chosen, self._caller, timeout=self._call_timeout,
            **({"on_member": on_member} if on_progress is not None else {}),
        )
        if on_progress is not None:
            on_progress({"event": "aggregating"})
        result = await agg.aggregate(
            prompt, answers, caller=self._caller,
            judge_aliases=self._judge_order(chosen), mode=mode, timeout=self._call_timeout,
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
        capability = await router.classify(
            prompt, caller=self._caller, classifier_alias=self._classifier_for(allowed)
        )
        k = size if size is not None else compose.adaptive_k(capability)
        chosen = compose.compose_council(pool, k=k, capability=capability)
        notes.append(f"auto: {capability}, k={len(chosen)}")
        return chosen

    def _judge_order(self, chosen: list[Member]) -> list[str]:
        # Judges come only from the already-tier-filtered chosen members, so the chair
        # is used only when it survived the gate and is part of this council.
        ordered = [m.alias for m in sorted(chosen, key=lambda m: m.strength, reverse=True)]
        if self._chair_alias in ordered:
            ordered.remove(self._chair_alias)
            ordered.insert(0, self._chair_alias)
        return ordered


def build(
    config_path: str | Path = DEFAULT_CONFIG_PATH, *, base_url: str = DEFAULT_BASE_URL, api_key: str
) -> Orchestrator:
    members = registry.load_members(config_path, available_keys=registry.available_env_keys())
    store = usage.UsageStore()
    caller = client.make_caller(base_url, api_key, recorder=store.record)
    return Orchestrator(members, caller, store=store, runlog=runlog_module.RunLog())
