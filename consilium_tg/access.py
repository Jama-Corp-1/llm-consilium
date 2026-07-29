from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path


class AccessStore:
    def __init__(self, path: str, owner_id: int | None = None) -> None:
        self._path = str(path)
        self._owner = owner_id

    def _load(self) -> dict[str, object]:
        try:
            with open(self._path, encoding="utf-8") as f:
                d: dict[str, object] = json.load(f)
        except (OSError, ValueError):
            d = {}
        if not isinstance(d, dict):
            d = {}
        # Coerce to the expected shapes so callers can narrow without asserts and a
        # malformed persisted file degrades to empty rather than crashing.
        d["allowed"] = d["allowed"] if isinstance(d.get("allowed"), list) else []
        d["pending"] = d["pending"] if isinstance(d.get("pending"), dict) else {}
        return d

    @staticmethod
    def _allowed(d: dict[str, object]) -> list[object]:
        v = d["allowed"]
        return v if isinstance(v, list) else []

    @staticmethod
    def _pending(d: dict[str, object]) -> dict[str, object]:
        v = d["pending"]
        return v if isinstance(v, dict) else {}

    def _save(self, d: dict[str, object]) -> None:
        try:
            parent = Path(self._path).parent
            parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=str(parent), prefix=".tgacc-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(d, f)
            os.replace(tmp, self._path)
        except OSError:
            pass

    def owner_id(self) -> int | None:
        return self._owner

    def is_owner(self, uid: int | str) -> bool:
        return self._owner is not None and int(uid) == int(self._owner)

    def is_allowed(self, uid: int | str) -> bool:
        allowed = self._allowed(self._load())
        return self.is_owner(uid) or int(uid) in allowed

    def request_access(self, uid: int | str, username: str) -> None:
        d = self._load()
        self._pending(d)[str(int(uid))] = username or ""
        self._save(d)

    def list_pending(self) -> dict[str, str]:
        pending = self._pending(self._load())
        return {str(k): str(v) for k, v in pending.items()}

    def approve(self, uid: int | str) -> bool:
        d = self._load()
        pending, allowed = self._pending(d), self._allowed(d)
        pending.pop(str(int(uid)), None)
        if int(uid) not in allowed:
            allowed.append(int(uid))
        self._save(d)
        return True

    def deny(self, uid: int | str) -> bool:
        d = self._load()
        self._pending(d).pop(str(int(uid)), None)
        self._save(d)
        return True
