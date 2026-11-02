from fastapi.testclient import TestClient

from consilium_chat.app import create_app


def test_app_serves_index_and_status(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))

    class FakeService:
        def list_models(self):
            return []

    client = TestClient(create_app(service=FakeService()))
    assert client.get("/").status_code == 200
    body = client.get("/api/status").json()
    assert "providers" in body and "proxy_up" in body and "total_cost_usd" in body


def test_app_builds_when_council_service_raises(tmp_path, monkeypatch):
    # The guard in create_app must degrade to service=None (not raise) when
    # CouncilService.build() fails — the first-run/unconfigured path this feature targets.
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium_chat import app as appmod

    def _boom():
        raise RuntimeError("no proxy yet")

    monkeypatch.setattr(appmod.CouncilService, "build", staticmethod(_boom))
    app = appmod.create_app()  # no service= → exercises the try/except
    assert app is not None
    assert TestClient(app).get("/api/status").status_code == 200


def _patch_main(m, monkeypatch, *, loaded):
    served, opens = {}, []
    monkeypatch.setattr(m, "_serve", lambda app, host, port: served.update(host=host, port=port))
    monkeypatch.setattr(m, "_open_browser", lambda url: opens.append(url))
    monkeypatch.setattr(m, "_load_env", lambda: loaded)
    return served, opens


def test_main_resolves_host_port(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium_chat import __main__ as m

    served, _ = _patch_main(m, monkeypatch, loaded={"GROQ_API_KEY": "x"})
    m.main(["--host", "0.0.0.0", "--port", "9001"])
    assert served == {"host": "0.0.0.0", "port": 9001}


def test_main_defaults_from_settings(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium_chat import __main__ as m

    served, _ = _patch_main(m, monkeypatch, loaded={"GROQ_API_KEY": "x"})
    m.main([])
    assert served["host"] == "127.0.0.1" and served["port"] == 8080


def test_should_open_truth_table():
    from consilium_chat import __main__ as m

    assert m._should_open("off", False, "127.0.0.1") is False
    assert m._should_open("on", True, "127.0.0.1") is True
    assert m._should_open("on", True, "0.0.0.0") is False       # non-loopback never opens
    assert m._should_open("auto", False, "127.0.0.1") is True   # first run
    assert m._should_open("auto", True, "127.0.0.1") is False   # already configured


def test_main_opens_browser_when_unconfigured(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium_chat import __main__ as m

    _, opens = _patch_main(m, monkeypatch, loaded={})
    m.main([])
    assert opens == ["http://127.0.0.1:8080/"]


def test_main_no_open_flag_suppresses(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium_chat import __main__ as m

    _, opens = _patch_main(m, monkeypatch, loaded={})
    m.main(["--no-open"])
    assert opens == []


def test_main_skips_open_when_configured(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium_chat import __main__ as m

    _, opens = _patch_main(m, monkeypatch, loaded={"GROQ_API_KEY": "x"})
    m.main([])
    assert opens == []


def test_main_survives_broken_provider_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium import providers
    from consilium_chat import __main__ as m

    def broken():
        raise ValueError("bad proxy/config.yaml")

    monkeypatch.setattr(providers, "providers", broken)
    served, opens = _patch_main(m, monkeypatch, loaded={"GROQ_API_KEY": "x"})
    m.main([])
    assert served["port"] == 8080
    assert opens == ["http://127.0.0.1:8080/"]  # no providers -> treated as unconfigured
