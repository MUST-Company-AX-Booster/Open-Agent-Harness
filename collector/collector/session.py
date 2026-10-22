"""Telethon client factory. The session is a string (env), never a file."""
from __future__ import annotations

from telethon import TelegramClient
from telethon.sessions import StringSession

from collector.config import load


def build_client(session: str | None = None) -> TelegramClient:
    cfg = load()
    raw = session if session is not None else cfg.telegram_session_string
    return TelegramClient(StringSession(raw or None), cfg.telegram_api_id, cfg.telegram_api_hash)


async def connected_client() -> TelegramClient:
    client = build_client()
    await client.connect()
    if not await client.is_user_authorized():
        await client.disconnect()
        raise RuntimeError(
            "TELEGRAM_SESSION_STRING is not authorized (revoked?). "
            "Re-run: docker compose run --rm collector python -m collector.login"
        )
    return client
