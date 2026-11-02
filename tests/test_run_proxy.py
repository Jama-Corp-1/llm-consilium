import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "run-proxy.sh"
ENV_EXAMPLE = ROOT / ".env.example"
REQUIRED_VARS = {"LITELLM_MASTER_KEY"}


def _run(env):
    # The script falls back to `python3` on PATH when the repo has no .venv (e.g. a
    # worktree); put the test interpreter's bin dir first so it can import yaml.
    path = os.pathsep.join([os.path.dirname(sys.executable), os.environ["PATH"]])
    full = {"PATH": path, "CONSILIUM_ENV_FILE": "/nonexistent"}
    full.update(env)
    return subprocess.run(
        ["bash", str(SCRIPT)], env=full, capture_output=True, text=True
    )


def test_check_only_passes_with_groq_key():
    env = {"LITELLM_MASTER_KEY": "x", "GROQ_API_KEY": "x", "CONSILIUM_CHECK_ONLY": "1"}
    result = _run(env)
    assert result.returncode == 0, result.stderr
    assert "READY   Groq" in result.stderr
    assert "dormant Cloudflare Workers AI" in result.stderr


def test_check_only_fails_without_tier_a_provider():
    env = {"LITELLM_MASTER_KEY": "x", "CONSILIUM_CHECK_ONLY": "1"}
    result = _run(env)
    assert result.returncode != 0
    assert "Tier-A" in result.stdout + result.stderr


def test_missing_var_fails_fast():
    env = {"GROQ_API_KEY": "x", "CONSILIUM_CHECK_ONLY": "1"}
    result = _run(env)
    assert result.returncode != 0
    assert "LITELLM_MASTER_KEY" in result.stderr


def test_env_example_covers_required_vars():
    keys = {
        line.split("=", 1)[0].strip()
        for line in ENV_EXAMPLE.read_text().splitlines()
        if "=" in line and not line.strip().startswith("#")
    }
    assert {"GROQ_API_KEY", "CLOUDFLARE_API_TOKEN", "CLOUDFLARE_API_BASE"} | REQUIRED_VARS <= keys
