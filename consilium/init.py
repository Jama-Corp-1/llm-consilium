from __future__ import annotations

import getpass
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import httpx

from consilium import env_file, providers
from consilium.providers import VENDOR_BASE_URLS, Provider


@dataclass(frozen=True)
class PingResult:
    ok: bool
    detail: str


def mask(value: str) -> str:
    if not value:
        return "not set"
    return f"set (...{value[-4:]})" if len(value) >= 4 else "set (...)"


def _key_var(provider: Provider) -> str:
    """The credential var: first ``*_API_KEY`` / ``*_API_TOKEN`` in ``env_vars``.

    ``env_vars`` is sorted, so position alone is unreliable (cloudflare lists
    ``CLOUDFLARE_API_BASE`` before its token). Returns "" when none matches.
    """
    return next(
        (v for v in provider.env_vars if v.endswith(("_API_KEY", "_API_TOKEN"))), ""
    )


def live_ping(
    provider: Provider, env: dict[str, str], *, client: httpx.Client | None = None
) -> PingResult:
    key = env.get(_key_var(provider), "")
    if not key:
        return PingResult(False, "no key")
    if provider.key == "cloudflare":
        base = env.get("CLOUDFLARE_API_BASE", "")
    else:
        base = VENDOR_BASE_URLS.get(provider.key, "")
    if not base:
        return PingResult(False, "no base url")
    owns_client = client is None
    client = client or httpx.Client(timeout=15.0)
    try:
        resp = client.post(
            f"{base.rstrip('/')}/chat/completions",
            json={"model": provider.probe_model_id,
                  "messages": [{"role": "user", "content": "ping"}], "max_tokens": 1},
            headers={"Authorization": f"Bearer {key}"},
        )
    except Exception as exc:  # noqa: BLE001 - a ping is best-effort readiness; never abort init
        return PingResult(False, f"unreachable: {exc.__class__.__name__}")
    finally:
        if owns_client:
            client.close()
    if resp.status_code // 100 == 2:
        return PingResult(True, "ok")
    return PingResult(False, f"HTTP {resp.status_code}")


def run(
    *,
    env_path: str | Path = env_file.DEFAULT_ENV_PATH,
    prompt: Callable[[str], str] = getpass.getpass,
    echo: Callable[[str], object] = print,
    ping: Callable[[Provider, dict[str, str]], PingResult] = live_ping,
) -> int:
    provs = providers.providers()
    existing = env_file.load(env_path)
    collected: dict[str, str] = {}
    for provider in provs:
        echo(f"\n{provider.name}  [Tier {provider.tier}]  - {provider.signup}")
        for var in provider.env_vars:
            entered = prompt(f"  {var} [{mask(existing.get(var, ''))}] (Enter to keep): ").strip()
            if entered:
                collected[var] = entered
    merged = {**existing, **collected}
    if not merged.get("LITELLM_MASTER_KEY"):
        merged["LITELLM_MASTER_KEY"] = f"sk-{secrets.token_hex(24)}"
    env_file.write(env_path, merged)
    echo(f"\nWrote {env_path}")
    echo("\nReadiness:")
    for provider in provs:
        if all(merged.get(v) for v in provider.env_vars):
            res = ping(provider, merged)
            echo(f"  {'green' if res.ok else 'red'} {provider.name}: {res.detail}")
        else:
            echo(f"  dormant {provider.name}: no key")
    return 0
