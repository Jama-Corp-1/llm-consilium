from consilium.providers import PROVIDERS, Provider


def test_registry_covers_seven_providers():
    keys = {p.key for p in PROVIDERS}
    assert keys == {"groq", "cloudflare", "cerebras", "mistral", "sambanova", "nvidia"}


def test_each_provider_well_formed():
    for p in PROVIDERS:
        assert isinstance(p, Provider)
        assert p.tier in {"A", "B"}
        assert p.env_vars and all(v.isupper() for v in p.env_vars)
        assert p.signup and p.ping_model


def test_cloudflare_needs_token_and_base():
    cf = next(p for p in PROVIDERS if p.key == "cloudflare")
    assert cf.env_vars == ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_BASE")


def test_tiers_match_project_policy():
    tier = {p.key: p.tier for p in PROVIDERS}
    assert tier["cerebras"] == tier["groq"] == tier["cloudflare"] == "A"
    assert tier["mistral"] == tier["sambanova"] == tier["nvidia"] == "B"
