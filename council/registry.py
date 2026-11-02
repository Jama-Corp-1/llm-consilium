from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import yaml

from council.types import Member

_ENV_REF = re.compile(r"os\.environ/([A-Za-z_][A-Za-z0-9_]*)")
DEFAULT_ENV_FILE = Path.home() / ".config" / "consilium" / ".env"


@dataclass(frozen=True)
class ProviderSpec:
    key: str  # vendor
    tier: str
    env_vars: tuple[str, ...]  # sorted union of members' required_env
    probe_alias: str  # cheapest-to-probe member: highest fast score, then rpm, then alias
    probe_model_id: str  # strip_provider_prefix(probe member's model_id)
    members: tuple[str, ...]


def _env_file_pairs(env_file: str | Path) -> dict[str, str]:
    pairs: dict[str, str] = {}
    path = Path(env_file)
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            name, _, value = line.partition("=")
            if value.strip():
                pairs[name.strip()] = value.strip()
    return pairs


def env_values(env_file: str | Path = DEFAULT_ENV_FILE) -> dict[str, str]:
    """Env-file key=value pairs, overlaid by non-empty process environment entries."""
    values = _env_file_pairs(env_file)
    values.update({k: v for k, v in os.environ.items() if v})
    return values


def available_env_keys(env_file: str | Path = DEFAULT_ENV_FILE) -> set[str]:
    return set(env_values(env_file))


def _required_env(params: dict[str, Any]) -> set[str]:
    names: set[str] = set()
    for value in params.values():
        if isinstance(value, str):
            names.update(_ENV_REF.findall(value))
    return names


def vendor_of(model_id: str) -> str:
    """Inference vendor of a LiteLLM model id (its provider prefix)."""
    if model_id.startswith("openai/@cf/"):  # Cloudflare Workers AI shim
        return "cloudflare"
    return model_id.split("/", 1)[0] if "/" in model_id else model_id


def strip_provider_prefix(model_id: str) -> str:
    """Model name as the upstream vendor sees it: "groq/openai/x" -> "openai/x"."""
    return model_id.split("/", 1)[1] if "/" in model_id else model_id


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
        model_id = str(params.get("model", ""))
        vendor = vendor_of(model_id)
        family = info.get("provider_family") or vendor
        api_base = params.get("api_base")
        timeout = info.get("timeout")
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
                model_id=model_id,
                vendor=vendor,
                api_base_ref=str(api_base) if api_base is not None else None,
                required_env=frozenset(_required_env(params)),
                timeout=float(timeout) if timeout is not None else None,
            )
        )
    return members


def providers(members: list[Member]) -> list[ProviderSpec]:
    """One ProviderSpec per vendor, in order of first appearance.

    Raises ValueError when a vendor's members disagree on privacy tier.
    """
    by_vendor: dict[str, list[Member]] = {}
    for m in members:
        by_vendor.setdefault(m.vendor, []).append(m)
    specs: list[ProviderSpec] = []
    for vendor, group in by_vendor.items():
        tiers = sorted({m.privacy_tier for m in group})
        if len(tiers) > 1:
            raise ValueError(f"vendor {vendor} mixes tiers {'/'.join(tiers)}")
        probe = min(group, key=lambda m: (-m.scores.get("fast", 0), -m.rpm, m.alias))
        specs.append(
            ProviderSpec(
                key=vendor,
                tier=tiers[0],
                env_vars=tuple(sorted(set().union(*(m.required_env for m in group)))),
                probe_alias=probe.alias,
                probe_model_id=strip_provider_prefix(probe.model_id),
                members=tuple(m.alias for m in group),
            )
        )
    return specs


def _resolve_ref(ref: str, env: Mapping[str, str]) -> str | None:
    match = _ENV_REF.fullmatch(ref)
    if match:
        return env.get(match.group(1))
    return ref


def served_alias(
    members: list[Member],
    requested: str,
    body_model: str | None,
    api_base: str | None,
    env: Mapping[str, str] | None = None,
) -> str:
    """Alias that actually served a response, inferred from its body model / api_base.

    Falls back to ``requested`` when nothing identifies a member. Never raises.
    """
    if not body_model:
        return requested
    if any(m.alias == body_model for m in members):
        return body_model
    candidates = [m for m in members if strip_provider_prefix(m.model_id) == body_model]
    if len(candidates) == 1:
        return candidates[0].alias
    if not candidates or not api_base:
        return requested
    environ: Mapping[str, str] = os.environ if env is None else env
    wanted = api_base.rstrip("/")
    for m in candidates:
        if m.api_base_ref is None:
            continue
        resolved = _resolve_ref(m.api_base_ref, environ)
        if resolved and resolved.rstrip("/") == wanted:
            return m.alias
    host = (urlparse(api_base).hostname or api_base).lower()
    for m in candidates:
        if m.vendor and m.vendor.lower() in host:
            return m.alias
    return requested
