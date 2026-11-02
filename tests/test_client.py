import asyncio
import functools

import httpx
import pytest

from council import client
from council.errors import MemberCallError, ProxyError
from council.types import CallResult


def _transport(status, body=None, headers=None):
    def handler(request):
        if isinstance(body, str):
            return httpx.Response(status, text=body, headers=headers)
        return httpx.Response(status, json=body or {}, headers=headers)

    return httpx.MockTransport(handler)


def _ok_body(model=None):
    body = {"choices": [{"message": {"content": "ok"}}]}
    if model is not None:
        body["model"] = model
    return body


def test_complete_returns_answer_on_200():
    body = {"choices": [{"message": {"content": "hello"}}]}
    out = asyncio.run(
        client.complete("http://x/v1", "k", "council/a", "hi", transport=_transport(200, body))
    )
    assert out.content == "hello"


@pytest.mark.parametrize(
    "status,body,frag",
    [
        (401, "GroqException - Invalid API Key", "401"),
        (429, None, "429"),
        (500, None, "HTTP 500"),
    ],
)
def test_complete_maps_errors(status, body, frag):
    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi",
                transport=_transport(status, body), max_retries=0,
            )
        )
    assert frag in ei.value.detail


def test_complete_maps_timeout():
    def handler(request):
        raise httpx.TimeoutException("slow")

    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi",
                transport=httpx.MockTransport(handler), max_retries=0,
            )
        )
    assert ei.value.detail == "timeout"


def test_complete_maps_transport_error():
    def handler(request):
        raise httpx.ConnectError("boom")

    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi", transport=httpx.MockTransport(handler)
            )
        )
    assert ei.value.detail.startswith("request error")


def test_complete_malformed_200_body_raises_member_call_error():
    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi", transport=_transport(200, {"unexpected": 1})
            )
        )
    assert "malformed" in ei.value.detail


def test_complete_null_content_raises_member_call_error():
    body = {"choices": [{"message": {"content": None}}]}
    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete("http://x/v1", "k", "council/a", "hi", transport=_transport(200, body))
        )
    assert "content" in ei.value.detail


def test_complete_empty_content_raises_member_call_error():
    body = {"choices": [{"message": {"content": "   "}}]}
    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete("http://x/v1", "k", "council/a", "hi", transport=_transport(200, body))
        )
    assert "content" in ei.value.detail


def test_make_caller_binds_base_and_key():
    caller = client.make_caller("http://x/v1", "k")
    assert isinstance(caller, functools.partial)
    assert caller.args == ("http://x/v1", "k")


def test_backoff_retries_5xx_then_succeeds():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    out = asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi",
            transport=httpx.MockTransport(handler), max_retries=2,
        )
    )
    assert out.content == "ok" and calls["n"] == 2


def test_backoff_retries_429_then_succeeds(monkeypatch):
    monkeypatch.setattr(client, "_delay", lambda attempt: 0.0)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    out = asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi",
            transport=httpx.MockTransport(handler), max_retries=2,
        )
    )
    assert out.content == "ok" and calls["n"] == 2


def test_429_exhausted_raises_after_retries(monkeypatch):
    monkeypatch.setattr(client, "_delay", lambda attempt: 0.0)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(429, json={})

    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi",
                transport=httpx.MockTransport(handler), max_retries=2,
            )
        )
    assert calls["n"] == 3 and "429" in ei.value.detail


def test_429_honors_retry_after_header_capped(monkeypatch):
    slept = []

    async def fake_sleep(secs):
        slept.append(secs)

    monkeypatch.setattr(client.asyncio, "sleep", fake_sleep)
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(429, headers={"Retry-After": "120"}, json={})
        return httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})

    out = asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi",
            transport=httpx.MockTransport(handler), max_retries=2,
        )
    )
    assert out.content == "ok" and slept == [30.0]  # 120 capped to 30


def test_empty_200_records_request_with_zero_tokens():
    seen = []
    body = {"choices": [{"message": {"content": "   "}}]}  # served 200, empty content, no usage

    with pytest.raises(MemberCallError):
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi",
                transport=_transport(200, body),
                recorder=lambda a, t: seen.append((a, t)),
            )
        )
    assert seen == [("council/a", 0)]  # request counted despite unusable content


