"""Settings from the environment, read once."""
from __future__ import annotations

import functools
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    database_url: str
    slack_bot_token: str
    telegram_api_id: int
    telegram_api_hash: str
    telegram_session_string: str
    telegram_groups: list[int]
    backfill_limit: int


@functools.cache
def load() -> Settings:
    env = os.environ
    return Settings(
        database_url=env["DATABASE_URL"],
        slack_bot_token=env.get("SLACK_BOT_TOKEN", ""),
        telegram_api_id=int(env.get("TELEGRAM_API_ID") or 0),
        telegram_api_hash=env.get("TELEGRAM_API_HASH", ""),
        telegram_session_string=env.get("TELEGRAM_SESSION_STRING", ""),
        telegram_groups=[int(g) for g in env.get("TELEGRAM_GROUPS", "").split(",") if g.strip()],
        backfill_limit=int(env.get("BACKFILL_LIMIT") or 2000),
    )
