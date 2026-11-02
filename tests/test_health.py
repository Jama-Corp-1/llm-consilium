from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from council import health
from council.health import HealthStore

T0 = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


class Clock:
    def __init__(self) -> None:
        self.t = T0

    def __call__(self) -> datetime:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t = self.t + timedelta(seconds=seconds)


def test_mark_down_then_down_aliases(tmp_path: Path) -> None:
    store = HealthStore(tmp_path / "health.json")
    store.mark_down("council/a", "402 payment required", ttl_s=3600)
    assert store.down_aliases() == {"council/a"}
    assert store.status_of("council/a") == ("down", "402 payment required")
    rec = store.snapshot()["council/a"]
    assert rec["status"] == "down" and rec["source"] == "call"
    assert rec["until"] is not None and rec["since"]


def test_ttl_expiry_clears_down(tmp_path: Path) -> None:
    clock = Clock()
    store = HealthStore(tmp_path / "health.json", now=clock)
    store.mark_down("council/a", "boom", ttl_s=60)
    assert store.down_aliases() == {"council/a"}
    clock.advance(59)
    assert store.down_aliases() == {"council/a"}
    clock.advance(2)
    assert store.down_aliases() == set()
    assert store.status_of("council/a") == ("unknown", "")


def test_mark_up_overrides_down(tmp_path: Path) -> None:
    store = HealthStore(tmp_path / "health.json")
    store.mark_down("council/a", "boom", ttl_s=3600)
    store.mark_up("council/a", source="probe")
    assert store.down_aliases() == set()
    assert store.status_of("council/a") == ("up", "")
    rec = store.snapshot()["council/a"]
    assert rec["source"] == "probe" and rec["until"] is None


def test_unknown_alias_status(tmp_path: Path) -> None:
    assert HealthStore(tmp_path / "health.json").status_of("nope") == ("unknown", "")


def test_corrupt_file_reads_empty(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    path.write_text("{not json")
    store = HealthStore(path)
    assert store.snapshot() == {}
    assert store.down_aliases() == set()
    store.mark_up("council/a")
    assert json.loads(path.read_text())["council/a"]["status"] == "up"


def test_non_dict_json_reads_empty(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    path.write_text("[1, 2]")
    assert HealthStore(path).snapshot() == {}


def test_write_is_atomic_tmp_replaced(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    store = HealthStore(path)
    store.mark_down("council/a", "boom", ttl_s=10)
    assert list(tmp_path.glob("health-*.tmp")) == []
    assert "council/a" in json.loads(path.read_text())


def test_note_probe_and_probed_at(tmp_path: Path) -> None:
    clock = Clock()
    store = HealthStore(tmp_path / "health.json", now=clock)
    assert store.probed_at() is None
    store.mark_down("council/a", "boom", ttl_s=3600)
    clock.advance(5)
    store.note_probe("council/a", "unknown", "429")
    assert store.status_of("council/a") == ("down", "boom")
    rec = store.snapshot()["council/a"]
    assert rec["last_probe"] == {"status": "unknown", "reason": "429", "at": clock().isoformat()}
    assert store.probed_at() == clock().isoformat()


def test_note_probe_new_alias_has_no_status_change(tmp_path: Path) -> None:
    store = HealthStore(tmp_path / "health.json")
    store.note_probe("council/b", "up", "")
    assert store.status_of("council/b") == ("unknown", "")
    assert store.probed_at() is not None


def test_probed_at_returns_latest(tmp_path: Path) -> None:
    clock = Clock()
    store = HealthStore(tmp_path / "health.json", now=clock)
    store.note_probe("council/a", "up", "")
    clock.advance(10)
    store.note_probe("council/b", "down", "x")
    assert store.probed_at() == clock().isoformat()


def test_mark_preserves_last_probe(tmp_path: Path) -> None:
    store = HealthStore(tmp_path / "health.json")
    store.note_probe("council/a", "up", "")
    store.mark_down("council/a", "boom", ttl_s=10)
    assert "last_probe" in store.snapshot()["council/a"]


def test_snapshot_is_a_copy(tmp_path: Path) -> None:
    store = HealthStore(tmp_path / "health.json")
    store.mark_down("council/a", "boom", ttl_s=3600)
    snap = store.snapshot()
    snap["council/a"]["status"] = "up"
    snap["other"] = {}
    assert store.status_of("council/a")[0] == "down"
    assert "other" not in store.snapshot()


def test_rereads_file_written_by_other_process(tmp_path: Path) -> None:
    path = tmp_path / "health.json"
    a, b = HealthStore(path), HealthStore(path)
    a.mark_down("council/a", "boom", ttl_s=3600)
    assert b.down_aliases() == {"council/a"}
    b.mark_up("council/a")
    assert a.down_aliases() == set()


def test_unwritable_path_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "file"
    blocker.write_text("x")
    store = HealthStore(blocker / "sub" / "health.json")
    store.mark_down("council/a", "boom", ttl_s=10)
    store.mark_up("council/a")
    store.note_probe("council/a", "up", "")
    assert store.snapshot() == {}
    assert store.down_aliases() == set()
    assert store.status_of("council/a") == ("unknown", "")
    assert store.probed_at() is None


def test_lock_noop_without_fcntl(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(health, "fcntl", None)
    store = HealthStore(tmp_path / "health.json")
    store.mark_down("council/a", "boom", ttl_s=10)
    assert store.down_aliases() == {"council/a"}
    assert not (tmp_path / "health.json.lock").exists()


def test_lock_sidecar_created_with_fcntl(tmp_path: Path) -> None:
    if health.fcntl is None:
        pytest.skip("fcntl unavailable")
    HealthStore(tmp_path / "health.json").mark_up("council/a")
    assert (tmp_path / "health.json.lock").exists()
