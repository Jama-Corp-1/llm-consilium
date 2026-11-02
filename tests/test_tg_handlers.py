import asyncio
import types

from consilium_tg import handlers
from consilium_tg.access import AccessStore
from consilium_tg.config import Settings
from consilium_tg.store import BotStore
from council.types import AskResult, CouncilResult, MemberAnswer


class FakeMessage:
    def __init__(self, text=""):
        self.text = text
        self.replies = []
        self.edits = []

    async def reply_text(self, text, reply_markup=None, **kw):
        child = FakeMessage(text)
        self.replies.append(child)
        return child

    async def edit_text(self, text, reply_markup=None, **kw):
        self.edits.append(text)


class FakeBot:
    def __init__(self):
        self.sent = []

    async def send_message(self, chat_id, text, reply_markup=None, **kw):
        self.sent.append((chat_id, text))
        return FakeMessage(text)


class FakeCallbackQuery:
    def __init__(self, data):
        self.data = data
        self.answered = False
        self.edits = []

    async def answer(self, *a, **k):
        self.answered = True

    async def edit_message_text(self, text, reply_markup=None, **kw):
        self.edits.append(text)


def make_update(uid=7, chat_id=100, text="hello", username="u"):
    msg = FakeMessage(text)
    upd = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=uid, username=username, full_name=username),
        effective_chat=types.SimpleNamespace(id=chat_id),
        message=msg, callback_query=None,
    )
    return upd, msg


def make_callback(uid=7, chat_id=100, data=""):
    q = FakeCallbackQuery(data)
    upd = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=uid, username="u", full_name="u"),
        effective_chat=types.SimpleNamespace(id=chat_id),
        message=None, callback_query=q,
    )
    return upd, q


class FakeService:
    def __init__(self):
        self.last = {}

    async def ask(self, prompt, *, model=None, capability=None, sensitivity="sensitive"):
        self.last = {"kind": "ask", "model": model, "sensitivity": sensitivity}
        return AskResult(answer="ASKED", model_used=model or "auto", capability=None, note="n")

    async def council(self, prompt, *, members=None, size=None, mode=None,
                      sensitivity="sensitive", on_progress=None):
        self.last = {"kind": "council", "members": members, "size": size, "mode": mode,
                     "sensitivity": sensitivity}
        if on_progress:
            on_progress({"event": "roster", "members": ["council/a"]})
            on_progress({"event": "member", "alias": "council/a", "ok": True})
            on_progress({"event": "aggregating"})
        return CouncilResult(
            answer="FINAL", per_member=[MemberAnswer("council/a", True, "x", "ok")],
            disagreements="", judge_used="j", mode=mode or "judge",
            note="", confidence="high")

    def list_models(self):
        return [{"alias": "council/a", "tier": "A"}, {"alias": "council/b", "tier": "B"}]


def _wire(tmp_path, owner=7):
    store = BotStore(tmp_path / "tg.db")
    access = AccessStore(tmp_path / "acc.json", owner_id=owner)
    return store, access, FakeService(), Settings(db_path=str(tmp_path / "tg.db"))


def _ctx(store, access, service, settings, args=None):
    return types.SimpleNamespace(
        bot=FakeBot(),
        bot_data={"store": store, "access": access, "service": service, "settings": settings},
        args=args or [],
    )


def test_denied_user_triggers_pairing(tmp_path):
    store, access, service, settings = _wire(tmp_path, owner=7)
    upd, msg = make_update(uid=9, text="hi")
    ctx = _ctx(store, access, service, settings)
    asyncio.run(handlers.on_text(upd, ctx))
    assert access.list_pending() == {"9": "u"}
    assert ctx.bot.sent and ctx.bot.sent[0][0] == 7     # owner notified
    assert msg.replies                                   # user got a notice
    assert service.last == {}                            # council never called


