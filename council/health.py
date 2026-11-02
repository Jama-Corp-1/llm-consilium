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
            handle = open(self._path.with_name(self._path.name + ".lock"), "a")
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
        except OSError:
            pass  # never crash a call on telemetry

    # -- helpers -------------------------------------------------------

    def _is_down(self, rec: Record, now: datetime) -> bool:
        if rec.get("status") != "down":
            return False
        until = rec.get("until")
        if until is None:
            return True
        try:
            return datetime.fromisoformat(str(until)) > now
        except ValueError:
            return False  # malformed expiry must not block a member forever

    # -- mutations -----------------------------------------------------

    def mark_down(self, alias: str, reason: str, *, ttl_s: float, source: str = "call") -> None:
        now = self._now()

        def apply(rec: Record) -> None:
            rec.update(
                status="down",
                reason=reason,
                since=now.isoformat(),
                until=(now + timedelta(seconds=ttl_s)).isoformat(),
                source=source,
            )

        self._update(alias, apply)

    def mark_up(self, alias: str, *, source: str = "call") -> None:
        now = self._now()

        def apply(rec: Record) -> None:
            rec.update(status="up", reason="", since=now.isoformat(), until=None, source=source)

        self._update(alias, apply)

    def note_probe(self, alias: str, status: str, reason: str) -> None:
        now = self._now()

        def apply(rec: Record) -> None:
            rec["last_probe"] = {"status": status, "reason": reason, "at": now.isoformat()}

        self._update(alias, apply)

    # -- queries -------------------------------------------------------

    def snapshot(self) -> dict[str, dict[str, object]]:
        return copy.deepcopy(self._read())

    def down_aliases(self) -> set[str]:
        now = self._now()
        return {a for a, rec in self._read().items() if self._is_down(rec, now)}

    def status_of(self, alias: str) -> tuple[str, str]:
        rec = self._read().get(alias)
        if rec is None:
            return ("unknown", "")
        status = rec.get("status")
        if status == "down":
            if self._is_down(rec, self._now()):
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
            if not isinstance(at, str):
                continue
            try:
                stamps.append((datetime.fromisoformat(at), at))
            except ValueError:
                continue
        return max(stamps)[1] if stamps else None
