import subprocess
import sys

from consilium import paths, setup


def test_mcp_commands_use_absolute_paths():
    add = setup.mcp_add_command()
    assert add[:6] == ["claude", "mcp", "add", "--scope", "user", "consilium"]
    assert str(paths.venv_python()) in add and str(paths.SERVER_PATH) in add
    assert setup.mcp_remove_command()[:3] == ["claude", "mcp", "remove"]


def test_mcp_register_removes_then_adds():
    calls = []

    def runner(cmd, **kw):
        calls.append(cmd[:4])
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert setup.mcp_register(runner=runner, echo=lambda _m: None) == 0
    assert calls[0][:3] == ["claude", "mcp", "remove"]
    assert calls[1][:3] == ["claude", "mcp", "add"]


def test_systemd_unit_text_calls_foreground():
    unit = setup.systemd_unit_text("/venv/bin/python", "/home/u/repo")
    assert "ExecStart=/venv/bin/python -m consilium start --foreground" in unit
    assert "WorkingDirectory=/home/u/repo" in unit and "PYTHONPATH=/home/u/repo" in unit
    assert "WantedBy=default.target" in unit


def test_install_service_non_linux_prints_only(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    ran = []
    rc = setup.install_service(runner=lambda *a, **k: ran.append(a), echo=lambda _m: None)
    assert rc == 0 and ran == []  # no systemctl on non-Linux


def test_install_service_linux_writes_unit(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(paths, "SYSTEMD_USER_DIR", tmp_path / "user")
    ran = []
    rc = setup.install_service(runner=lambda cmd, **k: ran.append(cmd[0]), echo=lambda _m: None)
    assert rc == 0
    unit = (tmp_path / "user" / "consilium-proxy.service").read_text()
    assert "consilium start --foreground" in unit
    assert "systemctl" in ran and "loginctl" in ran


def test_health_unit_text_fields():
    unit = setup.systemd_health_unit_text("/venv/bin/python", "/home/u/repo")
    assert "Description=Consilium health probe" in unit
    assert "After=consilium-proxy.service" in unit
    assert "Type=oneshot" in unit
    assert "WorkingDirectory=/home/u/repo" in unit and "PYTHONPATH=/home/u/repo" in unit
    assert "ExecStart=/venv/bin/python -m consilium health" in unit


def test_health_timer_text_fields():
    timer = setup.systemd_health_timer_text()
    assert "[Timer]" in timer
    assert "OnBootSec=2min" in timer
    assert "OnUnitActiveSec=30min" in timer
    assert "Persistent=true" in timer
    assert "WantedBy=timers.target" in timer


def test_install_service_writes_timer_and_enables_it(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(paths, "SYSTEMD_USER_DIR", tmp_path)
    calls = []

    def runner(cmd, **kw):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    assert setup.install_service(runner=runner, echo=lambda _m: None) == 0
    service = (tmp_path / "consilium-health.service").read_text()
    timer = (tmp_path / "consilium-health.timer").read_text()
    assert "-m consilium health" in service
    assert "OnUnitActiveSec=30min" in timer
    assert ["systemctl", "--user", "daemon-reload"] in calls
    assert ["systemctl", "--user", "enable", "--now", "consilium-health.timer"] in calls


def test_install_service_non_linux_mentions_health(monkeypatch):
    monkeypatch.setattr(sys, "platform", "win32")
    said = []
    setup.install_service(runner=lambda *a, **k: None, echo=said.append)
    assert any("-m consilium health" in m for m in said)


def test_install_service_refuses_when_the_interpreter_has_no_litellm(tmp_path, monkeypatch):
    """A unit written against an interpreter without litellm looks installed and dies later.

    This is the failure that happened in practice: `python3 -m consilium install-service`
    with the system python wrote ExecStart=/usr/bin/python3, the proxy kept running on its
    old process, and the next restart failed with FileNotFoundError on /usr/bin/litellm.
    """
    calls: list[list[str]] = []
    messages: list[str] = []
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.setattr(paths, "SYSTEMD_USER_DIR", tmp_path / "units")
    monkeypatch.setattr(paths, "litellm_exe", lambda: tmp_path / "nowhere" / "litellm")

    rc = setup.install_service(
        runner=lambda *a, **k: calls.append(list(a[0])), echo=messages.append
    )

    assert rc == 1
    assert calls == []  # no systemctl was touched
    assert not (tmp_path / "units").exists()  # and no unit file was written
    assert "refusing to install" in messages[0]