def test_ask_uses_session_model_and_persists(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    sid = store.active_session(100)
    store.set_setting(sid, "tool", "ask")
    store.set_setting(sid, "model", "council/pick")
    upd, msg = make_update(uid=7, text="question")
    asyncio.run(handlers.on_text(upd, _ctx(store, access, service, settings)))
    assert service.last["kind"] == "ask" and service.last["model"] == "council/pick"
    assert any("ASKED" in r.text for r in msg.replies)
    assert [m["role"] for m in store.recent_messages(sid, 8)] == ["user", "assistant"]


def test_council_uses_roster_and_persists_final(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    sid = store.active_session(100)
    store.set_setting(sid, "tool", "council")
    store.set_members(sid, ["council/a", "council/b"])
    upd, msg = make_update(uid=7, text="deep question")
    asyncio.run(handlers.on_text(upd, _ctx(store, access, service, settings)))
    assert service.last["kind"] == "council"
    assert service.last["members"] == ["council/a", "council/b"]
    progress = msg.replies[0]
    assert any("FINAL" in e for e in progress.edits)
    assert store.recent_messages(sid, 8)[-1]["content"] == "FINAL"


def test_council_override_command(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    sid = store.active_session(100)
    store.set_setting(sid, "tool", "ask")          # default ask, but /council overrides
    upd, msg = make_update(uid=7, text="")
    ctx = _ctx(store, access, service, settings, args=["hello", "world"])
    asyncio.run(handlers.council_cmd(upd, ctx))
    assert service.last["kind"] == "council"


def test_settings_toggle_tool(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    sid = store.active_session(100)
    upd, q = make_callback(uid=7, data="set:tool")
    asyncio.run(handlers.on_callback(upd, _ctx(store, access, service, settings)))
    assert q.answered and store.get_settings(sid)["tool"] == "ask"


def test_models_toggle_roster(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    sid = store.active_session(100)
    upd, q = make_callback(uid=7, data="mdl:council/a")
    asyncio.run(handlers.on_callback(upd, _ctx(store, access, service, settings)))
    assert store.get_settings(sid)["members"] == ["council/a"]
    upd2, q2 = make_callback(uid=7, data="mdl:auto")
    asyncio.run(handlers.on_callback(upd2, _ctx(store, access, service, settings)))
    assert store.get_settings(sid)["members"] == []


def test_new_and_switch_session(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    a = store.active_session(100)
    upd, msg = make_update(uid=7, text="")
    asyncio.run(handlers.new_session(upd, _ctx(store, access, service, settings)))
    b = store.active_session(100)
    assert b != a
    upd2, q = make_callback(uid=7, data=f"sess:switch:{a}")
    asyncio.run(handlers.on_callback(upd2, _ctx(store, access, service, settings)))
    assert store.active_session(100) == a


def test_owner_approve_via_command(tmp_path):
    store, access, service, settings = _wire(tmp_path, owner=7)
    access.request_access(9, "nine")
    upd, msg = make_update(uid=7, text="")
    asyncio.run(handlers.approve_cmd(upd, _ctx(store, access, service, settings, args=["9"])))
    assert access.is_allowed(9) and access.list_pending() == {}


def test_rename_active_session(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    store.active_session(100)                       # ensure a session exists
    upd, msg = make_update(uid=7, text="")
    ctx = _ctx(store, access, service, settings, args=["My", "Title"])
    asyncio.run(handlers.rename_cmd(upd, ctx))
    assert store.list_sessions(100)[0]["title"] == "My Title"


def test_rename_without_args_shows_usage(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    store.active_session(100)
    upd, msg = make_update(uid=7, text="")
    asyncio.run(handlers.rename_cmd(upd, _ctx(store, access, service, settings, args=[])))
    assert any("Usage: /rename" in r.text for r in msg.replies)


def test_malformed_callback_does_not_raise(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    sid = store.active_session(100)
    before = store.active_session(100)
    for bad in ("sess:switch:abc", "appr:xyz"):
        upd, q = make_callback(uid=7, data=bad)
        asyncio.run(handlers.on_callback(upd, _ctx(store, access, service, settings)))
    assert store.active_session(100) == before == sid   # state unchanged


def test_sess_del_deletes_session(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    first = store.active_session(100)
    second = store.create_session(100)              # a 2nd session, now active
    upd, q = make_callback(uid=7, data=f"sess:del:{second}")
    asyncio.run(handlers.on_callback(upd, _ctx(store, access, service, settings)))
    ids = [s["id"] for s in store.list_sessions(100)]
    assert second not in ids and first in ids


class _RaisingProgress(FakeMessage):
    """A message whose edit_text always fails; its reply_text yields the same kind.

    The `_run_council` loop obtains the progress message via `update.message.reply_text`,
    so making that child also raise on edit_text simulates a failing FINAL edit; the
    per-tick progress edits are already suppressed, so only the final edit is exercised.
    """

    async def reply_text(self, text, reply_markup=None, **kw):
        child = _RaisingProgress(text)
        self.replies.append(child)
        return child

    async def edit_text(self, text, reply_markup=None, **kw):
        raise RuntimeError("edit failed")


def test_final_edit_failure_still_persists(tmp_path):
    store, access, service, settings = _wire(tmp_path)
    sid = store.active_session(100)
    store.set_setting(sid, "tool", "council")

    outer = _RaisingProgress("deep question")       # its reply_text returns a raising progress msg
    upd = types.SimpleNamespace(
        effective_user=types.SimpleNamespace(id=7, username="u", full_name="u"),
        effective_chat=types.SimpleNamespace(id=100),
        message=outer, callback_query=None,
    )
    asyncio.run(handlers.on_text(upd, _ctx(store, access, service, settings)))
    # persisted the answer despite the failing edit
    assert store.recent_messages(sid, 8)[-1]["content"] == "FINAL"
    # a fallback fresh message carried the answer (reply on update.message == outer)
    assert any("FINAL" in r.text for r in outer.replies)


def test_ask_proxy_error_is_relayed_not_internal(tmp_path):
    from council.errors import ProxyError

    store, access, _service, settings = _wire(tmp_path)

    class ProxyFail(FakeService):
        async def ask(self, prompt, *, model=None, capability=None, sensitivity="sensitive"):
            raise ProxyError("proxy rejected the request: HTTP 401")

    sid = store.active_session(100)
    store.set_setting(sid, "tool", "ask")
    upd, msg = make_update(uid=7, text="question")
    asyncio.run(handlers.on_text(upd, _ctx(store, access, ProxyFail(), settings)))
    texts = [r.text for r in msg.replies]
    assert any("proxy rejected the request: HTTP 401" in t for t in texts)
    assert not any("internal error" in t for t in texts)
