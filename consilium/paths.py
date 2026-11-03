from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "proxy" / "config.yaml"
SERVER_PATH = REPO_ROOT / "consilium_mcp" / "server.py"

CONFIG_DIR = Path.home() / ".config" / "consilium"
PID_PATH = CONFIG_DIR / "proxy.pid"
LOG_PATH = CONFIG_DIR / "proxy.log"
SYSTEMD_USER_DIR = Path.home() / ".config" / "systemd" / "user"

PROXY_HOST = "127.0.0.1"
PROXY_PORT = 4000


def _litellm_beside(python: Path) -> Path:
    name = "litellm.exe" if os.name == "nt" else "litellm"
    return python.parent / name


def repo_venv_python() -> Path:
    """Where this repo's own virtualenv interpreter lives, whether or not it exists."""
    if os.name == "nt":
        return REPO_ROOT / ".venv" / "Scripts" / "python.exe"
    return REPO_ROOT / ".venv" / "bin" / "python"


def venv_python() -> Path:
    """The interpreter that can actually serve the proxy.

    ``sys.executable`` is correct whenever consilium was invoked from the environment its
    dependencies live in. It is silently wrong when a command is run with a bare system
    python: ``install-service`` and ``mcp-register`` bake the interpreter path into a
    systemd unit and an MCP registration, so a system python yields a unit that dies at
    exec time with ``FileNotFoundError: /usr/bin/litellm``, and an MCP server that cannot
    import its dependencies. Since the failure surfaces later — on the next restart, not
    at install time — prefer the repo's own ``.venv`` when the running interpreter has no
    litellm beside it.
    """
    current = Path(sys.executable)
    if _litellm_beside(current).exists():
        return current
    candidate = repo_venv_python()
    if _litellm_beside(candidate).exists():
        return candidate
    return current


def litellm_exe() -> Path:
    return _litellm_beside(venv_python())
