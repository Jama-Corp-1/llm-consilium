from consilium import __main__ as cli


def test_dispatch_start_foreground(monkeypatch):
    got = {}

    def fake_start(*, foreground=False):
        got["fg"] = foreground
        return 0

    monkeypatch.setattr(cli.service, "start", fake_start)
    assert cli.main(["start", "--foreground"]) == 0 and got["fg"] is True
    assert cli.main(["start"]) == 0 and got["fg"] is False


def test_dispatch_each_command(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.service, "stop", lambda: seen.append("stop") or 0)
    monkeypatch.setattr(cli.service, "status", lambda: seen.append("status") or 0)
    monkeypatch.setattr(cli.setup, "mcp_register", lambda: seen.append("reg") or 0)
    monkeypatch.setattr(cli.setup, "install_service", lambda: seen.append("svc") or 0)
    monkeypatch.setattr(cli.doctor, "doctor", lambda: seen.append("doc") or 0)
    for cmd in ["stop", "status", "mcp-register", "install-service", "doctor"]:
        assert cli.main([cmd]) == 0
    assert seen == ["stop", "status", "reg", "svc", "doc"]


def test_dispatch_check_env_and_health(monkeypatch):
    seen = []
    monkeypatch.setattr(cli.check_env, "check_env", lambda: seen.append("check") or 0)

    def fake_health(argv):
        seen.append(("health", argv))
        return 1

    monkeypatch.setattr(cli.health_cmd, "health", fake_health)
    assert cli.main(["check-env"]) == 0
    assert cli.main(["health", "--json"]) == 1
    assert seen == ["check", ("health", ["--json"])]


def test_usage_lists_new_commands(capsys):
    cli.main([])
    out = capsys.readouterr().out
    assert "check-env" in out and "health" in out


def test_unknown_command_returns_usage(capsys):
    assert cli.main(["bogus"]) == 2
    assert cli.main([]) == 2
