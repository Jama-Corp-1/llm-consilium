from pathlib import Path

import pytest

from council import registry
from council.types import Member

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


def test_strip_provider_prefix():
    assert registry.strip_provider_prefix("groq/openai/gpt-oss-120b") == "openai/gpt-oss-120b"
    assert registry.strip_provider_prefix("openai/@cf/meta/x") == "@cf/meta/x"
    assert registry.strip_provider_prefix("plain") == "plain"


def test_members_carry_identity_fields():
    m = _members()["council/cloudflare-llama-70b"]
    assert m.model_id == "openai/@cf/meta/llama-3.3-70b-instruct-fp8-fast"
    assert m.vendor == "cloudflare" and m.api_base_ref == "os.environ/CLOUDFLARE_API_BASE"
    assert m.required_env == frozenset({"CLOUDFLARE_API_BASE", "CLOUDFLARE_API_TOKEN"})
    g = _members()["council/groq-gpt-oss-120b"]
    assert g.vendor == "groq" and g.api_base_ref is None
    assert g.required_env == frozenset({"GROQ_API_KEY"})


def test_timeout_parsed_when_present(tmp_path):
    cfg = tmp_path / "t.yaml"
    cfg.write_text("model_list:\n  - model_name: council/t\n    litellm_params: {model: groq/x}\n"
                   "    model_info: {privacy_tier: A, scores: {general: 3}, timeout: 60}\n")
    assert registry.load_members(cfg)[0].timeout == 60.0
    assert _members()["council/groq-gpt-oss-120b"].timeout is None


def test_providers_derived_from_config():
    specs = {p.key: p for p in registry.providers(registry.load_members(CONFIG))}
    assert set(specs) == {"groq", "cloudflare", "mistral", "nvidia_nim"}
    assert specs["groq"].tier == "A" and specs["mistral"].tier == "B"
    assert specs["cloudflare"].env_vars == ("CLOUDFLARE_API_BASE", "CLOUDFLARE_API_TOKEN")
    assert specs["groq"].probe_alias == "council/groq-gpt-oss-20b"       # fast 5
    assert specs["groq"].probe_model_id == "openai/gpt-oss-20b"
    assert "council/groq-qwen3.8-27b" in specs["groq"].members


def test_providers_rejects_mixed_tier_vendor():
    a = Member("council/a", "A", {"general": 3}, 10, model_id="groq/a", vendor="groq",
               required_env=frozenset({"K"}))
    b = Member("council/b", "B", {"general": 3}, 10, model_id="groq/b", vendor="groq",
               required_env=frozenset({"K"}))
    with pytest.raises(ValueError):
        registry.providers([a, b])


def test_served_alias_three_live_shapes():
    ms = registry.load_members(CONFIG)
    gpt = "council/groq-gpt-oss-120b"
    assert registry.served_alias(ms, gpt, gpt, None) == gpt
    assert registry.served_alias(
        ms, gpt, "openai/gpt-oss-120b", "https://api.groq.com/openai/v1") == gpt
    assert registry.served_alias(
        ms, "council/groq-llama-70b", "@cf/meta/llama-3.3-70b-instruct-fp8-fast", None
    ) == "council/cloudflare-llama-70b"
    assert registry.served_alias(ms, "council/x", "nonsense", None) == "council/x"


def test_served_alias_disambiguates_by_api_base():
    a = Member("council/a", "A", {"general": 3}, 10, model_id="openai/x", vendor="cloudflare",
               api_base_ref="os.environ/CF_BASE")
    b = Member("council/b", "A", {"general": 3}, 10, model_id="groq/x", vendor="groq")
    env = {"CF_BASE": "https://cf.example/v1"}
    assert registry.served_alias(
        [a, b], "council/zzz", "x", "https://cf.example/v1", env=env) == "council/a"
    assert registry.served_alias(
        [a, b], "council/zzz", "x", "https://api.groq.com/openai/v1", env=env) == "council/b"


def test_env_values_file_then_process_override(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\n\nNOEQ\nCONS_A = from-file \nCONS_B=file-b\n")
    monkeypatch.setenv("CONS_B", "proc-b")
    monkeypatch.setenv("CONS_C", "")
    vals = registry.env_values(env_file)
    assert vals["CONS_A"] == "from-file"
    assert vals["CONS_B"] == "proc-b"          # non-empty process env wins
    assert "CONS_C" not in vals and "NOEQ" not in vals
    empty = tmp_path / "empty.env"
    empty.write_text("")
    assert registry.env_values(empty)["CONS_B"] == "proc-b"
    assert registry.env_values(tmp_path / "missing.env")["CONS_B"] == "proc-b"


def _ambiguous_pair():
    a = Member("council/a", "A", {"general": 3}, 10, model_id="openai/x", vendor="cloudflare",
               api_base_ref="os.environ/CF_BASE")
    b = Member("council/b", "A", {"general": 3}, 10, model_id="groq/x", vendor="groq")
    return [a, b]


def test_served_alias_malformed_api_base_never_raises():
    ms = _ambiguous_pair()
    assert registry.served_alias(ms, "council/zzz", "x", "http://[bad/v1", env={}) == "council/zzz"
    assert registry.served_alias(ms, "council/zzz", "x", "http://[groq/v1", env={}) == "council/b"


def test_served_alias_ambiguous_without_api_base_returns_requested():
    assert registry.served_alias(_ambiguous_pair(), "council/zzz", "x", None) == "council/zzz"


def test_probe_alias_tiebreak_rpm_then_alias():
    def m(alias, rpm):
        return Member(alias, "A", {"fast": 4}, rpm, model_id=f"groq/{alias}", vendor="groq")

    probe = registry.providers([m("council/b", 10), m("council/c", 30)])[0].probe_alias
    assert probe == "council/c"
    probe = registry.providers([m("council/b", 30), m("council/a", 30)])[0].probe_alias
    assert probe == "council/a"


def test_load_members_reads_reasoning_effort_and_max_tokens(tmp_path):
    cfg = tmp_path / "r.yaml"
    cfg.write_text(
        "model_list:\n  - model_name: council/r\n    litellm_params: {model: groq/x}\n"
        "    model_info: {privacy_tier: A, scores: {reasoning: 4}, reasoning_effort: true,"
        " max_tokens: 1024}\n"
    )
    m = registry.load_members(cfg)[0]
    assert m.accepts_reasoning_effort is True and m.max_tokens == 1024
    # A member that says nothing accepts nothing: support is verified per member, never
    # inferred from the model id or the vendor.
    plain = _members()["council/cloudflare-gpt-oss-120b"]
    assert plain.accepts_reasoning_effort is False and plain.max_tokens is None


def test_only_the_probed_groq_members_accept_a_reasoning_effort():
    flagged = {a for a, m in _members().items() if m.accepts_reasoning_effort}
    assert flagged == {
        "council/groq-gpt-oss-120b",
        "council/groq-gpt-oss-20b",
        "council/groq-qwen3.8-27b",
    }
