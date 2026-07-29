from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from consilium import env_file, paths
from consilium.providers import PROVIDERS
from consilium.service import port_open as _default_port_open

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]
Echo = Callable[[str], object]
PortOpen = Callable[..., bool]


def doctor(*, port_open: PortOpen = _default_port_open, runner: Runner = subprocess.run,
           env_path: str | Path = env_file.DEFAULT_ENV_PATH, echo: Echo = print) -> int:
    env = env_file.load(env_path)
    echo("Providers:")
    for provider in PROVIDERS:
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
    return 0
