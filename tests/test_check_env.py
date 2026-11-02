import pytest

from consilium import check_env


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    for var in ("GROQ_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_BASE",
                "MISTRAL_API_KEY", "NVIDIA_NIM_API_KEY", "CEREBRAS_API_KEY",
                "CONSILIUM_ENV_FILE"):
        monkeypatch.delenv(var, raising=False)


def test_ready_when_groq_present(tmp_path):
    envf = tmp_path / ".env"
    envf.write_text("GROQ_API_KEY=gsk-x\n")
    out = []
    rc = check_env.check_env(env_path=envf, echo=out.append)
    joined = "\n".join(out)
    assert rc == 0
    assert "READY   Groq [Tier A]" in joined
    assert "dormant Cloudflare Workers AI [Tier A]" in joined
    assert out[-1] == "OK: Tier-A providers ready: Groq"


def test_ready_from_process_env(tmp_path, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-x")
    out = []
    assert check_env.check_env(env_path=tmp_path / "missing", echo=out.append) == 0
    assert out[-1] == "OK: Tier-A providers ready: Groq"


def test_exit_1_without_tier_a(tmp_path):
    envf = tmp_path / ".env"
    envf.write_text("MISTRAL_API_KEY=m-x\n")
    out = []
    rc = check_env.check_env(env_path=envf, echo=out.append)
    joined = "\n".join(out)
    assert rc == 1
    assert "READY   Mistral [Tier B]" in joined
    assert "no Tier-A" in joined


def test_check_env_no_file_exit_1(tmp_path):
    out = []
    rc = check_env.check_env(env_path=tmp_path / "nope" / ".env", echo=out.append)
    assert rc == 1
    assert out[-1] == (
        "ERROR: no Tier-A provider configured "
        "(set keys for one of: Groq, Cloudflare Workers AI)"
    )


def test_default_env_path_honours_consilium_env_file(tmp_path, monkeypatch):
    envf = tmp_path / ".env"
    envf.write_text("GROQ_API_KEY=gsk-x\n")
    monkeypatch.setenv("CONSILIUM_ENV_FILE", str(envf))
    assert check_env.check_env(echo=lambda _s: None) == 0
