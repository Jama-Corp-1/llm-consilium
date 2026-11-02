from consilium import providers
from consilium.providers import (
    DISPLAY_NAMES,
    SIGNUP_HINTS,
    VENDOR_BASE_URLS,
    Provider,
)


def _by_key():
    return {p.key: p for p in providers.providers()}


def test_providers_keys_match_config():
    assert set(_by_key()) == {"groq", "cloudflare", "mistral", "nvidia_nim"}
    assert all(isinstance(p, Provider) for p in providers.providers())


def test_providers_follow_config_order():
    assert [p.key for p in providers.providers()] == [
        "groq", "cloudflare", "mistral", "nvidia_nim",
    ]


def test_tier_follows_config():
    tier = {k: p.tier for k, p in _by_key().items()}
    assert tier["groq"] == tier["cloudflare"] == "A"
    assert tier["mistral"] == tier["nvidia_nim"] == "B"


def test_cloudflare_probe():
    cf = _by_key()["cloudflare"]
    assert cf.probe_model_id.startswith("@cf/")
    assert cf.probe_alias.startswith("council/cloudflare-")
    assert cf.env_vars == ("CLOUDFLARE_API_BASE", "CLOUDFLARE_API_TOKEN")


def test_groq_probe_is_fastest_member():
    groq = _by_key()["groq"]
    assert groq.probe_alias == "council/groq-gpt-oss-20b"
    assert groq.probe_model_id == "openai/gpt-oss-20b"
    assert groq.env_vars == ("GROQ_API_KEY",)


def test_display_and_signup_present_for_every_provider():
    for p in providers.providers():
        assert p.name == DISPLAY_NAMES[p.key]
        assert p.signup and p.signup == SIGNUP_HINTS[p.key]
        assert p.key in VENDOR_BASE_URLS


def test_hint_tables_cover_known_vendors():
    expected = {"groq", "cloudflare", "mistral", "nvidia_nim", "cerebras", "sambanova"}
    assert set(DISPLAY_NAMES) == set(SIGNUP_HINTS) == set(VENDOR_BASE_URLS) == expected
    assert VENDOR_BASE_URLS["cloudflare"] == ""  # base comes from CLOUDFLARE_API_BASE
    assert "paid plan required since 2026" in SIGNUP_HINTS["cerebras"]


def test_unknown_vendor_gets_key_as_name(tmp_path):
    cfg = tmp_path / "config.yaml"
    cfg.write_text(
        "model_list:\n"
        "  - model_name: council/foo-x\n"
        "    litellm_params:\n"
        "      model: foo/x\n"
        "      api_key: os.environ/FOO_API_KEY\n"
        "    model_info:\n"
        "      privacy_tier: B\n"
    )
    (foo,) = providers.providers(cfg)
    assert foo.key == "foo" and foo.name == "foo" and foo.signup == ""
    assert foo.env_vars == ("FOO_API_KEY",) and foo.probe_model_id == "x"
