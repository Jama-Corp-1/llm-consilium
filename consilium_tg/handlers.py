from __future__ import annotations

import asyncio
import contextlib
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any, TypeVar

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from consilium_chat.context import build_prompt
from consilium_tg import render
from consilium_tg.access import AccessStore
from consilium_tg.config import Settings
from consilium_tg.store import BotStore
from council.errors import AllMembersFailed, NoEligibleMember, PrivacyRefusal
from council.types import CouncilResult

if TYPE_CHECKING:
    from consilium_chat.council_service import CouncilService

_SERVICE_ERRORS = (NoEligibleMember, AllMembersFailed, PrivacyRefusal)
_MODES: list[str] = ["", "vote", "judge", "debate", "peer-rank"]
_SIZES: list[int | None] = [None, 3, 4, 5]

_Context = ContextTypes.DEFAULT_TYPE
_Deps = tuple["BotStore", "AccessStore", "CouncilService | None", "Settings"]
# live-progress bridge: council progress dict, or a terminal ("final", result) /
# ("error", message) tuple, or None as the stream sentinel.
_QueueItem = dict[str, object] | tuple[str, CouncilResult | str] | None
_T = TypeVar("_T")


def _as_int(s: str | int | None) -> int | None:
    try:
        return int(s)  # type: ignore[arg-type]  # None handled by TypeError below
    except (TypeError, ValueError):
        return None


def kb(layout: Sequence[Sequence[tuple[str, str]]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(text, callback_data=data) for (text, data) in row] for row in layout]
    )


def _deps(context: _Context) -> _Deps:
    d = context.bot_data
    return d["store"], d["access"], d["service"], d["settings"]


async def _ensure_allowed(update: Update, context: _Context) -> bool:
    _store, access, _service, _settings = _deps(context)
    user = update.effective_user
    assert user is not None and update.message is not None
    if access.is_allowed(user.id):
        return True
    owner = access.owner_id()
    if owner is None:
        await update.message.reply_text("This bot is locked — the owner is not configured.")
        return False
    access.request_access(user.id, user.username or user.full_name)
    await context.bot.send_message(
        owner,
        f"Access request from {user.full_name} (id {user.id}).",
        reply_markup=kb([[("Approve", f"appr:{user.id}"), ("Deny", f"deny:{user.id}")]]),
    )
    await update.message.reply_text("Access requested — waiting for the owner's approval.")
    return False


async def start(update: Update, context: _Context) -> None:
    _store, access, _service, _settings = _deps(context)
    assert update.effective_user is not None and update.message is not None
    if access.is_allowed(update.effective_user.id):
        await update.message.reply_text(
            "Consilium bot — a free-LLM council. Send a message to consult it. "
            "/settings to configure this session, /council for a full council, "
            "/sessions to switch conversations, /help for more."
        )
    else:
        await _ensure_allowed(update, context)


async def help_cmd(update: Update, context: _Context) -> None:
    assert update.message is not None
    await update.message.reply_text(
        "Ask the free-LLM council.\n"
        "• plain text → ask/council per /settings\n"
        "• /ask <q> or /council <q> — one-off override\n"
        "• /settings — tool, sensitivity (tier), mode, council model roster, size, footer "
        "(per session, changeable mid-conversation)\n"
        "• /sessions — switch/delete · /new — fresh session · /rename <title> — retitle "
        "the active session\n\n"
        "Privacy: your messages transit Telegram's servers. The privacy gate still limits which "
        "LLMs see the prompt (default: sensitive → Tier-A only)."
    )


async def new_session(update: Update, context: _Context) -> None:
    if not await _ensure_allowed(update, context):
        return
    assert update.effective_chat is not None and update.message is not None
    store, *_ = _deps(context)
    store.create_session(update.effective_chat.id)
    await update.message.reply_text("Started a fresh session (previous ones kept — /sessions).")


