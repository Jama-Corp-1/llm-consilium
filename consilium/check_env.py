"""`python -m consilium check-env`: which providers have keys, and is any Tier-A one ready."""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from consilium import env_file, paths, providers
from council import registry

ENV_FILE_VAR = "CONSILIUM_ENV_FILE"


def check_env(
    *,
    env_path: str | Path | None = None,
    config_path: str | Path = paths.CONFIG_PATH,
    echo: Callable[[str], object] = print,
) -> int:
    """Print one READY/dormant line per provider; 0 if a Tier-A provider is ready, else 1.

    Keys come from the env file overlaid by the process environment (run-proxy.sh has
    already sourced the file). ``env_path`` defaults to $CONSILIUM_ENV_FILE, then the
    standard location. Values are never printed.
    """
    path = env_path or os.environ.get(ENV_FILE_VAR) or env_file.DEFAULT_ENV_PATH
    env = registry.env_values(path)
    provs = providers.providers(config_path)
    ready_a: list[str] = []
    for p in provs:
        ready = all(env.get(v) for v in p.env_vars)
        echo(f"{'READY  ' if ready else 'dormant'} {p.name} [Tier {p.tier}]")
        if ready and p.tier == "A":
            ready_a.append(p.name)
    if ready_a:
        echo(f"OK: Tier-A providers ready: {', '.join(ready_a)}")
        return 0
    tier_a = ", ".join(p.name for p in provs if p.tier == "A")
    echo(f"ERROR: no Tier-A provider configured (set keys for one of: {tier_a})")
    return 1
