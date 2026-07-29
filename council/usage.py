from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path

from council.types import Member

DEFAULT_DB_PATH = Path.home() / ".config" / "consilium" / "usage.db"

_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS usage ("
    "alias TEXT NOT NULL, day TEXT NOT NULL, "
    "requests INTEGER NOT NULL DEFAULT 0, tokens INTEGER NOT NULL DEFAULT 0, "
    "PRIMARY KEY (alias, day))"
)


def today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


class UsageStore:
    def __init__(self, db_path: str | Path = DEFAULT_DB_PATH) -> None:
        self._path = str(db_path)
        try:
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
            with closing(self._connect()) as conn, conn:
                conn.execute(_SCHEMA)
        except (sqlite3.Error, OSError):
            pass  # best-effort; a broken store degrades to no telemetry

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._path, timeout=5)
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def record(self, alias: str, tokens: int, *, day: str | None = None) -> None:
        d = day or today()
        try:
            with closing(self._connect()) as conn, conn:
                conn.execute(
                    "INSERT INTO usage (alias, day, requests, tokens) VALUES (?, ?, 1, ?) "
                    "ON CONFLICT(alias, day) DO UPDATE SET "
                    "requests = requests + 1, tokens = tokens + excluded.tokens",
                    (alias, d, int(tokens)),
                )
        except (sqlite3.Error, OSError):
            pass  # never crash a call on telemetry

    def counts(self, day: str | None = None) -> dict[str, tuple[int, int]]:
        d = day or today()
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT alias, requests, tokens FROM usage WHERE day = ?", (d,)
                ).fetchall()
        except (sqlite3.Error, OSError):
            return {}
        return {alias: (req, tok) for alias, req, tok in rows}

    def history(self, days: int = 7, *, end_day: str | None = None) -> list[dict[str, object]]:
        end = end_day or today()
        start = (
            datetime.strptime(end, "%Y-%m-%d") - timedelta(days=max(days - 1, 0))
        ).strftime("%Y-%m-%d")
        try:
            with closing(self._connect()) as conn:
                rows = conn.execute(
                    "SELECT day, alias, requests, tokens FROM usage "
                    "WHERE day >= ? AND day <= ? ORDER BY day DESC, alias",
                    (start, end),
                ).fetchall()
        except (sqlite3.Error, OSError):
            return []
        return [
            {"day": d, "alias": a, "requests": req, "tokens": tok}
            for d, a, req, tok in rows
        ]


def _exhausted(m: Member, counts: dict[str, tuple[int, int]]) -> bool:
    req, tok = counts.get(m.alias, (0, 0))
    return (m.rpd is not None and req >= m.rpd) or (m.tpd is not None and tok >= m.tpd)


def available(members: list[Member], counts: dict[str, tuple[int, int]]) -> list[Member]:
    return [m for m in members if not _exhausted(m, counts)]


def summary(members: list[Member], counts: dict[str, tuple[int, int]]) -> list[dict[str, object]]:
    rows = []
    for m in members:
        req, tok = counts.get(m.alias, (0, 0))
        rows.append(
            {
                "alias": m.alias,
                "tier": m.privacy_tier,
                "requests": req,
                "tokens": tok,
                "rpd": m.rpd,
                "tpd": m.tpd,
                "exhausted": _exhausted(m, counts),
                "cost_usd": round(tok / 1000 * m.cost_per_1k, 6),
            }
        )
    return rows
