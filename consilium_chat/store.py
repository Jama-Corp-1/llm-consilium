from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS threads (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "title TEXT NOT NULL, created_at TEXT NOT NULL);"
    "CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY AUTOINCREMENT, "
    "thread_id INTEGER NOT NULL, role TEXT NOT NULL, content TEXT NOT NULL, "
    "meta TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL);"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ChatStore:
    def __init__(self, db_path: str | Path) -> None:
        self._path = str(db_path)
        try:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as c, c:
                c.executescript(_SCHEMA)
        except (sqlite3.Error, OSError):
            pass  # degrade to no-op like UsageStore

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5)
        conn.row_factory = sqlite3.Row
        return conn

    def create_thread(self, title: str) -> int:
        try:
            with closing(self._connect()) as c, c:
                cur = c.execute("INSERT INTO threads (title, created_at) VALUES (?, ?)",
                                (title, _now()))
                return cur.lastrowid if cur.lastrowid is not None else -1
        except (sqlite3.Error, OSError):
            return -1

    def list_threads(self) -> list[dict[str, Any]]:
        try:
            with closing(self._connect()) as c:
                rows = c.execute(
                    "SELECT id, title, created_at FROM threads ORDER BY id DESC").fetchall()
        except (sqlite3.Error, OSError):
            return []
        return [dict(r) for r in rows]

    def get_messages(self, thread_id: int) -> list[dict[str, Any]]:
        try:
            with closing(self._connect()) as c:
                rows = c.execute(
                    "SELECT id, role, content, meta, created_at FROM messages "
                    "WHERE thread_id = ? ORDER BY id ASC", (thread_id,)).fetchall()
        except (sqlite3.Error, OSError):
            return []
        out: list[dict[str, Any]] = []
        for r in rows:
            d = dict(r)
            try:
                d["meta"] = json.loads(d["meta"] or "{}")
            except (ValueError, TypeError):
                d["meta"] = {}
            out.append(d)
        return out

    def add_message(
        self, thread_id: int, role: str, content: str,
        meta: dict[str, Any] | None = None,
    ) -> int:
        try:
            with closing(self._connect()) as c, c:
                cur = c.execute(
                    "INSERT INTO messages (thread_id, role, content, meta, created_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (thread_id, role, content, json.dumps(meta or {}), _now()))
                return cur.lastrowid if cur.lastrowid is not None else -1
        except (sqlite3.Error, OSError):
            return -1

    def rename_thread(self, thread_id: int, title: str) -> None:
        try:
            with closing(self._connect()) as c, c:
                c.execute("UPDATE threads SET title = ? WHERE id = ?", (title, thread_id))
        except (sqlite3.Error, OSError):
            pass

    def delete_thread(self, thread_id: int) -> None:
        try:
            with closing(self._connect()) as c, c:
                c.execute("DELETE FROM messages WHERE thread_id = ?", (thread_id,))
                c.execute("DELETE FROM threads WHERE id = ?", (thread_id,))
        except (sqlite3.Error, OSError):
            pass