def test_recorder_receives_total_tokens():
    seen = []

    def handler(request):
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": 42}},
        )

    asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi",
            transport=httpx.MockTransport(handler), recorder=lambda a, t: seen.append((a, t)),
        )
    )
    assert seen == [("council/a", 42)]


def test_non_numeric_total_tokens_degrades_to_zero():
    seen = []
    body = {"choices": [{"message": {"content": "ok"}}], "usage": {"total_tokens": "abc"}}
    out = asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi",
            transport=_transport(200, body),
            recorder=lambda a, t: seen.append((a, t)),
        )
    )
    assert out.content == "ok" and seen == [("council/a", 0)]  # bad token count -> 0, no crash


def test_classify_error_table():
    auth_body = '{"error":{"message":"Authentication Error, invalid key"}}'
    assert client.classify_error(401, auth_body) is None
    assert client.classify_error(403, '{"error":{"type":"auth_error"}}') is None
    groq_401 = "GroqException - Invalid API Key"
    assert client.classify_error(401, groq_401) == ("401 auth failed", True)
    assert client.classify_error(402, "CerebrasException - Payment required")[1] is True
    assert client.classify_error(404, "GroqException - model does not exist")[1] is True
    assert client.classify_error(400, "OpenAIException - Model zai-glm-4.7 is archived")[1] is True
    assert client.classify_error(400, "openaiexception - MODEL NOT FOUND")[1] is True
    assert client.classify_error(400, "OpenAIException - bad request")[1] is False
    assert client.classify_error(400, "model does not exist")[1] is False  # hint without marker
    assert client.classify_error(429, "whatever") == ("429 rate-limited", False)
    assert client.classify_error(503, "x") == ("HTTP 503", False)
    assert client.classify_error(422, "x") == ("HTTP 422", False)


def test_proxy_401_raises_proxy_error():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(
            401,
            json={"error": {"message": "Authentication Error, bad key", "type": "auth_error"}},
        )

    with pytest.raises(ProxyError):
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi", transport=httpx.MockTransport(handler)
            )
        )
    assert calls["n"] == 1  # no retry


def test_provider_404_is_permanent_member_error_without_retry():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, text="GroqException - The model `x` does not exist")

    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi",
                transport=httpx.MockTransport(handler), max_retries=2,
            )
        )
    assert calls["n"] == 1
    assert ei.value.permanent is True
    assert ei.value.alias == "council/a"


def test_transient_errors_are_not_permanent():
    with pytest.raises(MemberCallError) as ei:
        asyncio.run(
            client.complete(
                "http://x/v1", "k", "council/a", "hi", transport=_transport(500), max_retries=0
            )
        )
    assert ei.value.permanent is False


def test_served_alias_from_resolver_and_recorder_gets_served():
    seen = []
    args = []

    def resolve(req, body_model, api_base):
        args.append((req, body_model, api_base))
        return "council/served"

    out = asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi",
            recorder=lambda a, t: seen.append(a),
            resolve=resolve,
            transport=_transport(
                200,
                _ok_body("openai/gpt-oss-120b"),
                headers={
                    "x-litellm-attempted-fallbacks": "1",
                    "x-litellm-model-api-base": "https://api.groq.com/openai/v1",
                },
            ),
        )
    )
    assert out == CallResult("ok", "council/served", 1)
    assert seen == ["council/served"]
    assert args == [("council/a", "openai/gpt-oss-120b", "https://api.groq.com/openai/v1")]


def test_default_served_alias_is_requested():
    out = asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi", transport=_transport(200, _ok_body())
        )
    )
    assert out == CallResult("ok", "council/a", 0)


def test_non_numeric_fallbacks_header_degrades_to_zero():
    out = asyncio.run(
        client.complete(
            "http://x/v1", "k", "council/a", "hi",
            transport=_transport(200, _ok_body(), headers={"x-litellm-attempted-fallbacks": "x"}),
        )
    )
    assert out.fallbacks == 0


def test_make_caller_forwards_resolver():
    def resolve(req, body_model, api_base):
        return "council/b"

    caller = client.make_caller("http://x/v1", "k", resolve=resolve)
    assert caller.keywords["resolve"] is resolve
