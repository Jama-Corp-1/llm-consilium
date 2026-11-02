"""`python -m consilium health`: live-probe every active member and record it in the store.

Each member gets one 1-token completion through the proxy. Only a permanent provider
failure (dead key, removed model, ...) marks a member down; 429 / 5xx / timeouts are
"unknown" and never change its up/down state.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

from consilium import env_file, paths
from council import client, orchestrator, registry
from council.health import PROBE_TTL_S, HealthStore
from council.types import Member

Echo = Callable[[str], object]
Sleep = Callable[[float], Awaitable[None]]

_SLOW_RPM = 2  # vendors at or below this rpm get a 1/rpm gap between probes
_PRECHECK_TIMEOUT_S = 10.0


@dataclass(frozen=True)
class ProbeOutcome:
    alias: str
    tier: str
    status: str  # "up" | "down" | "unknown"
    reason: str
    ms: int


def classify_probe(status_code: int | None, body: str, error: str | None) -> tuple[str, str]:
    if error is not None:
        return ("unknown", error)
    if status_code == 200:
        return ("up", "")
    verdict = client.classify_error(status_code or 0, body)
    if verdict is None:
        return ("unknown", "proxy auth")
    detail, permanent = verdict
    return ("down", detail) if permanent else ("unknown", detail)


async def _probe_one(
    http: httpx.AsyncClient, m: Member, default_timeout: float
) -> ProbeOutcome:
    payload = {
        "model": m.alias,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    status_code: int | None = None
    body = ""
    error: str | None = None
    start = time.monotonic()
    try:
        resp = await asyncio.wait_for(
            http.post("/chat/completions", json=payload), m.timeout or default_timeout
        )
        status_code, body = resp.status_code, resp.text
    except (asyncio.TimeoutError, httpx.TimeoutException):
        error = "timeout"
    except httpx.HTTPError as exc:
        error = type(exc).__name__
    except Exception as exc:  # noqa: BLE001 - one bad probe must never abort the run
        error = type(exc).__name__
    ms = int((time.monotonic() - start) * 1000)
    status, reason = classify_probe(status_code, body, error)
    return ProbeOutcome(m.alias, m.privacy_tier, status, reason, ms)


async def probe_members(
    members: list[Member],
    base_url: str,
    api_key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    default_timeout: float = 30.0,
    sleep: Sleep = asyncio.sleep,
) -> list[ProbeOutcome]:
    """Probe all members: vendors concurrently, members of one vendor sequentially."""
    by_vendor: dict[str, list[Member]] = {}
    for m in members:
        by_vendor.setdefault(m.vendor, []).append(m)

    # Per-member deadlines come from asyncio.wait_for; the client timeout is only a backstop.
    backstop = max([default_timeout, *(m.timeout or 0.0 for m in members)]) + 5.0
    async with httpx.AsyncClient(
        base_url=base_url,
        headers={"Authorization": f"Bearer {api_key}"},
        timeout=backstop,
        transport=transport,
    ) as http:

        async def run_vendor(group: list[Member]) -> list[ProbeOutcome]:
            results: list[ProbeOutcome] = []
            for i, m in enumerate(group):
                if i > 0 and m.rpm <= _SLOW_RPM:
                    await sleep(1 / max(m.rpm, 1))
                results.append(await _probe_one(http, m, default_timeout))
            return results

        grouped = await asyncio.gather(*(run_vendor(g) for g in by_vendor.values()))

    by_alias = {o.alias: o for group in grouped for o in group}
    return [by_alias[m.alias] for m in members]


def apply(outcomes: list[ProbeOutcome], store: HealthStore) -> None:
    for o in outcomes:
        if o.status == "up":
            store.mark_up(o.alias, source="probe")
        elif o.status == "down":
            store.mark_down(o.alias, o.reason, ttl_s=PROBE_TTL_S, source="probe")
        store.note_probe(o.alias, o.status, o.reason)


def render(outcomes: list[ProbeOutcome], probed_at: str | None) -> str:
    width = max([len("ALIAS"), *(len(o.alias) for o in outcomes)])
    lines = [f"Health (probed {probed_at or 'never'}):"]
    lines.append(f"  {'ALIAS':<{width}}  TIER  {'STATUS':<7}  {'MS':>6}  REASON")
    for o in outcomes:
        lines.append(
            f"  {o.alias:<{width}}  {o.tier:<4}  {o.status:<7}  {o.ms:>6}  {o.reason}".rstrip()
        )
    counts = {s: sum(o.status == s for o in outcomes) for s in ("up", "down", "unknown")}
    lines.append(f"{counts['up']} up, {counts['down']} down, {counts['unknown']} unknown")
    return "\n".join(lines)


def precheck(
    base_url: str,
    api_key: str,
    *,
    transport: httpx.BaseTransport | None = None,
    echo: Echo = print,
) -> int | None:
    """GET /models: None to proceed, 2 if the proxy is unreachable or rejects the key."""
    try:
        with httpx.Client(
            base_url=base_url,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=_PRECHECK_TIMEOUT_S,
            transport=transport,
        ) as http:
            resp = http.get("/models")
    except httpx.HTTPError:
        echo(f"ERROR: proxy unreachable at {base_url}")
        return 2
    if resp.status_code in (401, 403):
        echo(f"ERROR: proxy rejected the master key (HTTP {resp.status_code})")
        return 2
    return None  # 200 or anything else: let the per-member probes speak


def health(
    argv: list[str],
    *,
    base_url: str = orchestrator.DEFAULT_BASE_URL,
    api_key: str | None = None,
    store: HealthStore | None = None,
    members: list[Member] | None = None,
    transport: httpx.MockTransport | None = None,  # test seam; serves both clients
    echo: Echo = print,
) -> int:
    """CLI entry. Exit 2: no key / proxy unusable; 1: a member is down; 0 otherwise."""
    key = api_key if api_key is not None else env_file.master_key()
    if key is None:
        echo("ERROR: LITELLM_MASTER_KEY not found")
        return 2
    failed = precheck(base_url, key, transport=transport, echo=echo)
    if failed is not None:
        return failed
    if members is None:
        members = registry.load_members(
            paths.CONFIG_PATH, available_keys=registry.available_env_keys()
        )
    outcomes = asyncio.run(probe_members(members, base_url, key, transport=transport))
    store = store if store is not None else HealthStore()
    apply(outcomes, store)
    if "--json" in argv:
        echo(json.dumps(store.snapshot(), indent=2, sort_keys=True))
    else:
        echo(render(outcomes, store.probed_at()))
    return 1 if any(o.status == "down" for o in outcomes) else 0
