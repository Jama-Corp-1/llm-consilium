from consilium_chat.store import ChatStore


def test_thread_and_message_roundtrip(tmp_path):
    s = ChatStore(tmp_path / "chat.db")
    tid = s.create_thread("First")
    s.add_message(tid, "user", "hi", None)
    s.add_message(tid, "assistant", "hello", {"mode": "vote", "confidence": "high"})
    msgs = s.get_messages(tid)
    assert [m["role"] for m in msgs] == ["user", "assistant"]
    assert msgs[1]["meta"]["mode"] == "vote"
    assert s.list_threads()[0]["id"] == tid


def test_rename_and_delete(tmp_path):
    s = ChatStore(tmp_path / "chat.db")
    tid = s.create_thread("x")
    s.rename_thread(tid, "renamed")
    assert s.list_threads()[0]["title"] == "renamed"
    s.delete_thread(tid)
    assert s.list_threads() == []


def test_degrades_on_bad_path(tmp_path):
    blocker = tmp_path / "f"
    blocker.write_text("x")
    s = ChatStore(blocker / "nested" / "chat.db")  # parent is a file -> NotADirectoryError
    assert s.create_thread("x") == -1   # sentinel, no raise
    assert s.list_threads() == []


def test_none_lastrowid_returns_sentinel_without_raise(tmp_path):
    # Regression: a None cursor.lastrowid must degrade to the -1 sentinel, not
    # raise TypeError (int(None)) which the sqlite3.Error handler would not catch.
    class _NoRowidCursor:
        """Real cursor proxy that reports lastrowid=None."""

        lastrowid = None

        def __init__(self, cur):
            self._cur = cur

        def __getattr__(self, name):
            return getattr(self._cur, name)

    class _NoRowidConn:
        """Wraps a real connection but reports lastrowid=None on INSERT cursors."""

        def __init__(self, conn):
            self._conn = conn

        def execute(self, sql, *args, **kwargs):
            cur = self._conn.execute(sql, *args, **kwargs)
            if sql.strip().upper().startswith("INSERT"):
                return _NoRowidCursor(cur)
            return cur

        def __getattr__(self, name):
            return getattr(self._conn, name)

        def __enter__(self):
            self._conn.__enter__()
            return self

        def __exit__(self, *exc):
            return self._conn.__exit__(*exc)

        def close(self):
            self._conn.close()

    class _NoRowidStore(ChatStore):
        def _connect(self):
            return _NoRowidConn(super()._connect())

    s = _NoRowidStore(tmp_path / "chat.db")
    assert s.create_thread("x") == -1
    assert s.add_message(1, "user", "hi", None) == -1
