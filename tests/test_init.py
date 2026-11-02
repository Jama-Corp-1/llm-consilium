import json

import httpx

from consilium import env_file, init, providers
from consilium.providers import Provider


def _prov(key):
    return next(p for p in providers.providers() if p.key == key)


def test_mask_hides_secret():
    assert init.mask("") == "not set"
    assert "1234" in init.mask("csk-abcd1234")  # last-4 surfaced for recognition
    assert "abcd1234" not in init.mask("csk-abcd1234")  # full value never shown


def test_live_ping_ok_and_error():
    def handler(request):
        return httpx.Response(200 if "good" in request.headers["authorization"] else 401)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    groq = _prov("groq")
    assert init.live_ping(groq, {"GROQ_API_KEY": "good"}, client=client).ok is True
    assert init.live_ping(groq, {"GROQ_API_KEY": "bad"}, client=client).ok is False


def test_live_ping_survives_non_httperror():
    # A malformed base (e.g. user-entered CLOUDFLARE_API_BASE) raises httpx.InvalidURL,
    # which is NOT an httpx.HTTPError — the ping must still degrade gracefully, never raise.
    class Boom:
        def post(self, *args, **kwargs):
            raise httpx.InvalidURL("malformed base")

    groq = _prov("groq")
    res = init.live_ping(groq, {"GROQ_API_KEY": "x"}, client=Boom())
    assert res.ok is False and "InvalidURL" in res.detail


def test_run_writes_keys_and_reports_readiness(tmp_path):
    p = tmp_path / ".env"
    # scripted answers: one per env_var across all providers, in config order:
    # groq, cloudflare(base, token -- sorted env_vars), mistral, nvidia_nim
    answers = iter(["gsk-groq", "", "", "msk-mist", ""])
    lines: list[str] = []
    asked: list[str] = []

    def prompt(msg):
        asked.append(msg.split()[0])
        return next(answers)

    pinged: list[str] = []

    def ping(provider, env):
        pinged.append(provider.key)
        return init.PingResult(ok=True, detail="ok")

    rc = init.run(env_path=p, prompt=prompt, echo=lines.append, ping=ping)
    assert rc == 0
    assert asked == ["GROQ_API_KEY", "CLOUDFLARE_API_BASE", "CLOUDFLARE_API_TOKEN",
                     "MISTRAL_API_KEY", "NVIDIA_NIM_API_KEY"]
    saved = env_file.load(p)
    assert saved["MISTRAL_API_KEY"] == "msk-mist" and saved["GROQ_API_KEY"] == "gsk-groq"
    assert saved["LITELLM_MASTER_KEY"].startswith("sk-")  # generated
    # only providers with all keys are pinged; cloudflare (base+token skipped) is dormant
    assert pinged == ["groq", "mistral"]
    joined = "\n".join(lines)
    assert "Groq" in joined and "Mistral" in joined and "dormant" in joined
    assert "msk-mist" not in joined and "gsk-groq" not in joined  # secrets never echoed


def test_live_ping_uses_vendor_base_and_probe_model():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    res = init.live_ping(_prov("groq"), {"GROQ_API_KEY": "k"}, client=client)
    assert res.ok is True
    (req,) = seen
    assert str(req.url) == "https://api.groq.com/openai/v1/chat/completions"
    body = json.loads(req.content)
    assert body["model"] == "openai/gpt-oss-20b" and body["max_tokens"] == 1
    assert req.headers["authorization"] == "Bearer k"


def test_live_ping_cloudflare_base_from_env():
    seen: list[httpx.Request] = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    cf = _prov("cloudflare")
    env = {"CLOUDFLARE_API_BASE": "https://cf.example/ai/v1/",
           "CLOUDFLARE_API_TOKEN": "cft"}
    assert init.live_ping(cf, env, client=client).ok is True
    (req,) = seen
    assert str(req.url) == "https://cf.example/ai/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer cft"  # token, not the base
    assert json.loads(req.content)["model"] == cf.probe_model_id
    assert cf.probe_model_id.startswith("@cf/")


def test_live_ping_cloudflare_without_base():
    cf = _prov("cloudflare")
    res = init.live_ping(cf, {"CLOUDFLARE_API_TOKEN": "cft"}, client=httpx.Client())
    assert res == init.PingResult(False, "no base url")


def test_live_ping_unknown_vendor_has_no_base():
    foo = Provider("foo", "foo", "B", ("FOO_API_KEY",), "", "council/foo", "x")
    res = init.live_ping(foo, {"FOO_API_KEY": "k"}, client=httpx.Client())
    assert res == init.PingResult(False, "no base url")


def test_live_ping_no_key():
    res = init.live_ping(_prov("groq"), {}, client=httpx.Client())
    assert res == init.PingResult(False, "no key")


def test_run_keeps_existing_master_and_unknown(tmp_path):
    p = tmp_path / ".env"
    env_file.write(p, {"LITELLM_MASTER_KEY": "sk-keepme", "WEIRD": "w"})
    rc = init.run(env_path=p, prompt=lambda _m: "", echo=lambda _l: None,
                  ping=lambda _p, _e: init.PingResult(True, "ok"))
    assert rc == 0
    saved = env_file.load(p)
    assert saved["LITELLM_MASTER_KEY"] == "sk-keepme"  # preserved, not regenerated
    assert saved["WEIRD"] == "w"  # unknown key preserved
