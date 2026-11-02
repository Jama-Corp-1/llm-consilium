import os

from consilium import env_file


def test_load_parses_keys_and_skips_comments(tmp_path):
    p = tmp_path / ".env"
    p.write_text("# comment\nA=1\n\nB = two \n# x\nUNKNOWN_X=zzz\n")
    assert env_file.load(p) == {"A": "1", "B": "two", "UNKNOWN_X": "zzz"}


def test_load_missing_file_returns_empty(tmp_path):
    assert env_file.load(tmp_path / "nope.env") == {}


def test_write_roundtrips_and_preserves_unknown(tmp_path):
    p = tmp_path / ".env"
    values = {
        "CEREBRAS_API_KEY": "csk-x",
        "MISTRAL_API_KEY": "m",
        "LITELLM_MASTER_KEY": "sk-1",
        "WEIRD": "w",
    }
    env_file.write(p, values)
    assert env_file.load(p) == values  # every key round-trips, incl. unknown "WEIRD"


def test_write_sets_posix_permissions(tmp_path):
    p = tmp_path / ".env"
    env_file.write(p, {"LITELLM_MASTER_KEY": "sk-1"})
    if os.name == "posix":
        assert (p.stat().st_mode & 0o777) == 0o600


def test_write_replaces_symlink_instead_of_following(tmp_path):
    if os.name != "posix":
        import pytest
        pytest.skip("symlink creation needs privilege on Windows")
    target = tmp_path / "target.txt"
    target.write_text("PREEXISTING")
    link = tmp_path / ".env"
    os.symlink(target, link)
    env_file.write(link, {"LITELLM_MASTER_KEY": "sk-secret"})
    assert not link.is_symlink()  # symlink replaced by a real file
    assert env_file.load(link)["LITELLM_MASTER_KEY"] == "sk-secret"
    assert target.read_text() == "PREEXISTING"  # attacker target NOT written through


def test_write_tightens_perms_on_preexisting_loose_file(tmp_path):
    if os.name != "posix":
        import pytest
        pytest.skip("posix permissions")
    p = tmp_path / ".env"
    p.write_text("OLD=1")
    p.chmod(0o644)
    env_file.write(p, {"LITELLM_MASTER_KEY": "sk-1"})
    assert (p.stat().st_mode & 0o777) == 0o600  # loose perms fixed atomically, no race


def test_groups_from_config():
    groups = env_file.groups()
    titles = [t for t, _ in groups]
    assert titles == [
        "Tier A (safe for any prompt — no-train)",
        "Tier B (public prompts only)",
        "Proxy auth",
    ]
    tier_a, tier_b, auth = (v for _, v in groups)
    # vendor order of first appearance in config (groq, cloudflare), vars sorted per vendor
    assert set(tier_a) == {"GROQ_API_KEY", "CLOUDFLARE_API_BASE", "CLOUDFLARE_API_TOKEN"}
    assert tier_a == ("GROQ_API_KEY", "CLOUDFLARE_API_BASE", "CLOUDFLARE_API_TOKEN")
    assert "MISTRAL_API_KEY" in tier_b
    assert tier_b == ("MISTRAL_API_KEY", "NVIDIA_NIM_API_KEY")
    assert auth == ("LITELLM_MASTER_KEY",)


def test_groups_accepts_explicit_providers():
    from consilium.providers import Provider

    provs = [
        Provider("b", "B", "B", ("B_API_KEY",), "", "b/x", "x"),
        Provider("a", "A", "A", ("A_API_KEY", "A_BASE"), "", "a/x", "x"),
    ]
    groups = dict(env_file.groups(provs))
    assert groups["Tier A (safe for any prompt — no-train)"] == ("A_API_KEY", "A_BASE")
    assert groups["Tier B (public prompts only)"] == ("B_API_KEY",)


def test_write_puts_unknown_keys_in_other(tmp_path):
    p = tmp_path / ".env"
    env_file.write(p, {"GROQ_API_KEY": "g", "CEREBRAS_API_KEY": "c", "WEIRD": "w",
                       "LITELLM_MASTER_KEY": "sk-1"})
    text = p.read_text()
    other = text.index("# Other")
    assert (text.index("# Tier A") < text.index("GROQ_API_KEY=g")
            < text.index("# Proxy auth") < other)
    # no cerebras member in config -> its key is preserved under "Other"
    assert other < text.index("CEREBRAS_API_KEY=c") and other < text.index("WEIRD=w")


def test_write_survives_broken_config(monkeypatch, tmp_path):
    # Persisting secrets must never depend on proxy/config.yaml being valid.
    from consilium import providers

    def boom(*_a, **_k):
        raise ValueError("vendor x mixes tiers A/B")

    monkeypatch.setattr(providers, "providers", boom)
    p = tmp_path / ".env"
    values = {"GROQ_API_KEY": "g", "LITELLM_MASTER_KEY": "sk-1"}
    env_file.write(p, values)
    assert env_file.load(p) == values
    assert "# Proxy auth" in p.read_text()


def test_master_key_env_over_file(monkeypatch, tmp_path):
    p = tmp_path / ".env"
    env_file.write(p, {"LITELLM_MASTER_KEY": "sk-file"})
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    assert env_file.master_key(p) == "sk-file"
    monkeypatch.setenv("LITELLM_MASTER_KEY", "sk-env")
    assert env_file.master_key(p) == "sk-env"


def test_master_key_none_when_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)
    assert env_file.master_key(tmp_path / "nope.env") is None
    p = tmp_path / ".env"
    env_file.write(p, {"GROQ_API_KEY": "g"})
    assert env_file.master_key(p) is None
