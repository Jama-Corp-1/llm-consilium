from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Any

import yaml

from council.types import Member

_ENV_REF = re.compile(r"os\.environ/([A-Za-z_][A-Za-z0-9_]*)")
DEFAULT_ENV_FILE = Path.home() / ".config" / "consilium" / ".env"


def available_env_keys(env_file: str | Path = DEFAULT_ENV_FILE) -> set[str]:
    keys = {k for k, v in os.environ.items() if v}
    path = Path(env_file)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if value.strip():
                keys.add(name.strip())
    return keys


def _required_env(params: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for value in params.values():
        if isinstance(value, str):
            names.update(_ENV_REF.findall(value))
    return names


def _derive_family(model: str) -> str:
    if model.startswith("openai/@cf/"):  # Cloudflare Workers AI shim
        return "cloudflare"
    return model.split("/", 1)[0] if "/" in model else model


def _scores(info: dict[str, Any]) -> dict[str, int]:
    raw = info.get("scores")
    if isinstance(raw, dict) and raw:
        return {str(k): int(v) for k, v in raw.items()}
    # Legacy fallback: flat strength + capabilities list.
    strength = int(info.get("strength", 1))
    caps = info.get("capabilities", ["general"])
    return {str(c): strength for c in caps}


def load_members(
    config_path: str | Path, *, available_keys: set[str] | None = None
) -> list[Member]:
    data = yaml.safe_load(Path(config_path).read_text())
    members: list[Member] = []
    for entry in data.get("model_list", []):
        info = entry.get("model_info") or {}
        params = entry.get("litellm_params") or {}
        if available_keys is not None and not _required_env(params) <= available_keys:
            continue  # key-presence activation: a member with a missing key is dormant
        rpd = info.get("rpd")
        tpd = info.get("tpd")
        family = info.get("provider_family") or _derive_family(str(params.get("model", "")))
        members.append(
            Member(
                alias=entry["model_name"],
                privacy_tier=info.get("privacy_tier", "B"),
                scores=_scores(info),
                rpm=int(params.get("rpm", 10)),
                provider_family=family,
                rpd=int(rpd) if rpd is not None else None,
                tpd=int(tpd) if tpd is not None else None,
                cost_per_1k=float(info.get("cost_per_1k", 0.0)),
            )
        )
    return members
