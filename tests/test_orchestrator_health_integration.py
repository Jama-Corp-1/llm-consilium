"""End-to-end: real client.complete over httpx.MockTransport + real HealthStore."""

import asyncio
import functools
import json

import httpx
import pytest

from council import client, registry
from council.errors import ProxyError
from council.health import HealthStore
from council.orchestrator import DEFAULT_CONFIG_PATH, Orchestrator

GROQ = "council/groq-gpt-oss-120b"
CF = "council/cloudflare-llama-70b"
QWEN = "council/groq-qwen3.8-27b"


def _members():
    by_alias = {m.alias: m for m in registry.load_members(DEFAULT_CONFIG_PATH)}
    return [by_alias[GROQ], by_alias[CF], by_alias[QWEN]]


def _orch(tmp_path, handler, log):
    def recording(request):
        model = json.loads(request.content)["model"]
        log.append(model)
        return handler(model)

    members = _members()
    caller = functools.partial(
        client.complete, "http://proxy/v1", "k",
        transport=httpx.MockTransport(recording), max_retries=0,
        resolve=functools.partial(registry.served_alias, members, env={}),
    )
    health = HealthStore(tmp_path / "health.json")
    return Orchestrator(members, caller, health=health), health


def _ok(model):
    return httpx.Response(200, json={"model": model, "choices": [{"message": {"content": "ok"}}]})


def test_provider_404_drops_then_excludes_without_calling(tmp_path):
    def handler(model):
        if model == GROQ:
            return httpx.Response(
                404, text='{"error":{"message":"GroqException - model does not exist"}}'
            )
        return _ok(model)

    log: list[str] = []
    o, health = _orch(tmp_path, handler, log)
    first = asyncio.run(o.council("2+2?", members=[GROQ, CF, QWEN], mode="vote"))
    groq = next(a for a in first.per_member if a.alias == GROQ)
    assert groq.ok is False and groq.permanent is True
    assert health.status_of(GROQ)[0] == "down"
    assert GROQ in log

    log.clear()
    second = asyncio.run(o.council("2+2?", members=[GROQ, CF, QWEN], mode="vote"))
    assert "dropped council/groq-gpt-oss-120b (down:" in second.note
    assert GROQ not in log
    assert {a.alias for a in second.per_member} == {CF, QWEN}


def test_proxy_401_raises_proxy_error_no_marks(tmp_path):
    def handler(model):
        return httpx.Response(
            401, text='{"error":{"message":"Authentication Error, Invalid proxy server token"}}'
        )

    log: list[str] = []
    o, health = _orch(tmp_path, handler, log)
    with pytest.raises(ProxyError):
        asyncio.run(o.council("2+2?", members=[GROQ, CF, QWEN], mode="vote"))
    with pytest.raises(ProxyError):
        asyncio.run(o.ask("2+2?", model=CF))
    assert health.snapshot() == {}