async def rename_cmd(update: Update, context: _Context) -> None:
    if not await _ensure_allowed(update, context):
        return
    assert update.effective_chat is not None and update.message is not None
    store, *_ = _deps(context)
    title = " ".join(context.args or []).strip()
    if not title:
        await update.message.reply_text("Usage: /rename <new title>")
        return
    sid = store.active_session(update.effective_chat.id)
    store.rename_session(sid, title)
    await update.message.reply_text(f"Renamed this session to “{title}”.")


async def sessions_cmd(update: Update, context: _Context) -> None:
    if not await _ensure_allowed(update, context):
        return
    assert update.effective_chat is not None and update.message is not None
    store, *_ = _deps(context)
    rows = store.list_sessions(update.effective_chat.id)
    await update.message.reply_text("Sessions:", reply_markup=kb(render.sessions_layout(rows)))


async def settings_cmd(update: Update, context: _Context) -> None:
    if not await _ensure_allowed(update, context):
        return
    assert update.effective_chat is not None and update.message is not None
    store, *_ = _deps(context)
    sid = store.active_session(update.effective_chat.id)
    await update.message.reply_text(
        "Settings (this session):",
        reply_markup=kb(render.settings_layout(store.get_settings(sid))),
    )


async def ask_cmd(update: Update, context: _Context) -> None:
    await _run_message(update, context, tool_override="ask", text=" ".join(context.args or []))


async def council_cmd(update: Update, context: _Context) -> None:
    await _run_message(update, context, tool_override="council",
                       text=" ".join(context.args or []))


async def on_text(update: Update, context: _Context) -> None:
    assert update.message is not None
    await _run_message(update, context, tool_override=None, text=update.message.text)


async def _run_message(
    update: Update, context: _Context, *, tool_override: str | None, text: str | None
) -> None:
    if not await _ensure_allowed(update, context):
        return
    assert update.effective_chat is not None and update.message is not None
    store, _access, service, settings = _deps(context)
    text = text or ""
    if not text.strip():
        await update.message.reply_text("Send a question.")
        return
    chat_id = update.effective_chat.id
    sid = store.active_session(chat_id)
    cfg = store.get_settings(sid)
    tool = tool_override or cfg.get("tool", "council")
    store.add_message(sid, "user", text)
    store.maybe_autotitle(sid, text)
    history = store.recent_messages(sid, settings.context_turns)[:-1]
    prompt = build_prompt(history, text, turns=settings.context_turns,
                          char_budget=settings.context_char_budget)
    if service is None:
        await update.message.reply_text("Council backend unavailable — start the proxy.")
        return
    if tool == "ask":
        await _run_ask(update, service, store, sid, cfg, prompt)
    else:
        await _run_council(update, service, store, sid, cfg, prompt)


async def _send_chunks_reply(update: Update, text: str) -> None:
    assert update.message is not None
    parts = render.chunk(text)
    for p in parts:
        await update.message.reply_text(p)


async def _run_ask(
    update: Update, service: CouncilService, store: BotStore, sid: int,
    cfg: dict[str, object], prompt: str,
) -> None:
    assert update.message is not None
    model = cfg.get("model")
    sensitivity = cfg.get("sensitivity", "sensitive")
    try:
        res = await service.ask(prompt, model=str(model) if model else None, capability=None,
                                sensitivity=str(sensitivity))
    except _SERVICE_ERRORS as exc:
        await update.message.reply_text(f"⚠️ {exc}")
        return
    except Exception as exc:  # noqa: BLE001 - never crash a chat on an unexpected error
        await update.message.reply_text(f"⚠️ internal error: {exc.__class__.__name__}")
        return
    meta: dict[str, object] = {"model": res.model_used}
    await _send_chunks_reply(update, render.answer_text(
        res.answer, meta, show_footer=bool(cfg.get("show_footer", True))))
    store.add_message(sid, "assistant", res.answer)


