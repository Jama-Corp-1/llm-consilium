from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from consilium import env_file

_CFG = Path.home() / ".config" / "consilium"


@dataclass(frozen=True)
class Settings:
    bot_token: str = ""
    owner_id: int | None = None
    db_path: str = str(_CFG / "tg.db")
    access_path: str = str(_CFG / "tg_access.json")
    context_turns: int = 8
    context_char_budget: int = 6000
    default_sensitivity: str = "sensitive"


def load_settings(env: dict[str, str] | None = None) -> Settings:
    if env is None:
        env = {**env_file.load(), **os.environ}
    d = Settings()
    owner = env.get("TELEGRAM_OWNER_ID")
    return Settings(
        bot_token=env.get("TELEGRAM_BOT_TOKEN", d.bot_token),
        owner_id=int(owner) if owner else None,
        db_path=env.get("CONSILIUM_TG_DB", d.db_path),
        access_path=env.get("CONSILIUM_TG_ACCESS", d.access_path),
        context_turns=int(env.get("CONSILIUM_TG_TURNS", d.context_turns)),
        context_char_budget=int(env.get("CONSILIUM_TG_BUDGET", d.context_char_budget)),
        default_sensitivity=env.get("CONSILIUM_TG_SENSITIVITY", d.default_sensitivity),
    )
