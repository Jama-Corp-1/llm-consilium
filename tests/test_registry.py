from pathlib import Path

from council import registry

CONFIG = Path(__file__).resolve().parents[1] / "proxy" / "config.yaml"


def _members():
    return {m.alias: m for m in registry.load_members(CONFIG)}


ALL_ALIASES = {
    "council/groq-gpt-oss-120b", "council/groq-gpt-oss-20b", "council/groq-qwen3.8-27b",
    "council/cloudflare-gpt-oss-120b", "council/cloudflare-qwen3.8-27b",
    "council/cloudflare-glm-4.7-flash", "council/cloudflare-llama-70b",
    "council/cloudflare-llama-4-scout", "council/cloudflare-qwen-coder-32b",
    "council/cloudflare-nemotron-120b",
    "council/mistral-large-4", "council/mistral-codestral",
    "council/nvidia-deepseek-r1", "council/nvidia-llama-70b",
}


def test_loads_all_members():
    assert set(_members()) == ALL_ALIASES


def test_tier_b_providers_tagged_b():
    m = _members()
    for alias in ["council/mistral-large-4", "council/mistral-codestral",
                  "council/nvidia-deepseek-r1"]:
        assert m[alias].privacy_tier == "B"
    assert m["council/cloudflare-gpt-oss-120b"].privacy_tier == "A"


def test_capabilities_and_scores_parsed():
    m = _members()["council/groq-gpt-oss-120b"]
    assert m.privacy_tier == "A"
    assert m.scores["reasoning"] == 4
    assert m.strength == 4
    assert "reasoning" in m.capabilities


def test_provider_family_is_weights_lineage():
    # Explicit provider_family wins over the inference-vendor prefix so auto-composition
    # diversifies model lineages (openai/qwen/meta/...), not just API vendors.
    m = _members()
    assert m["council/groq-gpt-oss-120b"].provider_family == "openai"
    assert m["council/cloudflare-gpt-oss-120b"].provider_family == "openai"
    assert m["council/groq-qwen3.8-27b"].provider_family == "qwen"
    assert m["council/cloudflare-llama-70b"].provider_family == "meta"


def test_provider_family_falls_back_to_vendor_prefix(tmp_path):
    cfg = tmp_path / "nofamily.yaml"
    cfg.write_text(
        "model_list:\n"
        "  - model_name: council/x\n"
        "    litellm_params: {model: groq/x, rpm: 7}\n"
        "    model_info: {privacy_tier: A, scores: {general: 3}}\n"
        "  - model_name: council/y\n"
        "    litellm_params: {model: openai/@cf/meta/y, api_base: os.environ/B}\n"
        "    model_info: {privacy_tier: A, scores: {general: 3}}\n"
    )
    m = {x.alias: x for x in registry.load_members(cfg)}
    assert m["council/x"].provider_family == "groq"
    assert m["council/y"].provider_family == "cloudflare"


def test_legacy_strength_capabilities_synthesized(tmp_path):
    cfg = tmp_path / "legacy.yaml"
    cfg.write_text(
        "model_list:\n"
        "  - model_name: council/legacy\n"
        "    litellm_params: {model: groq/x, rpm: 7}\n"
        "    model_info: {privacy_tier: A, strength: 4, capabilities: [reasoning, code]}\n"
    )
    m = {x.alias: x for x in registry.load_members(cfg)}["council/legacy"]
    assert m.scores == {"reasoning": 4, "code": 4}
    assert m.strength == 4 and m.provider_family == "groq"


def test_rpm_defaults_when_absent():
    # cloudflare alias has no rpm in config -> default 10
    assert _members()["council/cloudflare-llama-70b"].rpm == 10


def test_daily_caps_parsed():
    m = _members()
    assert m["council/groq-gpt-oss-120b"].rpd == 1000
    assert m["council/groq-gpt-oss-120b"].tpd is None
    assert m["council/cloudflare-llama-70b"].tpd == 10000
    assert m["council/cloudflare-llama-70b"].rpd is None
    assert m["council/mistral-codestral"].rpd is None and m["council/mistral-codestral"].tpd is None


def test_available_env_keys_reads_process_and_file(tmp_path, monkeypatch):
    monkeypatch.setenv("FOO_KEY", "x")
    monkeypatch.delenv("BAR_KEY", raising=False)
    env = tmp_path / ".env"
    env.write_text("BAR_KEY=y\nEMPTY_KEY=\n# comment\n")
    keys = registry.available_env_keys(env)
    assert "FOO_KEY" in keys and "BAR_KEY" in keys
    assert "EMPTY_KEY" not in keys


def test_load_members_filters_missing_keys():
    # Only the Groq key "available" -> Cloudflare + all Tier-B drop out.
    keys = {"GROQ_API_KEY"}
    aliases = {m.alias for m in registry.load_members(CONFIG, available_keys=keys)}
    assert "council/groq-gpt-oss-120b" in aliases
    assert "council/groq-qwen3.8-27b" in aliases
    assert "council/cloudflare-llama-70b" not in aliases  # needs CLOUDFLARE_*
    assert "council/mistral-large-4" not in aliases
    assert "council/nvidia-deepseek-r1" not in aliases


def test_load_members_no_filter_returns_all():
    assert len(registry.load_members(CONFIG)) == len(ALL_ALIASES)


def test_cost_per_1k_defaults_zero_for_free_members():
    assert _members()["council/groq-gpt-oss-120b"].cost_per_1k == 0.0


def test_cost_per_1k_parsed_from_model_info(tmp_path):
    cfg = tmp_path / "priced.yaml"
    cfg.write_text(
        "model_list:\n"
        "  - model_name: council/priced\n"
        "    litellm_params: {model: groq/x, rpm: 7}\n"
        "    model_info: {privacy_tier: A, scores: {general: 3}, cost_per_1k: 1.5}\n"
    )
    m = {x.alias: x for x in registry.load_members(cfg)}["council/priced"]
    assert m.cost_per_1k == 1.5
