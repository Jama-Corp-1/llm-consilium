from __future__ import annotations

import asyncio
import functools
from collections.abc import Callable
from typing import Any

import httpx

from council.errors import MemberCallError
from council.types import AsyncCaller

_BACKOFF_DELAYS = (0.5, 1.0)
_MAX_RETRY_AFTER = 30.0


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
    timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
    recorder: Callable[[str, int], None] | None = None,
    max_retries: int = 2,
) -> str:
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
            return _extract(alias, resp.json(), recorder)
        if resp.status_code == 429:
            if attempt < max_retries:
                ra = _retry_after(resp)
                await asyncio.sleep(ra if ra is not None else _delay(attempt))
                attempt += 1
                continue
            raise MemberCallError(alias, "429 rate-limited")
        if resp.status_code >= 500:
            if attempt < max_retries:
                await asyncio.sleep(_delay(attempt))
                attempt += 1
                continue
            raise MemberCallError(alias, f"HTTP {resp.status_code}")
        detail = {401: "401 auth failed"}.get(resp.status_code, f"HTTP {resp.status_code}")
        raise MemberCallError(alias, detail)


def _extract(alias: str, data: Any, recorder: Callable[[str, int], None] | None) -> str:
    # A 200 means the provider served (and metered) the request, so record it before
    # validating the body — otherwise an empty/malformed 200 would silently under-count
    # usage against the daily cap.
    if recorder is not None:
        recorder(alias, _token_count(data))
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise MemberCallError(alias, "malformed response body") from exc
    if not isinstance(content, str) or not content.strip():
        raise MemberCallError(alias, "empty response content (finish_reason=length?)")
    return content


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
    timeout: float = 30.0,
) -> AsyncCaller:
    return functools.partial(
        complete,
        base_url,
        api_key,
        recorder=recorder,
        max_retries=max_retries,
        max_tokens=max_tokens,
        timeout=timeout,
    )
