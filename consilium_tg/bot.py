from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.ext import Application

from consilium_tg import handlers
from consilium_tg.access import AccessStore
from consilium_tg.config import Settings, load_settings
from consilium_tg.store import BotStore

if TYPE_CHECKING:
    from telegram.ext import ContextTypes

    from consilium_chat.council_service import CouncilService

_log = logging.getLogger(__name__)

# PTB's Application is generic over six type parameters; this bot does not depend on
# any of them, so parameterize with Any to satisfy strict typing without the churn.
_App = Application[Any, Any, Any, Any, Any, Any]


def _build_service() -> CouncilService | None:
    try:
        from consilium_chat.council_service import CouncilService

        return CouncilService.build()
    except Exception:  # noqa: BLE001 - a missing proxy/key must not block app creation
        _log.warning("council service unavailable at startup", exc_info=True)
        return None


async def _on_error(  # pragma: no cover - PTB error hook
    update: object, context: ContextTypes.DEFAULT_TYPE
) -> None:
    _log.error("handler error", exc_info=context.error)
    if not isinstance(update, Update):
        return
    with contextlib.suppress(Exception):
        if update.callback_query is not None:
            await update.callback_query.answer("Something went wrong.")
        elif update.effective_message is not None:
            await update.effective_message.reply_text("⚠️ Something went wrong.")


def build_application(
    *,
    settings: Settings | None = None,
    service: CouncilService | None = None,
    store: BotStore | None = None,
    access: AccessStore | None = None,
) -> _App:
    settings = settings or load_settings()
    store = store or BotStore(settings.db_path, default_sensitivity=settings.default_sensitivity)
    access = access or AccessStore(settings.access_path, owner_id=settings.owner_id)
    if service is None:
        service = _build_service()
    app = Application.builder().token(settings.bot_token).concurrent_updates(True).build()
    app.bot_data.update(
        {"settings": settings, "store": store, "service": service, "access": access}
    )
    handlers.register(app)
    app.add_error_handler(_on_error)
    return app


def run(app: _App | None = None) -> None:  # pragma: no cover - starts the polling loop
    (app or build_application()).run_polling()