async def _run_council(
    update: Update, service: CouncilService, store: BotStore, sid: int,
    cfg: dict[str, object], prompt: str,
) -> None:
    assert update.message is not None
    message = update.message
    progress = await message.reply_text("\U0001f9e0 Council starting…")
    q: asyncio.Queue[_QueueItem] = asyncio.Queue()

    def on_progress(evt: dict[str, object]) -> None:
        q.put_nowait(evt)

    members = cfg.get("members")
    size = cfg.get("size")
    mode = cfg.get("mode")
    sensitivity = cfg.get("sensitivity", "sensitive")

    async def run() -> None:
        try:
            res = await service.council(
                prompt,
                members=list(members) if isinstance(members, list) else None,
                size=size if isinstance(size, int) else None,
                mode=str(mode) if mode else None,
                sensitivity=str(sensitivity),
                on_progress=on_progress,
            )
            q.put_nowait(("final", res))
        except _SERVICE_ERRORS as exc:
            q.put_nowait(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001 - map any error to a friendly message
            q.put_nowait(("error", f"internal error: {exc.__class__.__name__}"))
        finally:
            q.put_nowait(None)

    task = asyncio.ensure_future(run())
    roster: list[str] = []
    done: dict[str, bool] = {}
    aggregating = False
    last = 0.0
    show_footer = bool(cfg.get("show_footer", True))
    try:
        while True:
            item = await q.get()
            if item is None:
                break
            if isinstance(item, tuple):
                kind, payload = item
                if kind == "final" and isinstance(payload, CouncilResult):
                    meta: dict[str, object] = {"mode": payload.mode,
                                               "confidence": payload.confidence,
                                               "note": payload.note}
                    parts = render.chunk(render.answer_text(
                        payload.answer, meta, show_footer=show_footer))
                    store.add_message(sid, "assistant", payload.answer)
                    try:
                        await progress.edit_text(parts[0])
                    except Exception:  # noqa: BLE001 - fall back to a fresh message
                        with contextlib.suppress(Exception):
                            await message.reply_text(parts[0])
                    for p in parts[1:]:
                        with contextlib.suppress(Exception):
                            await message.reply_text(p)
                else:
                    with contextlib.suppress(Exception):
                        await progress.edit_text(f"⚠️ {payload}")
                continue
            event = item.get("event")
            if event == "roster":
                raw_members = item.get("members", [])
                roster = [str(m) for m in raw_members] if isinstance(raw_members, list) else []
            elif event == "member":
                done[str(item["alias"])] = bool(item["ok"])
            elif event == "aggregating":
                aggregating = True
            now = time.monotonic()
            if now - last >= 1.0:
                with contextlib.suppress(Exception):
                    await progress.edit_text(render.progress_text(roster, done, aggregating))
                last = now
    finally:
        if not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await task


async def approve_cmd(update: Update, context: _Context) -> None:
    _store, access, _service, _settings = _deps(context)
    assert update.effective_user is not None and update.message is not None
    if not access.is_owner(update.effective_user.id) or not context.args:
        return
    target = _as_int(context.args[0])
    if target is None:
        await update.message.reply_text("Usage: /approve <id>")
        return
    access.approve(target)
    await update.message.reply_text(f"Approved {target}.")


async def deny_cmd(update: Update, context: _Context) -> None:
    _store, access, _service, _settings = _deps(context)
    assert update.effective_user is not None and update.message is not None
    if not access.is_owner(update.effective_user.id) or not context.args:
        return
    target = _as_int(context.args[0])
    if target is None:
        await update.message.reply_text("Usage: /deny <id>")
        return
    access.deny(target)
    await update.message.reply_text(f"Denied {target}.")


async def pending_cmd(update: Update, context: _Context) -> None:
    _store, access, _service, _settings = _deps(context)
    assert update.effective_user is not None and update.message is not None
    if not access.is_owner(update.effective_user.id):
        return
    pend = access.list_pending()
    body = ", ".join(f"{k} ({v})" for k, v in pend.items()) or "none"
    await update.message.reply_text(f"Pending: {body}")


def _cycle(seq: Sequence[_T], cur: object) -> _T:
    return seq[(seq.index(cur) + 1) % len(seq)] if cur in seq else seq[0]


async def on_callback(update: Update, context: _Context) -> None:
    store, access, service, _settings = _deps(context)
    query = update.callback_query
    assert query is not None and update.effective_user is not None
    await query.answer()
    data = query.data or ""
    uid = update.effective_user.id

    if data.startswith(("appr:", "deny:")):
        if access.is_owner(uid):
            target = _as_int(data.split(":", 1)[1])
            if target is None:
                return
            if data.startswith("appr:"):
                access.approve(target)
                with contextlib.suppress(Exception):
                    await context.bot.send_message(target, "You're approved — send a message.")
                await query.edit_message_text(f"Approved {target}.")
            else:
                access.deny(target)
                await query.edit_message_text(f"Denied {target}.")
        return

    if not access.is_allowed(uid):
        return
    assert update.effective_chat is not None
    chat_id = update.effective_chat.id
    sid = store.active_session(chat_id)

    if data.startswith("sess:"):
        rest = data[len("sess:"):]
        if rest == "new":
            store.create_session(chat_id)
        elif rest.startswith("switch:"):
            target = _as_int(rest.split(":", 1)[1])
            if target is None:
                return
            store.switch_session(chat_id, target)
        elif rest.startswith("del:"):
            target = _as_int(rest.split(":", 1)[1])
            if target is None:
                return
            store.delete_session(chat_id, target)
        await query.edit_message_text(
            "Sessions:", reply_markup=kb(render.sessions_layout(store.list_sessions(chat_id))))
        return

    if data == "menu:settings":
        await query.edit_message_text(
            "Settings (this session):",
            reply_markup=kb(render.settings_layout(store.get_settings(sid))))
        return

    if data == "menu:models" or data.startswith("mdl:"):
        if data.startswith("mdl:"):
            alias = data.split(":", 1)[1]
            raw_cur = store.get_settings(sid).get("members", [])
            chosen = [str(a) for a in raw_cur] if isinstance(raw_cur, list) else []
            if alias == "auto":
                chosen = []
            elif alias in chosen:
                chosen = [a for a in chosen if a != alias]
            else:
                chosen.append(alias)
            store.set_members(sid, chosen)
        models = service.list_models() if service is not None else []
        raw_selected = store.get_settings(sid).get("members", [])
        selected = [str(a) for a in raw_selected] if isinstance(raw_selected, list) else []
        await query.edit_message_text(
            "Attach council models:",
            reply_markup=kb(render.models_layout(models, selected)))
        return

    if data.startswith("set:"):
        key = data.split(":", 1)[1]
        cur = store.get_settings(sid)
        if key == "tool":
            store.set_setting(sid, "tool", "ask" if cur["tool"] == "council" else "council")
        elif key == "sensitivity":
            store.set_setting(sid, "sensitivity",
                              "public" if cur["sensitivity"] == "sensitive" else "sensitive")
        elif key == "mode":
            store.set_setting(sid, "mode", _cycle(_MODES, cur["mode"]))
        elif key == "size":
            store.set_setting(sid, "size", _cycle(_SIZES, cur["size"]))
        elif key == "show_footer":
            store.set_setting(sid, "show_footer", 0 if cur["show_footer"] else 1)
        await query.edit_message_text(
            "Settings (this session):",
            reply_markup=kb(render.settings_layout(store.get_settings(sid))))
        return


def register(application: Application[Any, Any, Any, Any, Any, Any]) -> None:
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_cmd))
    application.add_handler(CommandHandler("new", new_session))
    application.add_handler(CommandHandler("sessions", sessions_cmd))
    application.add_handler(CommandHandler("rename", rename_cmd))
    application.add_handler(CommandHandler("settings", settings_cmd))
    application.add_handler(CommandHandler("ask", ask_cmd))
    application.add_handler(CommandHandler("council", council_cmd))
    application.add_handler(CommandHandler("approve", approve_cmd))
    application.add_handler(CommandHandler("deny", deny_cmd))
    application.add_handler(CommandHandler("pending", pending_cmd))
    application.add_handler(CallbackQueryHandler(on_callback))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))
