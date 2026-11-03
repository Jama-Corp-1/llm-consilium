import os
import sys
from pathlib import Path

from consilium import paths


def test_repo_paths_absolute_and_exist():
    assert paths.REPO_ROOT.is_absolute()
    assert paths.CONFIG_PATH.name == "config.yaml" and paths.CONFIG_PATH.exists()
    assert paths.SERVER_PATH.name == "server.py" and paths.SERVER_PATH.exists()


def test_venv_python_and_litellm_sibling():
    # The suite runs from the environment that has litellm, so the running interpreter wins.
    assert paths.venv_python() == Path(sys.executable)
    assert paths.litellm_exe().parent == Path(sys.executable).parent
    assert paths.litellm_exe().name == ("litellm.exe" if os.name == "nt" else "litellm")


def _make_fake_env(root: Path, *, with_litellm: bool) -> Path:
    bin_dir = root / ("Scripts" if os.name == "nt" else "bin")
    bin_dir.mkdir(parents=True)
    python = bin_dir / ("python.exe" if os.name == "nt" else "python")
    python.write_text("")
    if with_litellm:
        (bin_dir / ("litellm.exe" if os.name == "nt" else "litellm")).write_text("")
    return python


def test_venv_python_falls_back_to_the_repo_venv_when_the_caller_lacks_litellm(
    tmp_path, monkeypatch
):
    # `python3 -m consilium install-service` run with a bare system python used to bake that
    # interpreter into the systemd unit, which then died on /usr/bin/litellm at restart time.
    bare = _make_fake_env(tmp_path / "system", with_litellm=False)
    repo_venv = _make_fake_env(tmp_path / "repo-venv", with_litellm=True)
    monkeypatch.setattr(sys, "executable", str(bare))
    monkeypatch.setattr(paths, "repo_venv_python", lambda: repo_venv)

    assert paths.venv_python() == repo_venv
    assert paths.litellm_exe() == repo_venv.parent / (
        "litellm.exe" if os.name == "nt" else "litellm"
    )


def test_venv_python_keeps_the_caller_when_neither_environment_has_litellm(
    tmp_path, monkeypatch
):
    bare = _make_fake_env(tmp_path / "system", with_litellm=False)
    no_litellm = _make_fake_env(tmp_path / "repo-venv", with_litellm=False)
    monkeypatch.setattr(sys, "executable", str(bare))
    monkeypatch.setattr(paths, "repo_venv_python", lambda: no_litellm)

    assert paths.venv_python() == bare


def test_repo_venv_python_points_inside_the_repo():
    assert paths.repo_venv_python().is_relative_to(paths.REPO_ROOT)
    assert ".venv" in paths.repo_venv_python().parts


def test_proxy_host_port_and_dirs():
    assert paths.PROXY_HOST == "127.0.0.1" and paths.PROXY_PORT == 4000
    assert paths.PID_PATH.name == "proxy.pid" and paths.LOG_PATH.name == "proxy.log"
    assert paths.SYSTEMD_USER_DIR.parts[-2:] == ("systemd", "user")
