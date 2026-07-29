from __future__ import annotations

from collections.abc import Sequence

from consilium_tg import bot
from consilium_tg.bot import _App, build_application
from consilium_tg.config import load_settings


def _run(app: _App) -> None:  # pragma: no cover - starts the polling loop
    bot.run(app)


def main(argv: Sequence[str] | None = None) -> int:
    settings = load_settings()
    if not settings.bot_token:
        print("No TELEGRAM_BOT_TOKEN — set it via `python -m consilium init` or "
              "~/.config/consilium/.env, then retry.")
        return 1
    _run(build_application())
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
