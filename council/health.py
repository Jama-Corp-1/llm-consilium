from __future__ import annotations

import copy
import json
import os
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:
    import fcntl
except ImportError:  # pragma: no cover - non-POSIX platform
    fcntl = None  # type: ignore[assignment]

DEFAULT_HEALTH_PATH = Path.home() / ".config" / "consilium" / "health.json"
CALL_TTL_S = 3600
PROBE_TTL_S = 86400

Record = dict[str, object]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


def _parse(value: object) -> datetime | None:
    """Parse an ISO-8601 string to an aware UTC datetime; None if it is not one."""
    if not isinstance(value, str):
        return None
    try:
        return _aware(datetime.fromisoformat(value))
    except (ValueError, TypeError):
        return None


class HealthStore:
    """Shared, best-effort JSON store of per-alias member health.

    Several processes (MCP, web, bot, probe) read and write the same file, so every public
    call re-reads it and every mutation is a locked read-modify-write with an atomic replace.
    I/O problems degrade to "no data"; they never raise.
    """

    def __init__(
        self,
        path: str | Path = DEFAULT_HEALTH_PATH,
        *,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self._path = Path(path)
        self._now = now or _utcnow

    # -- file plumbing -------------------------------------------------

    def _read(self) -> dict[str, Record]:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        if not isinstance(data, dict):
            return {}
        return {str(k): v for k, v in data.items() if isinstance(v, dict)}

    def _write(self, data: dict[str, Record]) -> None:
        tmp: str | None = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix="health-", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, indent=2)
            os.replace(tmp, self._path)
            tmp = None
        except (OSError, ValueError):
            pass  # best-effort; a broken store degrades to no health data
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass

    @contextmanager
    def _lock(self) -> Iterator[None]:
        if fcntl is None:
            yield
            return
        handle = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            handle = open(self._path.with_name(self._path.name + ".lock"), "a", encoding="utf-8")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except OSError:
            if handle is not None:
                handle.close()
            handle = None  # proceed unlocked rather than lose the update
        try:
            yield
        finally:
            if handle is not None:
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
                handle.close()

    def _update(self, alias: str, apply: Callable[[Record], None]) -> None:
        try:
            with self._lock():
                data = self._read()
                rec = data.get(alias, {})
                apply(rec)
                data[alias] = rec
                self._write(data)
        except (OSError, ValueError, OverflowError, TypeError):
            pass  # never crash a call on telemetry

    # -- helpers -------------------------------------------------------

    def _clock(self) -> datetime:
        """Current time as an aware UTC datetime (a naive injected clock is read as UTC)."""
        return _aware(self._now())

    def _is_down(self, rec: Record, now: datetime) -> bool:
        if rec.get("status") != "down":
            return False
        until = rec.get("until")
        if until is None:
            return True
        parsed = _parse(until)
        return parsed is not None and parsed > now  # malformed expiry never blocks forever

    # -- mutations -----------------------------------------------------

    def mark_down(self, alias: str, reason: str, *, ttl_s: float, source: str = "call") -> None:
        now = self._clock()
        try:
            until = (now + timedelta(seconds=ttl_s)).isoformat()
        except (ValueError, OverflowError, TypeError):
            # nan/inf/huge/non-numeric ttl: fall back to the default call ttl, never "forever"
            until = (now + timedelta(seconds=CALL_TTL_S)).isoformat()

        def apply(rec: Record) -> None:
            since = now.isoformat()
            if self._is_down(rec, now) and isinstance(rec.get("since"), str):
                since = str(rec["since"])  # "down since" = first failure of this streak
            rec.update(status="down", reason=reason, since=since, until=until, source=source)

        self._update(alias, apply)

    def mark_up(self, alias: str, *, source: str = "call") -> None:
        now = self._clock()

        def apply(rec: Record) -> None:
            rec.update(status="up", reason="", since=now.isoformat(), until=None, source=source)

        self._update(alias, apply)

    def note_probe(self, alias: str, status: str, reason: str) -> None:
        now = self._clock()

        def apply(rec: Record) -> None:
            rec["last_probe"] = {"status": status, "reason": reason, "at": now.isoformat()}

        self._update(alias, apply)

    # -- queries -------------------------------------------------------

    def snapshot(self) -> dict[str, dict[str, object]]:
        return copy.deepcopy(self._read())

    def down_aliases(self) -> set[str]:
        now = self._clock()
        return {a for a, rec in self._read().items() if self._is_down(rec, now)}

    def status_of(self, alias: str) -> tuple[str, str]:
        rec = self._read().get(alias)
        if rec is None:
            return ("unknown", "")
        status = rec.get("status")
        if status == "down":
            if self._is_down(rec, self._clock()):
                return ("down", str(rec.get("reason", "")))
            return ("unknown", "")
        if status == "up":
            return ("up", str(rec.get("reason", "")))
        return ("unknown", "")

    def probed_at(self) -> str | None:
        stamps: list[tuple[datetime, str]] = []
        for rec in self._read().values():
            probe = rec.get("last_probe")
            if not isinstance(probe, dict):
                continue
            at = probe.get("at")
            parsed = _parse(at)
            if isinstance(at, str) and parsed is not None:
                stamps.append((parsed, at))
        return max(stamps)[1] if stamps else None
