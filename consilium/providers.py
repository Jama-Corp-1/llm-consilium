"""Provider metadata for the setup CLI and the web facade, derived from proxy/config.yaml.

The config is the single source of truth for which vendors exist, their privacy tier,
their env vars and their probe model. Only presentation hints (display name, signup
text, readiness-ping base URL) live here, keyed by vendor (the LiteLLM model prefix).
Hint entries for vendors without config members (cerebras, sambanova) are harmless.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from consilium import paths
from council import registry


@dataclass(frozen=True)
class Provider:
    key: str  # vendor (LiteLLM prefix), e.g. "groq", "nvidia_nim"
    name: str
    tier: str
    env_vars: tuple[str, ...]  # sorted union of the vendor's required env names
    signup: str
    probe_alias: str
    probe_model_id: str  # model name as the vendor API sees it


DISPLAY_NAMES: dict[str, str] = {
    "groq": "Groq",
    "cloudflare": "Cloudflare Workers AI",
    "mistral": "Mistral",
    "nvidia_nim": "NVIDIA NIM",
    "cerebras": "Cerebras",
    "sambanova": "SambaNova",
}

SIGNUP_HINTS: dict[str, str] = {
    "groq": "console.groq.com -> API Keys (free)",
    "cloudflare": (
        "dash.cloudflare.com -> My Profile -> API Tokens -> 'Workers AI' template; "
        "API base https://api.cloudflare.com/client/v4/accounts/<id>/ai/v1"
    ),
    "mistral": "console.mistral.ai -> API Keys (free; trains on prompts -> Tier B)",
    "nvidia_nim": "build.nvidia.com -> API Key (free credits)",
    # Cerebras closed its free tier in 2026 (HTTP 402 without billing).
    "cerebras": "cloud.cerebras.ai -> API Keys (paid plan required since 2026; optional)",
    "sambanova": "cloud.sambanova.ai -> API Keys (free)",
}

# OpenAI-compatible base used by the readiness ping. Cloudflare's base is per-account and
# comes from CLOUDFLARE_API_BASE in the env at ping time.
VENDOR_BASE_URLS: dict[str, str] = {
    "groq": "https://api.groq.com/openai/v1",
    "mistral": "https://api.mistral.ai/v1",
    "nvidia_nim": "https://integrate.api.nvidia.com/v1",
    "cerebras": "https://api.cerebras.ai/v1",
    "sambanova": "https://api.sambanova.ai/v1",
    "cloudflare": "",
}


def providers(config_path: str | Path = paths.CONFIG_PATH) -> tuple[Provider, ...]:
    """One Provider per config vendor, in order of first appearance (no key filter)."""
    return tuple(
        Provider(
            key=spec.key,
            name=DISPLAY_NAMES.get(spec.key, spec.key),
            tier=spec.tier,
            env_vars=spec.env_vars,
            signup=SIGNUP_HINTS.get(spec.key, ""),
            probe_alias=spec.probe_alias,
            probe_model_id=spec.probe_model_id,
        )
        for spec in registry.providers(registry.load_members(config_path))
    )
