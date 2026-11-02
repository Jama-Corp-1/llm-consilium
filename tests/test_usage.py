from council import usage
from council.types import Member

CAPPED_REQ = Member("r", "A", {"general": 3}, 5, "r", rpd=2, tpd=None)
CAPPED_TOK = Member("t", "A", {"general": 3}, 5, "t", rpd=None, tpd=100)
UNCAPPED = Member("u", "A", {"general": 3}, 5, "u")
PRICED = Member("p", "A", {"general": 3}, 5, "p", cost_per_1k=2.0)


def test_record_and_counts_roundtrip(tmp_path):
    store = usage.UsageStore(tmp_path / "u.db")
    store.record("r", 40, day="2026-07-14")
    store.record("r", 10, day="2026-07-14")
    assert store.counts("2026-07-14") == {"r": (2, 50)}


def test_available_drops_over_rpd_and_tpd():
    counts = {"r": (2, 0), "t": (1, 100)}  # r at rpd=2, t at tpd=100
    got = usage.available([CAPPED_REQ, CAPPED_TOK, UNCAPPED], counts)
    assert [m.alias for m in got] == ["u"]


def test_available_keeps_under_cap():
    counts = {"r": (1, 0), "t": (1, 50)}
    got = usage.available([CAPPED_REQ, CAPPED_TOK, UNCAPPED], counts)
    assert {m.alias for m in got} == {"r", "t", "u"}


def test_summary_shape_and_exhausted_flag():
    counts = {"r": (2, 0)}
    rows = {row["alias"]: row for row in usage.summary([CAPPED_REQ, UNCAPPED], counts)}
    assert rows["r"]["requests"] == 2 and rows["r"]["rpd"] == 2 and rows["r"]["exhausted"] is True
    assert rows["u"]["exhausted"] is False and rows["u"]["tokens"] == 0


def test_summary_computes_cost_usd_from_tokens_and_rate():
    counts = {"p": (3, 1500)}  # 1500 tokens * $2.0 / 1k = $3.0
    rows = {row["alias"]: row for row in usage.summary([PRICED], counts)}
    assert rows["p"]["cost_usd"] == 3.0


def test_summary_cost_usd_zero_for_free_member():
    counts = {"u": (1, 1000)}
    rows = {row["alias"]: row for row in usage.summary([UNCAPPED], counts)}
    assert rows["u"]["cost_usd"] == 0.0


def test_history_returns_days_newest_first(tmp_path):
    store = usage.UsageStore(tmp_path / "u.db")
    store.record("r", 10, day="2026-07-20")
    store.record("r", 20, day="2026-07-22")
    store.record("s", 5, day="2026-07-22")
    rows = store.history(days=7, end_day="2026-07-22")
    assert rows[0]["day"] == "2026-07-22"  # newest first
    by_key = {(row["day"], row["alias"]): row for row in rows}
    assert by_key[("2026-07-22", "r")]["tokens"] == 20
    assert by_key[("2026-07-20", "r")]["requests"] == 1


def test_history_excludes_days_before_window(tmp_path):
    store = usage.UsageStore(tmp_path / "u.db")
    store.record("r", 10, day="2026-07-10")
    store.record("r", 20, day="2026-07-22")
    rows = store.history(days=3, end_day="2026-07-22")  # window 2026-07-20..22
    assert all(row["day"] >= "2026-07-20" for row in rows)
    assert not any(row["day"] == "2026-07-10" for row in rows)


def test_store_survives_corrupt_db(tmp_path):
    db = tmp_path / "corrupt.db"
    db.write_bytes(b"this is not a sqlite database at all")
    store = usage.UsageStore(db)   # init must not raise on a corrupt-but-writable file
    store.record("r", 5)           # must not raise
    assert store.counts() == {}    # degrades to empty


def test_store_survives_unwritable_path(tmp_path):
    # Hermetic under any uid (incl. root): the db path's parent is a regular file, so
    # mkdir raises NotADirectoryError (an OSError) that a root uid cannot bypass.
    blocker = tmp_path / "blocker"
    blocker.write_text("x", encoding="utf-8")
    store = usage.UsageStore(blocker / "nested" / "u.db")
    store.record("r", 5)          # must not raise
    assert store.counts() == {}   # degrades to empty


def test_summary_health_columns_default_unknown(tmp_path):
    from council.health import HealthStore

    timed = Member("x", "A", {"general": 3}, 5, "x", timeout=45.0)
    rows = {r["alias"]: r for r in usage.summary([UNCAPPED, timed], {})}
    assert rows["u"]["health"] == "unknown" and rows["u"]["health_reason"] == ""
    assert rows["u"]["timeout"] is None and rows["x"]["timeout"] == 45.0
    health = HealthStore(tmp_path / "h.json")
    health.mark_down("x", "402 payment required", ttl_s=60)
    health.mark_up("u")
    rows = {r["alias"]: r for r in usage.summary([UNCAPPED, timed], {}, health)}
    assert rows["x"]["health"] == "down"
    assert rows["x"]["health_reason"] == "402 payment required"
    assert rows["u"]["health"] == "up"
