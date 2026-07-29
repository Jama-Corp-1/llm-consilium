from __future__ import annotations

import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from council import orchestrator as orch  # noqa: E402
from council.types import AskResult, CouncilResult  # noqa: E402

mcp = FastMCP("consilium")
_orch: orch.Orchestrator | None = None


def _load_master_key() -> str:
    key = os.environ.get("LITELLM_MASTER_KEY")
    if key:
        return key
    env_file = Path.home() / ".config" / "consilium" / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith("LITELLM_MASTER_KEY="):
                return line.split("=", 1)[1].strip()
    raise RuntimeError("LITELLM_MASTER_KEY not found in env or ~/.config/consilium/.env")


def _get_orch() -> orch.Orchestrator:
    global _orch
    if _orch is None:
        _orch = orch.build(api_key=_load_master_key())
    return _orch


def _shape_ask(r: AskResult) -> dict[str, object]:
    return {
        "answer": r.answer,
        "model_used": r.model_used,
        "capability": r.capability,
        "note": r.note,
    }


def _shape_council(r: CouncilResult) -> dict[str, object]:
    return {
        "answer": r.answer,
        "mode": r.mode,
        "judge_used": r.judge_used,
        "disagreements": r.disagreements,
        "per_member": [
            {"alias": a.alias, "ok": a.ok, "detail": a.detail, "answer": a.answer}
            for a in r.per_member
        ],
        "note": r.note,
        "confidence": r.confidence,
    }


@mcp.tool()
async def ask(
    prompt: str, model: str | None = None, capability: str | None = None,
    sensitivity: str = "sensitive",
) -> dict[str, object]:
    """Ask ONE best-fit free model for a quick second opinion or cheap bulk step.

    prompt: the question. Strip secrets/credentials first (the gate refuses obvious ones).
    model: pin a specific member alias (e.g. "council/github-gpt-4.1"); omit to auto-route.
    capability: force a strength axis — "reasoning" | "code" | "fast" | "general".
        Omit to auto-classify. Ignored when `model` is set.
    sensitivity: "sensitive" (default, Tier-A no-train providers only) or "public"
        (adds Tier-B providers that may train on the prompt). Use "public" ONLY for
        generic/published questions.
    Returns: {answer, model_used, capability, note}. `note` records routing/fallbacks.
    """
    return _shape_ask(
        await _get_orch().ask(prompt, model=model, capability=capability, sensitivity=sensitivity)
    )


@mcp.tool()
async def council(
    prompt: str, sensitivity: str = "sensitive",
    members: list[str] | None = None, size: int | None = None, mode: str | None = None,
) -> dict[str, object]:
    """Convene the council: fan out to several diverse free models and aggregate.

    Use for high-stakes cross-checks where diverse errors matter (costs more free-tier
    quota than `ask`).

    prompt: the question. Strip secrets/credentials first.
    sensitivity: "sensitive" (default, Tier-A only) or "public" (adds Tier-B). A Tier-B
        member is NEVER contacted on a sensitive prompt, even if named in `members`.
    members: pin an exact roster (list of member aliases). Omit to auto-compose the
        strongest vendor-diverse set for the classified task. Members blocked by the
        privacy gate or exhausted are dropped (see `note`).
    size: council size override (else adaptive 3-5 by task type). Ignored when `members`
        is given (its length wins).
    mode: aggregation strategy — omit for auto (majority vote for closed-form, else chair
        synthesis). "judge" forces chair synthesis; "vote" forces majority; "peer-rank"
        has members rank each other's anonymized answers and returns the winner verbatim
        (self-votes excluded).
        "debate" runs a stance-steered debate — members critique and revise each other's
        anonymized answers under for/against/neutral stances until they converge, then the
        chair synthesizes; strongest for contentious questions, most free-tier calls.
    Returns: {answer, mode, judge_used, disagreements, per_member, note, confidence}.
        `note` records roster decisions (auto capability/size, dropped members,
        fallbacks). `confidence` (high/medium/low) is the chair's confidence the
        synthesized answer is correct.
    """
    return _shape_council(
        await _get_orch().council(
            prompt, sensitivity=sensitivity, members=members, size=size, mode=mode,
        )
    )


@mcp.tool()
async def stats(days: int = 1) -> dict[str, object]:
    """Consilium usage vs daily caps.

    days: 1 (default) returns today's per-member summary plus a total cost line;
        days > 1 additionally returns a per-day history (newest first).
    Returns: {today: [per-member rows incl. cost_usd], total_cost_usd, history?}.
    """
    o = _get_orch()
    today_rows = o.usage_summary()
    total_cost = sum(
        c for r in today_rows if isinstance(c := r.get("cost_usd", 0.0), (int, float))
    )
    out: dict[str, object] = {
        "today": today_rows,
        "total_cost_usd": round(total_cost, 6),
    }
    if days > 1:
        out["history"] = o.usage_history(days)
    return out


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
