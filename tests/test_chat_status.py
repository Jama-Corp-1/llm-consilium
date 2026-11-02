from fastapi.testclient import TestClient

from consilium_chat.app import create_app, is_configured


def test_is_configured_false_when_empty():
    assert is_configured({}) is False


def test_is_configured_true_when_a_provider_ready():
    # Groq needs only GROQ_API_KEY
    assert is_configured({"GROQ_API_KEY": "x"}) is True


def test_is_configured_false_on_partial_multivar():
    # Cloudflare needs BOTH CLOUDFLARE_API_TOKEN and CLOUDFLARE_API_BASE
    assert is_configured({"CLOUDFLARE_API_TOKEN": "x"}) is False


def test_status_has_configured_and_provider_metadata(tmp_path, monkeypatch):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    body = TestClient(create_app(service=object())).get("/api/status").json()
    assert "configured" in body and isinstance(body["configured"], bool)
    assert body["providers"], "expected provider rows"
    row = body["providers"][0]
    assert "env_vars" in row and isinstance(row["env_vars"], list)
    assert "signup" in row


def test_proxy_restart_stops_then_starts(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium_chat import app as appmod

    calls = []
    monkeypatch.setattr(appmod.proxy_service, "stop", lambda: calls.append("stop"))
    monkeypatch.setattr(appmod.proxy_service, "start", lambda: calls.append("start"))
    monkeypatch.setattr(appmod.proxy_service, "port_open", lambda h, p: True)

    out = create_app(service=object()).state.proxy_restart()
    assert calls == ["stop", "start"]
    assert out == {"ok": True, "proxy_up": True}


def test_app_reads_providers_once_from_config(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium import providers
    from consilium.providers import Provider
    from consilium_chat import app as appmod

    calls = []
    fake = (Provider("zed", "Zed AI", "A", ("ZED_API_KEY",), "zed.dev", "council/zed", "z"),)

    def fake_providers(*_a, **_k):
        calls.append(1)
        return fake

    monkeypatch.setattr(providers, "providers", fake_providers)
    monkeypatch.setattr(appmod.env_file, "load", lambda *_a, **_k: {"ZED_API_KEY": "k"})
    app = create_app(service=object())
    assert len(calls) == 1  # read once at app creation
    body = app.state.status_provider()
    assert len(calls) == 1  # not re-read per request
    assert body["configured"] is True
    assert [r["name"] for r in body["providers"]] == ["Zed AI"]
    assert body["providers"][0]["env_vars"] == ["ZED_API_KEY"]
    assert is_configured({"ZED_API_KEY": "k"}, fake) is True
    assert is_configured({}, fake) is False


def test_save_keys_pings_config_providers(monkeypatch, tmp_path):
    monkeypatch.setenv("CONSILIUM_CHAT_DB", str(tmp_path / "c.db"))
    from consilium import init, providers
    from consilium.providers import Provider
    from consilium_chat import app as appmod

    fake = (Provider("zed", "Zed AI", "B", ("ZED_API_KEY",), "", "council/zed", "z"),)
    monkeypatch.setattr(providers, "providers", lambda *_a, **_k: fake)
    monkeypatch.setattr(appmod.env_file, "load", lambda *_a, **_k: {})
    monkeypatch.setattr(appmod.env_file, "write", lambda *_a, **_k: None)
    pinged = []

    def fake_ping(p, merged, *, client=None):
        pinged.append((p.key, merged["ZED_API_KEY"], client is not None))
        return init.PingResult(True, "ok")

    monkeypatch.setattr(appmod.init, "live_ping", fake_ping)
    out = create_app(service=object()).state.save_keys({"ZED_API_KEY": "secret-1234"})
    assert pinged == [("zed", "secret-1234", True)]
    assert out["readiness"] == [{"name": "Zed AI", "tier": "B", "ok": True, "detail": "ok"}]
    assert "secret-1234" not in str(out)
