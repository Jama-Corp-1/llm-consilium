from pathlib import Path

import pytest
import yaml

CONFIG_PATH = Path(__file__).resolve().parents[1] / "proxy" / "config.yaml"

# Expected tier per alias. Pinning the whole map makes mistagging a training
# provider (Tier B) as Tier A a hard test failure — a privacy regression guard.
EXPECTED_TIER = {
    "council/groq-gpt-oss-120b": "A",
    "council/groq-gpt-oss-20b": "A",
    "council/groq-qwen3.8-27b": "A",
    "council/cloudflare-gpt-oss-120b": "A",
    "council/cloudflare-qwen3.8-27b": "A",
    "council/cloudflare-glm-4.7-flash": "A",
    "council/cloudflare-llama-70b": "A",
    "council/cloudflare-llama-4-scout": "A",
    "council/cloudflare-qwen-coder-32b": "A",
    "council/cloudflare-nemotron-120b": "A",
    "council/mistral-large-4": "B",
    "council/mistral-codestral": "B",
    "council/nvidia-deepseek-r1": "B",
    "council/nvidia-llama-70b": "B",
}
EXPECTED_ALIASES = set(EXPECTED_TIER)

# Tier-A safety contract: a Tier-A alias must route through a Tier-A provider
# prefix, so a Tier-A tag can never silently route through a training provider.
# `openai/` is Tier-A ONLY because api_base pins the Cloudflare Workers AI shim —
# enforced by test_openai_shim_aliases_pin_api_base.
TIER_A_MODEL_PREFIXES = ("groq/", "openai/")


def _load():
    return yaml.safe_load(CONFIG_PATH.read_text())


def test_expected_aliases_present():
    cfg = _load()
    names = {m["model_name"] for m in cfg["model_list"]}
    assert names == EXPECTED_ALIASES


def test_aliases_tagged_expected_tier():
    cfg = _load()
    for m in cfg["model_list"]:
        assert m["model_info"]["privacy_tier"] == EXPECTED_TIER[m["model_name"]], m["model_name"]


def test_no_literal_secrets():
    cfg = _load()
    for m in cfg["model_list"]:
        params = m["litellm_params"]
        assert params["api_key"].startswith("os.environ/"), m["model_name"]
        if "api_base" in params:
            assert params["api_base"].startswith("os.environ/"), m["model_name"]


def test_master_key_via_env():
    cfg = _load()
    assert cfg["general_settings"]["master_key"] == "os.environ/LITELLM_MASTER_KEY"


def test_tier_a_aliases_use_tier_a_provider_prefixes():
    cfg = _load()
    for m in cfg["model_list"]:
        if m["model_info"]["privacy_tier"] == "A":
            model = m["litellm_params"]["model"]
            assert model.startswith(TIER_A_MODEL_PREFIXES), m["model_name"]


def test_openai_shim_aliases_pin_api_base():
    cfg = _load()
    for m in cfg["model_list"]:
        params = m["litellm_params"]
        if params["model"].startswith("openai/"):
            assert "api_base" in params, m["model_name"]
            assert params["api_base"].startswith("os.environ/"), m["model_name"]


def test_fallbacks_stay_within_tier():
    cfg = _load()
    tier = {m["model_name"]: m["model_info"]["privacy_tier"] for m in cfg["model_list"]}
    for entry in cfg["router_settings"]["fallbacks"]:
        for src, targets in entry.items():
            assert src in tier, src
            for dst in targets:
                assert dst in tier, dst
                assert tier[dst] == tier[src], f"{src} -> {dst} crosses tiers"


def test_every_member_declares_provider_family():
    cfg = _load()
    for m in cfg["model_list"]:
        assert m["model_info"].get("provider_family"), m["model_name"]


def test_every_env_ref_documented_in_env_example():
    cfg = _load()
    refs = set()
    for m in cfg["model_list"]:
        for v in m["litellm_params"].values():
            if isinstance(v, str) and v.startswith("os.environ/"):
                refs.add(v.split("/", 1)[1])
    example = (CONFIG_PATH.parents[1] / ".env.example").read_text()
    for name in refs:
        assert f"{name}=" in example, name   # commented-out lines count


@pytest.mark.xfail(strict=True, reason="removed in Task 5")
def test_no_proxy_level_fallbacks():
    # The council owns failover (spec decision 1).
    assert "fallbacks" not in _load()["router_settings"]
