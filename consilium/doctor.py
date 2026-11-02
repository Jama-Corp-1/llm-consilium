from __future__ import annotations

import asyncio
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from consilium import env_file, health_cmd, paths, providers
from consilium.service import port_open as _default_port_open
from council import orchestrator, registry
from council.health import HealthStore

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]
Echo = Callable[[str], object]
PortOpen = Callable[..., bool]
Probe = Callable[[], list[health_cmd.ProbeOutcome]]


def _live_probe(api_key: str, env_path: str | Path) -> list[health_cmd.ProbeOutcome]:
    members = registry.load_members(
        paths.CONFIG_PATH, available_keys=registry.available_env_keys(env_path)
    )
    return asyncio.run(
        health_cmd.probe_members(members, orchestrator.DEFAULT_BASE_URL, api_key)
    )


def doctor(*, port_open: PortOpen = _default_port_open, runner: Runner = subprocess.run,
           env_path: str | Path = env_file.DEFAULT_ENV_PATH, echo: Echo = print,
           probe: Probe | None = None, store: HealthStore | None = None) -> int:
    env = env_file.load(env_path)
    echo("Providers:")
    for provider in providers.providers():
        ready = all(env.get(v) for v in provider.env_vars)
        echo(f"  {'READY  ' if ready else 'dormant'} {provider.name} [Tier {provider.tier}]")
    up = port_open(paths.PROXY_HOST, paths.PROXY_PORT)
    echo(f"Proxy {paths.PROXY_HOST}:{paths.PROXY_PORT}: {'up' if up else 'DOWN'}")
    try:
        result = runner(["claude", "mcp", "list"], capture_output=True, text=True)
        registered = "consilium" in (result.stdout or "")
    except Exception:  # noqa: BLE001 - doctor is best-effort diagnostics; never crash
        registered = False
    echo(f"MCP consilium registered: {'yes' if registered else 'no'}")
    key = env_file.master_key(env_path)
    if not up or key is None:
        echo("Health: skipped (proxy down or no master key)")
        return 0
    try:
        outcomes = probe() if probe is not None else _live_probe(key, env_path)
        store = store if store is not None else HealthStore()
        health_cmd.apply(outcomes, store)
    except Exception as exc:  # noqa: BLE001 - doctor is best-effort diagnostics; never crash
        echo(f"Health: error ({type(exc).__name__})")
        return 0
    echo(health_cmd.render(outcomes, store.probed_at()))
    return 0
