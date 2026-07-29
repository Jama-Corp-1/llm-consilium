from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_LOG_PATH = Path.home() / ".config" / "consilium" / "runs.jsonl"
_CONTENT_KEYS = ("prompt", "answer")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact(entry: dict[str, object]) -> dict[str, object]:
    e = dict(entry)
    for key in _CONTENT_KEYS:
        value = e.get(key)
        if isinstance(value, str):
            e[f"{key}_len"] = len(value)
            e[key] = None
    members = e.get("per_member")
    if isinstance(members, list):
        redacted = []
        for m in members:
            m2 = dict(m)
            if isinstance(m2.get("answer"), str):
                m2["answer_len"] = len(m2["answer"])
                m2["answer"] = None
            redacted.append(m2)
        e["per_member"] = redacted
    return e


class RunLog:
    """Best-effort append-only JSON-lines log of council runs (opt-in via CONSILIUM_LOG=1).

    Content (prompt/answers) is written only when `redact` is False (all-Tier-A run);
    otherwise text is replaced by its length. Logging never raises into a call.
    """

    def __init__(self, path: str | Path = DEFAULT_LOG_PATH, *, enabled: bool | None = None) -> None:
        self._path = Path(path)
        self._enabled = (os.environ.get("CONSILIUM_LOG") == "1") if enabled is None else enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    def record(self, entry: dict[str, object], *, redact: bool) -> None:
        if not self._enabled:
            return
        payload = _redact(entry) if redact else dict(entry)
        payload["redacted"] = redact
        payload.setdefault("ts", _now())
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        except OSError:
            pass  # best-effort; logging must never break a council call
