import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-proxy.sh"
ENV_EXAMPLE = ROOT / ".env.example"
REQUIRED_VARS = {
    "GROQ_API_KEY",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_API_BASE",
    "LITELLM_MASTER_KEY",
}


def _run(env):
    full = {"PATH": os.environ["PATH"], "CONSILIUM_ENV_FILE": "/nonexistent"}
    full.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)], env=full, capture_output=True, text=True
    )


def _all_vars():
    return {v: "x" for v in REQUIRED_VARS}


def test_check_only_passes_with_all_vars():
    env = _all_vars()
    env["CONSILIUM_CHECK_ONLY"] = "1"
    result = _run(env)
    assert result.returncode == 0, result.stderr


def test_missing_var_fails_fast():
    env = _all_vars()
    del env["CLOUDFLARE_API_BASE"]
    env["CONSILIUM_CHECK_ONLY"] = "1"
    result = _run(env)
    assert result.returncode != 0
    assert "CLOUDFLARE_API_BASE" in result.stderr


def test_env_example_covers_required_vars():
    keys = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    assert REQUIRED_VARS <= keys
