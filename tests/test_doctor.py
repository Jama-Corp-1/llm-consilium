import subprocess

from consilium import doctor, env_file


def test_doctor_reports_keys_proxy_and_mcp(tmp_path):
    envf = tmp_path / ".env"
    env_file.write(envf, {"GROQ_API_KEY": "gsk-x"})  # groq ready, others dormant
    out = []

    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "consilium: /path/to/server.py", "")

    rc = doctor.doctor(port_open=lambda _h, _p: True, runner=runner, env_path=envf,
                       echo=out.append)
    joined = "\n".join(out)
    assert rc == 0
    assert "Groq" in joined and "READY" in joined
    assert "dormant" in joined  # a provider without a key
    assert "up" in joined  # proxy reachable (injected True)
    assert "yes" in joined  # mcp registered (runner output contains "consilium")


def test_doctor_proxy_down_and_mcp_absent(tmp_path):
    envf = tmp_path / ".env"
    env_file.write(envf, {})
    out = []

    def runner(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0, "", "")

    doctor.doctor(port_open=lambda _h, _p: False, runner=runner, env_path=envf, echo=out.append)
    joined = "\n".join(out)
    assert "DOWN" in joined and "no" in joined
