from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable
from typing import Any

from consilium import paths

Runner = Callable[..., "subprocess.CompletedProcess[Any]"]
Echo = Callable[[str], object]


def mcp_add_command() -> list[str]:
    return [
        "claude", "mcp", "add", "--scope", "user", "consilium", "--",
        str(paths.venv_python()), str(paths.SERVER_PATH),
    ]


def mcp_remove_command() -> list[str]:
    return ["claude", "mcp", "remove", "--scope", "user", "consilium"]


def mcp_register(*, runner: Runner = subprocess.run, echo: Echo = print) -> int:
    runner(mcp_remove_command(), capture_output=True, text=True)  # ignore if absent
    result = runner(mcp_add_command(), capture_output=True, text=True)
    if result.returncode == 0:
        echo("registered consilium MCP (--scope user) — restart Claude Code to load it")
        return 0
    echo(f"mcp-register failed: {(result.stderr or '').strip()}")
    return 1


def systemd_unit_text(python: str, repo_root: str) -> str:
    return (
        "[Unit]\n"
        "Description=Consilium LiteLLM proxy (free-LLM council compute layer)\n"
        "After=network-online.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"WorkingDirectory={repo_root}\n"
        f"Environment=PYTHONPATH={repo_root}\n"
        f"ExecStart={python} -m consilium start --foreground\n"
        "Restart=on-failure\n"
        "RestartSec=3\n\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install_service(*, runner: Runner = subprocess.run, echo: Echo = print) -> int:
    if not sys.platform.startswith("linux"):
        echo(
            "Autostart isn't automated on this OS. Run `python -m consilium start` at "
            "login, or add a Task Scheduler task (Windows) running: "
            f"{paths.venv_python()} -m consilium start --foreground"
        )
        return 0
    unit_path = paths.SYSTEMD_USER_DIR / "consilium-proxy.service"
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(systemd_unit_text(str(paths.venv_python()), str(paths.REPO_ROOT)))
    runner(["systemctl", "--user", "daemon-reload"])
    runner(["systemctl", "--user", "enable", "--now", "consilium-proxy"])
    runner(["loginctl", "enable-linger"])
    echo(f"installed systemd --user unit: {unit_path}")
    return 0
