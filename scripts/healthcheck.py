#!/usr/bin/env python3
"""Live health-check for the Consilium LiteLLM proxy.

Probes GET /v1/models and sends a 1-token completion to one alias per Tier-A
provider. Prints per-probe PASS/FAIL and exits non-zero if any probe fails.
Reads no secret except the proxy master key (LITELLM_MASTER_KEY).
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:4000/v1"
REQUEST_TIMEOUT = 30.0

EXPECTED_ALIASES = (
    "council/groq-gpt-oss-120b",
    "council/groq-gpt-oss-20b",
    "council/groq-qwen3.8-27b",
    "council/cloudflare-gpt-oss-120b",
    "council/cloudflare-qwen3.8-27b",
    "council/cloudflare-llama-70b",
)

# One representative alias per provider (proves that key + route work end-to-end).
PROVIDER_PROBES = {
    "groq": "council/groq-gpt-oss-20b",
    "cloudflare": "council/cloudflare-llama-70b",
}


@dataclass(frozen=True)
class ProbeResult:
    name: str
    ok: bool
    detail: str


def check_models(returned: set[str], expected: tuple[str, ...]) -> ProbeResult:
    missing = [a for a in expected if a not in returned]
    if missing:
        return ProbeResult("models", False, f"missing aliases: {', '.join(missing)}")
    return ProbeResult("models", True, f"{len(expected)} aliases present")


def classify_completion(name: str, status_code: int | None, error: str | None) -> ProbeResult:
    if error is not None:
        return ProbeResult(name, False, error)
    if status_code == 200:
        return ProbeResult(name, True, "completion ok")
    if status_code == 401:
        return ProbeResult(name, False, "401 auth failed (check API key)")
    if status_code == 429:
        return ProbeResult(name, False, "429 rate-limited")
    return ProbeResult(name, False, f"HTTP {status_code}")


def summarize(results: list[ProbeResult]) -> int:
    all_ok = True
    for r in results:
        print(f"[{'PASS' if r.ok else 'FAIL'}] {r.name}: {r.detail}")
        all_ok = all_ok and r.ok
    print("---")
    print("OK: all probes passed" if all_ok else "FAILED: one or more probes failed")
    return 0 if all_ok else 1


def _probe_models(client: httpx.Client) -> ProbeResult:
    try:
        resp = client.get("/models")
    except httpx.HTTPError as exc:
        return ProbeResult("models", False, f"request error: {exc.__class__.__name__}")
    if resp.status_code != 200:
        return ProbeResult("models", False, f"HTTP {resp.status_code}")
    returned = {m.get("id") for m in resp.json().get("data", [])}
    return check_models(returned, EXPECTED_ALIASES)


def _probe_completion(client: httpx.Client, name: str, alias: str) -> ProbeResult:
    payload = {
        "model": alias,
        "messages": [{"role": "user", "content": "ping"}],
        "max_tokens": 1,
    }
    try:
        resp = client.post("/chat/completions", json=payload)
    except httpx.TimeoutException:
        return classify_completion(name, None, "timeout")
    except httpx.HTTPError as exc:
        return classify_completion(name, None, f"request error: {exc.__class__.__name__}")
    return classify_completion(name, resp.status_code, None)


def run_probes(base_url: str, api_key: str) -> list[ProbeResult]:
    headers = {"Authorization": f"Bearer {api_key}"}
    with httpx.Client(base_url=base_url, headers=headers, timeout=REQUEST_TIMEOUT) as client:
        results = [_probe_models(client)]
        for name, alias in PROVIDER_PROBES.items():
            results.append(_probe_completion(client, name, alias))
    return results


def main() -> int:
    base_url = os.environ.get("CONSILIUM_BASE_URL", DEFAULT_BASE_URL)
    api_key = os.environ.get("LITELLM_MASTER_KEY", "")
    if not api_key:
        print("ERROR: LITELLM_MASTER_KEY not set", file=sys.stderr)
        return 2
    return summarize(run_probes(base_url, api_key))


if __name__ == "__main__":
    raise SystemExit(main())
