"""Pulls messages from the watched Telegram groups into Postgres.

Live handler is the fast path; a periodic catch-up from the watermark is the
safety net for a dropped connection. Media-only messages are skipped.
"""
from __future__ import annotations

import asyncio
import logging

from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.utils import get_display_name

from collector import db
from collector.config import load

logger = logging.getLogger(__name__)

CATCHUP_INTERVAL_SECONDS = 300

UPSERT = """
insert into tg_messages
    (chat_id, message_id, chat_title, sender_id, sender_name, text, reply_to, ts)
values (%s, %s, %s, %s, %s, %s, %s, %s)
on conflict (chat_id, message_id) do nothing
"""


async def watermark(chat_id: int) -> int:
    rows = await db.fetch("select last_message_id from tg_sync_state where chat_id = %s", (chat_id,))
    return rows[0]["last_message_id"] if rows else 0


async def set_watermark(chat_id: int, message_id: int) -> None:
    await db.execute(
        """
        insert into tg_sync_state (chat_id, last_message_id, updated_at)
        values (%s, %s, now())
        on conflict (chat_id) do update
            set last_message_id = greatest(tg_sync_state.last_message_id, excluded.last_message_id),
                updated_at = now()
        """,
        (chat_id, message_id),
    )


def row(msg, chat_title: str, sender_name: str | None) -> tuple | None:
    text = (msg.message or "").strip()
    if not text:
        return None  # media-only: nothing to search on
    return (
        msg.chat_id, msg.id, chat_title,
        getattr(msg.sender_id, "user_id", msg.sender_id),
        sender_name, text, msg.reply_to_msg_id, msg.date,
    )


async def store_live(message, chat_title: str, sender_name: str | None) -> None:
    """Idempotent insert. Never moves the watermark: only catch-up owns it, so a gap
    left by a dropped connection is still refilled."""
    r = row(message, chat_title, sender_name)
    if r:
        await db.executemany(UPSERT, [r])


async def sync_group(client: TelegramClient, chat_id: int, limit: int | None = None) -> int:
    """Store messages newer than the watermark. Returns how many were stored."""
    since = await watermark(chat_id)
    limit = limit if limit is not None else load().backfill_limit
    try:
        entity = await client.get_entity(chat_id)
        title = get_display_name(entity)
        names: dict[int, str] = {}
        rows: list[tuple] = []
        highest = since  # reverse=True: oldest first, so a limit cut never skips unstored messages
        async for msg in client.iter_messages(entity, limit=limit, min_id=since, reverse=True):
            if msg.sender_id and msg.sender_id not in names:
                sender = await msg.get_sender()
                names[msg.sender_id] = get_display_name(sender) if sender else str(msg.sender_id)
            r = row(msg, title, names.get(msg.sender_id))
            if r:
                rows.append(r)
            highest = max(highest, msg.id)
    except FloodWaitError as e:
        # Telegram asked us to slow down. Wait, keep the watermark, retry next round.
        logger.warning("Flood wait %ss on %s", e.seconds, chat_id)
        await asyncio.sleep(e.seconds)
        return 0
    if rows:
        await db.executemany(UPSERT, rows)
    # Also on a pass with nothing new: updated_at then means "fully synced at", which goal scoring
    # reads as proof of coverage before it awards points for a quiet day.
    await set_watermark(chat_id, highest)
    logger.info("Synced %s (%s): %d new", title, chat_id, len(rows))
    return len(rows)


async def sync_all(client: TelegramClient, limit: int | None = None) -> int:
    total = 0
    for chat_id in load().telegram_groups:
        try:
            total += await sync_group(client, chat_id, limit)
        except Exception:
            logger.exception("Sync of %s failed", chat_id)
    return total


async def run_collector(client: TelegramClient) -> None:
    """Live handler plus periodic catch-up. Runs forever."""
    watched = load().telegram_groups
    if not watched:
        logger.warning("TELEGRAM_GROUPS is empty — Telegram collector idle")
        return

    @client.on(events.NewMessage(chats=watched))
    async def _on_message(event) -> None:  # pragma: no cover - needs live Telegram
        try:
            sender = await event.get_sender()
            await store_live(event.message, get_display_name(await event.get_chat()),
                             get_display_name(sender) if sender else None)
        except Exception:
            logger.exception("Failed to store live message")

    logger.info("Telegram collector watching %d groups", len(watched))
    await sync_all(client)
    while True:
        await asyncio.sleep(CATCHUP_INTERVAL_SECONDS)
        if not client.is_connected():
            logger.warning("Telegram disconnected, reconnecting")
            await client.connect()  # raises on failure so main.telegram_job logs it loudly
        await sync_all(client, limit=200)
