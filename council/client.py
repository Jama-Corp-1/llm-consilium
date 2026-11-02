from __future__ import annotations

import asyncio
import functools
import re
from collections.abc import Callable
from typing import Any

import httpx

from council.errors import MemberCallError, ProxyError
from council.types import CallResult, DetailedCaller

# (requested_alias, body_model, api_base_header) -> alias that actually served the call
Resolver = Callable[[str, str | None, str | None], str]

_BACKOFF_DELAYS = (0.5, 1.0)
_MAX_RETRY_AFTER = 30.0

# LiteLLM wraps provider failures as "<Provider>Exception - ..."; a proxy-level
# rejection (e.g. bad master key) carries no such marker. Case-sensitive on purpose:
# LiteLLM exception class names are capitalised ("GroqException").
PROVIDER_MARKER = re.compile(r"\b[A-Za-z_]+Exception\b")
MODEL_GONE_HINTS = (
    "not found",
    "does not exist",
    "decommissioned",
    "archived",
    "unknown model",
    "no longer",
)
_PERMANENT_DETAILS = {
    401: "401 auth failed",
    402: "402 payment required",
    403: "403 forbidden",
    404: "404 model not found",
}


def classify_error(status: int, body: str) -> tuple[str, bool] | None:
    """Classify a non-200 proxy response from its status and raw body text.

    Returns ``None`` when the proxy itself rejected the request (401/403 without a
    provider exception marker) -- the caller must raise ``ProxyError``. Otherwise
    returns ``(detail, permanent)`` for a ``MemberCallError``.
    """
    has_marker = PROVIDER_MARKER.search(body) is not None
    if status in (401, 403) and not has_marker:
        return None
    if status in _PERMANENT_DETAILS and has_marker:
        return (_PERMANENT_DETAILS[status], True)
    if status == 400 and has_marker:
        lowered = body.lower()
        if any(hint in lowered for hint in MODEL_GONE_HINTS):
            return ("400 model unavailable", True)
    if status == 429:
        return ("429 rate-limited", False)
    return (f"HTTP {status}", False)


def _delay(attempt: int) -> float:
    return _BACKOFF_DELAYS[min(attempt, len(_BACKOFF_DELAYS) - 1)]


def _retry_after(resp: httpx.Response) -> float | None:
    raw = resp.headers.get("Retry-After")
    if raw is None:
        return None
    try:
        secs = float(int(raw))
    except (TypeError, ValueError):
        return None  # HTTP-date form or garbage -> fall back to exponential backoff
    return min(max(secs, 0.0), _MAX_RETRY_AFTER)


async def complete(
    base_url: str,
    api_key: str,
    alias: str,
    prompt: str,
    *,
    max_tokens: int = 2048,
    timeout: float = 120.0,
    transport: httpx.AsyncBaseTransport | None = None,
    recorder: Callable[[str, int], None] | None = None,
    max_retries: int = 2,
    resolve: Resolver | None = None,
) -> CallResult:
    headers = {"Authorization": f"Bearer {api_key}"}
    payload = {
        "model": alias,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": max_tokens,
    }
    attempt = 0
    while True:
        try:
            async with httpx.AsyncClient(
                base_url=base_url, headers=headers, timeout=timeout, transport=transport
            ) as http:
                resp = await http.post("/chat/completions", json=payload)
        except httpx.TimeoutException as exc:
            if attempt < max_retries:
                await asyncio.sleep(_delay(attempt))
                attempt += 1
                continue
            raise MemberCallError(alias, "timeout") from exc
        except httpx.HTTPError as exc:
            raise MemberCallError(alias, f"request error: {exc.__class__.__name__}") from exc

        if resp.status_code == 200:
            return _extract(alias, resp, recorder, resolve)
        if resp.status_code == 429:
            if attempt < max_retries:
                ra = _retry_after(resp)
                await asyncio.sleep(ra if ra is not None else _delay(attempt))
                attempt += 1
                continue
        elif resp.status_code >= 500:
            if attempt < max_retries:
                await asyncio.sleep(_delay(attempt))
                attempt += 1
                continue
        classified = classify_error(resp.status_code, resp.text)
        if classified is None:
            raise ProxyError(f"proxy rejected the request: HTTP {resp.status_code}")
        detail, permanent = classified
        raise MemberCallError(alias, detail, permanent=permanent)


def _extract(
    alias: str,
    resp: httpx.Response,
    recorder: Callable[[str, int], None] | None,
    resolve: Resolver | None,
) -> CallResult:
    try:
        data: Any = resp.json()
    except ValueError:
        data = None
    served = alias
    if resolve is not None:
        body_model = data.get("model") if isinstance(data, dict) else None
        try:
            served = resolve(
                alias,
                body_model if isinstance(body_model, str) else None,
                resp.headers.get("x-litellm-model-api-base"),
            )
        except Exception:  # noqa: BLE001 - attribution is telemetry; never fail a served call
            served = alias
    # A 200 means the provider served (and metered) the request, so record it before
    # validating the body — otherwise an empty/malformed 200 would silently under-count
    # usage against the daily cap. Usage is charged to the alias that actually served.
    if recorder is not None:
        recorder(served, _token_count(data))
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise MemberCallError(alias, "malformed response body") from exc
    if not isinstance(content, str) or not content.strip():
        raise MemberCallError(alias, "empty response content (finish_reason=length?)")
    return CallResult(content, served, _fallbacks(resp))


def _fallbacks(resp: httpx.Response) -> int:
    try:
        return int(resp.headers.get("x-litellm-attempted-fallbacks", "0") or 0)
    except (TypeError, ValueError):
        return 0


def _token_count(data: Any) -> int:
    usage = data.get("usage") if isinstance(data, dict) else None
    if isinstance(usage, dict):
        try:
            return int(usage.get("total_tokens") or 0)
        except (TypeError, ValueError):
            return 0
    return 0


def make_caller(
    base_url: str,
    api_key: str,
    *,
    recorder: Callable[[str, int], None] | None = None,
    max_retries: int = 2,
    max_tokens: int = 2048,
    timeout: float = 120.0,
    resolve: Resolver | None = None,
) -> DetailedCaller:
    return functools.partial(
        complete,
        base_url,
        api_key,
        recorder=recorder,
        max_retries=max_retries,
        max_tokens=max_tokens,
        timeout=timeout,
        resolve=resolve,
    )
