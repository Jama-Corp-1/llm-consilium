from fastapi import FastAPI
from fastapi.testclient import TestClient

from consilium_chat.config import Settings
from consilium_chat.routes_chat import chat_router
from consilium_chat.store import ChatStore
from council.types import AskResult, CouncilResult, MemberAnswer


class FakeService:
    async def ask(self, prompt, *, model=None, capability=None, sensitivity="sensitive"):
        return AskResult(answer="ASKED", model_used="m1", capability=capability, note="routed")

    async def council(self, prompt, *, members=None, size=None, mode=None,
                      sensitivity="sensitive", on_progress=None):
        return CouncilResult(answer="A", per_member=[MemberAnswer("x", True, "xa", "ok")],
                             disagreements="none", judge_used="judge", mode=mode or "judge",
                             note="nn", confidence="high")


def _client(tmp_path, service=None):
    app = FastAPI()
    db = str(tmp_path / "chat.db")
    app.state.store = ChatStore(db)
    app.state.service = service or FakeService()
    app.state.settings = Settings(chat_db_path=db)
    app.include_router(chat_router())
    return TestClient(app)


def test_create_and_council_message(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/api/threads", json={}).json()["id"]
    r = c.post(f"/api/threads/{tid}/messages",
               json={"content": "hello there", "tool": "council", "sensitivity": "sensitive"})
    assert r.status_code == 200
    body = r.json()
    assert body["content"] == "A" and body["meta"]["confidence"] == "high"
    msgs = c.get(f"/api/threads/{tid}").json()
    assert [m["role"] for m in msgs] == ["user", "assistant"]


def test_ask_message(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/api/threads", json={}).json()["id"]
    r = c.post(f"/api/threads/{tid}/messages",
               json={"content": "q", "tool": "ask", "sensitivity": "sensitive"})
    assert r.json()["content"] == "ASKED" and r.json()["meta"]["model"] == "m1"


def test_auto_title_from_first_message(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/api/threads", json={}).json()["id"]
    c.post(f"/api/threads/{tid}/messages",
           json={"content": "the first question here", "tool": "ask", "sensitivity": "sensitive"})
    t = [x for x in c.get("/api/threads").json() if x["id"] == tid][0]
    assert "first question" in t["title"]


def test_rename_and_delete(tmp_path):
    c = _client(tmp_path)
    tid = c.post("/api/threads", json={}).json()["id"]
    c.patch(f"/api/threads/{tid}", json={"title": "Renamed"})
    assert [x for x in c.get("/api/threads").json() if x["id"] == tid][0]["title"] == "Renamed"
    c.delete(f"/api/threads/{tid}")
    assert all(x["id"] != tid for x in c.get("/api/threads").json())


def test_council_stream_emits_events(tmp_path):
    class StreamService:
        async def council(self, prompt, *, members=None, size=None, mode=None,
                          sensitivity="sensitive", on_progress=None):
            if on_progress:
                on_progress({"event": "roster", "members": ["x", "y"]})
                on_progress({"event": "member", "alias": "x", "ok": True})
                on_progress({"event": "member", "alias": "y", "ok": False})
                on_progress({"event": "aggregating"})
            return CouncilResult(answer="FINAL", per_member=[MemberAnswer("x", True, "xa", "ok")],
                                 disagreements="none", judge_used="j", mode="judge",
                                 note="", confidence="med")

    c = _client(tmp_path, service=StreamService())
    tid = c.post("/api/threads", json={}).json()["id"]
    body = c.get(f"/api/threads/{tid}/stream?content=hi&sensitivity=sensitive").text
    assert "roster" in body and "member" in body and "aggregating" in body and "FINAL" in body
    msgs = c.get(f"/api/threads/{tid}").json()
    assert any(m["role"] == "assistant" and m["content"] == "FINAL" for m in msgs)


def test_error_mapped_to_200(tmp_path):
    from council.errors import AllMembersFailed

    class FailService:
        async def council(self, prompt, **kw):
            raise AllMembersFailed("all members failed")

    c = _client(tmp_path, service=FailService())
    tid = c.post("/api/threads", json={}).json()["id"]
    r = c.post(f"/api/threads/{tid}/messages",
               json={"content": "x", "tool": "council", "sensitivity": "sensitive"})
    assert r.status_code == 200 and r.json()["event"] == "error"


def test_get_threads_empty(tmp_path):
    c = _client(tmp_path)
    assert c.get("/api/threads").json() == []


def test_ask_error_mapped_to_200(tmp_path):
    from council.errors import NoEligibleMember

    class FailAsk:
        async def ask(self, prompt, **kw):
            raise NoEligibleMember("no member")

    c = _client(tmp_path, service=FailAsk())
    tid = c.post("/api/threads", json={}).json()["id"]
    r = c.post(f"/api/threads/{tid}/messages",
               json={"content": "x", "tool": "ask", "sensitivity": "sensitive"})
    assert r.status_code == 200 and r.json()["event"] == "error"
    # no assistant row persisted on error
    msgs = c.get(f"/api/threads/{tid}").json()
    assert [m["role"] for m in msgs] == ["user"]


def test_create_thread_with_title(tmp_path):
    c = _client(tmp_path)
    body = c.post("/api/threads", json={"title": "My chat"}).json()
    assert body["title"] == "My chat"
    assert "created_at" in body and "id" in body


def test_stream_error_mapped(tmp_path):
    from council.errors import PrivacyRefusal

    class FailStream:
        async def council(self, prompt, **kw):
            raise PrivacyRefusal("nope")

    c = _client(tmp_path, service=FailStream())
    tid = c.post("/api/threads", json={}).json()["id"]
    body = c.get(f"/api/threads/{tid}/stream?content=hi&sensitivity=sensitive").text
    assert "error" in body
    # no assistant row persisted on error
    msgs = c.get(f"/api/threads/{tid}").json()
    assert [m["role"] for m in msgs] == ["user"]


def test_stream_coerces_size_to_int(tmp_path):
    seen = {}

    class SizeStream:
        async def council(self, prompt, *, members=None, size=None, mode=None,
                          sensitivity="sensitive", on_progress=None):
            seen["type"] = type(size)
            seen["value"] = size
            return CouncilResult(answer="FINAL", per_member=[MemberAnswer("x", True, "xa", "ok")],
                                 disagreements="none", judge_used="j", mode="judge",
                                 note="", confidence="med")

    c = _client(tmp_path, service=SizeStream())
    tid = c.post("/api/threads", json={}).json()["id"]
    body = c.get(f"/api/threads/{tid}/stream?content=hi&sensitivity=sensitive&size=3").text
    assert seen["type"] is int and seen["value"] == 3
    assert "FINAL" in body


def test_post_council_coerces_size_to_int(tmp_path):
    seen = {}

    class SizeService:
        async def council(self, prompt, *, members=None, size=None, mode=None,
                          sensitivity="sensitive", on_progress=None):
            seen["type"] = type(size)
            seen["value"] = size
            return CouncilResult(answer="A", per_member=[MemberAnswer("x", True, "xa", "ok")],
                                 disagreements="none", judge_used="j", mode="judge",
                                 note="", confidence="high")

    c = _client(tmp_path, service=SizeService())
    tid = c.post("/api/threads", json={}).json()["id"]
    c.post(f"/api/threads/{tid}/messages",
           json={"content": "x", "tool": "council", "sensitivity": "sensitive", "size": "4"})
    assert seen["type"] is int and seen["value"] == 4


def test_stream_non_service_error_emits_error_frame(tmp_path):
    class BoomStream:
        async def council(self, prompt, **kw):
            raise ValueError("boom")

    c = _client(tmp_path, service=BoomStream())
    tid = c.post("/api/threads", json={}).json()["id"]
    r = c.get(f"/api/threads/{tid}/stream?content=hi&sensitivity=sensitive")
    assert r.status_code == 200
    assert '"event": "error"' in r.text
    # no assistant row persisted on error
    msgs = c.get(f"/api/threads/{tid}").json()
    assert [m["role"] for m in msgs] == ["user"]


def test_proxy_error_mapped_to_200(tmp_path):
    from council.errors import ProxyError

    class ProxyFail:
        async def council(self, prompt, **kw):
            raise ProxyError("proxy rejected the request: HTTP 401")

    c = TestClient(_client(tmp_path, service=ProxyFail()).app, raise_server_exceptions=False)
    tid = c.post("/api/threads", json={}).json()["id"]
    r = c.post(f"/api/threads/{tid}/messages",
               json={"content": "x", "tool": "council", "sensitivity": "sensitive"})
    assert r.status_code == 200
    assert r.json() == {"event": "error", "error": "proxy rejected the request: HTTP 401",
                        "note": ""}
