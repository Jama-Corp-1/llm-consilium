"""Chat API router: threads, messages, and the council live-progress SSE stream.

The router is pure over ``request.app.state``: every handler reads its collaborators
(``store``, ``service``, ``settings``) off ``app.state``, so it is hermetically
testable with fakes. Service errors are mapped to HTTP 200 JSON ``{"event": "error"}``
envelopes rather than 500s, so the frontend can render them inline in the thread.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

from consilium_chat.config import Settings
from consilium_chat.store import ChatStore
from council.errors import AllMembersFailed, NoEligibleMember, PrivacyRefusal
from council.types import CouncilResult

_SERVICE_ERRORS = (NoEligibleMember, AllMembersFailed, PrivacyRefusal)
_DEFAULT_TITLE = "New chat"
_TITLE_CHARS = 40


def _coerce_size(raw: str | int | None) -> int | None:
    """Coerce a raw ``size`` (string from JSON/query) to ``int`` or ``None``."""
    try:
        return int(raw) if raw not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _council_meta(res: CouncilResult) -> dict[str, object]:
    """Assistant-message meta for a council result (shared by POST + stream)."""
    return {
        "mode": res.mode,
        "confidence": res.confidence,
        "note": res.note,
        "judge_used": res.judge_used,
        "per_member": [
            {"alias": a.alias, "ok": a.ok, "answer": a.answer} for a in res.per_member
        ],
    }


def _maybe_autotitle(store: ChatStore, thread_id: int, content: str) -> None:
    """Set the thread title from the first message if it is still a placeholder."""
    current = next(
        (t["title"] for t in store.list_threads() if t["id"] == thread_id), None
    )
    if current in ("", _DEFAULT_TITLE, None):
        store.rename_thread(thread_id, content[:_TITLE_CHARS])


def _build_prompt(
    store: ChatStore, settings: Settings, thread_id: int, content: str
) -> str:
    from consilium_chat.context import build_prompt

    # get_messages already includes the just-added user row; drop it so the prompt
    # is built from PRIOR turns plus the new content.
    history = store.get_messages(thread_id)[:-1]
    return build_prompt(
        history,
        content,
        turns=settings.context_turns,
        char_budget=settings.context_char_budget,
    )


def chat_router() -> APIRouter:
    """Build the chat ``APIRouter`` (threads, messages, SSE stream)."""
    router = APIRouter()

    @router.post("/api/threads")
    async def create_thread(request: Request) -> dict[str, Any]:
        body = await request.json() if await _has_body(request) else {}
        title = (body or {}).get("title") or _DEFAULT_TITLE
        store: ChatStore = request.app.state.store
        tid = store.create_thread(title)
        created = next((t for t in store.list_threads() if t["id"] == tid), None)
        return created or {"id": tid, "title": title, "created_at": None}

    @router.get("/api/threads")
    async def list_threads(request: Request) -> list[dict[str, Any]]:
        store: ChatStore = request.app.state.store
        return store.list_threads()

    @router.get("/api/threads/{thread_id}")
    async def get_thread(thread_id: int, request: Request) -> list[dict[str, Any]]:
        store: ChatStore = request.app.state.store
        return store.get_messages(thread_id)

    @router.delete("/api/threads/{thread_id}", response_model=None)
    async def delete_thread(thread_id: int, request: Request) -> dict[str, object]:
        request.app.state.store.delete_thread(thread_id)
        return {"ok": True}

    @router.patch("/api/threads/{thread_id}", response_model=None)
    async def rename_thread(thread_id: int, request: Request) -> dict[str, object]:
        body = await request.json()
        request.app.state.store.rename_thread(thread_id, body.get("title", ""))
        return {"ok": True}

    @router.post("/api/threads/{thread_id}/messages")
    async def post_message(thread_id: int, request: Request) -> dict[str, Any]:
        body = await request.json()
        store: ChatStore = request.app.state.store
        service = request.app.state.service
        settings: Settings = request.app.state.settings

        content = body.get("content", "")
        tool = body.get("tool", "council")
        sensitivity = body.get("sensitivity") or "sensitive"

        store.add_message(thread_id, "user", content, {})
        _maybe_autotitle(store, thread_id, content)
        prompt = _build_prompt(store, settings, thread_id, content)

        try:
            if tool == "ask":
                res = await service.ask(
                    prompt,
                    model=body.get("model") or None,
                    capability=None,
                    sensitivity=sensitivity,
                )
                answer = res.answer
                meta = {
                    "model": res.model_used,
                    "capability": res.capability,
                    "note": res.note,
                }
                meta["sensitivity"] = sensitivity
            else:
                res = await service.council(
                    prompt,
                    members=None,
                    size=_coerce_size(body.get("size")),
                    mode=body.get("mode") or None,
                    sensitivity=sensitivity,
                )
                answer = res.answer
                meta = _council_meta(res)
                meta["sensitivity"] = sensitivity
        except _SERVICE_ERRORS as exc:
            return {"event": "error", "error": str(exc), "note": ""}

        mid = store.add_message(thread_id, "assistant", answer, meta)
        return {
            "id": mid,
            "role": "assistant",
            "content": answer,
            "meta": meta,
            "created_at": _created_at(store, thread_id, mid),
        }

    @router.get("/api/threads/{thread_id}/stream", response_model=None)
    async def stream_council(thread_id: int, request: Request) -> StreamingResponse:
        store: ChatStore = request.app.state.store
        service = request.app.state.service
        settings: Settings = request.app.state.settings
        qp = request.query_params

        content = qp.get("content", "")
        sensitivity = qp.get("sensitivity") or "sensitive"
        mode = qp.get("mode") or None
        size = _coerce_size(qp.get("size"))

        store.add_message(thread_id, "user", content, {})
        _maybe_autotitle(store, thread_id, content)
        prompt = _build_prompt(store, settings, thread_id, content)

        async def gen() -> AsyncIterator[str]:
            q: asyncio.Queue[dict[str, object] | None] = asyncio.Queue()

            def on_progress(evt: dict[str, object]) -> None:
                q.put_nowait(evt)

            async def run() -> None:
                try:
                    res = await service.council(
                        prompt,
                        members=None,
                        size=size,
                        mode=mode,
                        sensitivity=sensitivity,
                        on_progress=on_progress,
                    )
                    meta = _council_meta(res)
                    meta["sensitivity"] = sensitivity
                    q.put_nowait({"event": "final", "content": res.answer, **meta})
                    store.add_message(thread_id, "assistant", res.answer, meta)
                except _SERVICE_ERRORS as exc:
                    q.put_nowait({"event": "error", "error": str(exc), "note": ""})
                except Exception as exc:  # noqa: BLE001 — never leak a raw 500 into the SSE stream
                    q.put_nowait(
                        {"event": "error", "error": f"internal error: {exc.__class__.__name__}",
                         "note": ""}
                    )
                finally:
                    q.put_nowait(None)

            task = asyncio.ensure_future(run())
            try:
                while True:
                    evt = await q.get()
                    if evt is None:
                        break
                    yield f"data: {json.dumps(evt)}\n\n"
            finally:
                if not task.done():
                    task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task

        return StreamingResponse(gen(), media_type="text/event-stream")

    return router


def _created_at(store: ChatStore, thread_id: int, mid: int) -> object:
    for m in store.get_messages(thread_id):
        if m["id"] == mid:
            return m["created_at"]
    return None


async def _has_body(request: Request) -> bool:
    """True if the request carries a (non-empty) body to parse as JSON."""
    body = await request.body()
    return bool(body)
