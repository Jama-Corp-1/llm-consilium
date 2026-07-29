from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Settings:
    host: str = "127.0.0.1"
    port: int = 8080
    chat_db_path: str = str(Path.home() / ".config" / "consilium" / "chat.db")
    context_turns: int = 8
    context_char_budget: int = 6000


def load_settings(env: Mapping[str, str] | None = None) -> Settings:
    env = os.environ if env is None else env
    d = Settings()
    return Settings(
        host=env.get("CONSILIUM_CHAT_HOST", d.host),
        port=int(env.get("CONSILIUM_CHAT_PORT", d.port)),
        chat_db_path=env.get("CONSILIUM_CHAT_DB", d.chat_db_path),
        context_turns=int(env.get("CONSILIUM_CHAT_TURNS", d.context_turns)),
        context_char_budget=int(env.get("CONSILIUM_CHAT_BUDGET", d.context_char_budget)),
    )
