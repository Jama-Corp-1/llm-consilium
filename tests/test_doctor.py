import subprocess

import pytest

from consilium import doctor, env_file, health_cmd
from council.health import HealthStore


@pytest.fixture(autouse=True)
def _no_process_master_key(monkeypatch):
    monkeypatch.delenv("LITELLM_MASTER_KEY", raising=False)


def _runner(stdout):
    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, stdout, "")

    return runner


def test_doctor_reports_keys_proxy_and_mcp(tmp_path):
    envf = tmp_path / ".env"
    env_file.write(envf, {"GROQ_API_KEY": "gsk-x"})  # groq ready, others dormant
    out = []

    rc = doctor.doctor(port_open=lambda _h, _p: True,
                       runner=_runner("consilium: /path/to/server.py"), env_path=envf,
                       echo=out.append, store=HealthStore(tmp_path / "health.json"))
    joined = "\n".join(out)
    assert rc == 0
    assert "Groq" in joined and "READY" in joined
    assert "dormant" in joined  # a provider without a key
    assert "up" in joined  # proxy reachable (injected True)
    assert "yes" in joined  # mcp registered (runner output contains "consilium")
    assert out[-1] == "Health: skipped (proxy down or no master key)"  # no master key


def test_doctor_proxy_down_and_mcp_absent(tmp_path):
    envf = tmp_path / ".env"
    env_file.write(envf, {})
    out = []

    doctor.doctor(port_open=lambda _h, _p: False, runner=_runner(""), env_path=envf,
                  echo=out.append, store=HealthStore(tmp_path / "health.json"))
    joined = "\n".join(out)
    assert "DOWN" in joined and "no" in joined


def test_doctor_prints_health_table_when_probe_injected(tmp_path):
    envf = tmp_path / ".env"
    env_file.write(envf, {"GROQ_API_KEY": "gsk-x", "LITELLM_MASTER_KEY": "sk-secret"})
    store = HealthStore(tmp_path / "health.json")
    outcomes = [
        health_cmd.ProbeOutcome("council/groq-a", "A", "up", "", 50),
        health_cmd.ProbeOutcome("council/groq-b", "A", "down", "404 model not found", 40),
    ]
    out = []
    rc = doctor.doctor(port_open=lambda _h, _p: True, runner=_runner("consilium"),
                       env_path=envf, echo=out.append, store=store, probe=lambda: outcomes)
    joined = "\n".join(out)
    assert rc == 0
    assert "council/groq-b" in joined and "404 model not found" in joined
    assert out[-1].splitlines()[-1] == "1 up, 1 down, 0 unknown"
    assert store.status_of("council/groq-b") == ("down", "404 model not found")
    assert "sk-secret" not in joined


def test_doctor_skips_health_when_proxy_down(tmp_path):
    envf = tmp_path / ".env"
    env_file.write(envf, {"LITELLM_MASTER_KEY": "sk-secret"})
    called = []

    def probe():
        called.append(1)
        return []

    out = []
    doctor.doctor(port_open=lambda _h, _p: False, runner=_runner(""), env_path=envf,
                  echo=out.append, store=HealthStore(tmp_path / "health.json"), probe=probe)
    assert called == []
    assert out[-1] == "Health: skipped (proxy down or no master key)"


def test_doctor_probe_error_is_best_effort(tmp_path):
    envf = tmp_path / ".env"
    env_file.write(envf, {"GROQ_API_KEY": "gsk-x", "LITELLM_MASTER_KEY": "sk-secret"})

    def boom():
        raise RuntimeError("probe exploded")

    out = []
    rc = doctor.doctor(port_open=lambda _h, _p: True, runner=_runner("consilium"),
                       env_path=envf, echo=out.append,
                       store=HealthStore(tmp_path / "health.json"), probe=boom)
    assert rc == 0
    assert out[-1] == "Health: error (RuntimeError)"
    assert "sk-secret" not in "\n".join(out)


def test_doctor_live_probe_uses_env_path_for_members(tmp_path, monkeypatch):
    envf = tmp_path / ".env"
    env_file.write(envf, {"GROQ_API_KEY": "gsk-x", "LITELLM_MASTER_KEY": "sk-secret"})
    seen = {}

    def fake_keys(path):
        seen["path"] = path
        return {"GROQ_API_KEY"}

    async def fake_probe(members, base_url, api_key):
        seen["members"] = [m.alias for m in members]
        return []

    monkeypatch.setattr(doctor.registry, "available_env_keys", fake_keys)
    monkeypatch.setattr(doctor.health_cmd, "probe_members", fake_probe)
    rc = doctor.doctor(port_open=lambda _h, _p: True, runner=_runner("consilium"),
                       env_path=envf, echo=lambda _m: None,
                       store=HealthStore(tmp_path / "health.json"))
    assert rc == 0 and seen["path"] == envf
