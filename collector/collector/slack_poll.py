"""Polls Slack channel history into Postgres.

Polling, not events: Hermes holds this app's Socket Mode connection, and Slack
splits events between connections, so a second listener would see only some
messages.
"""
from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from collector import db
from collector.config import load

logger = logging.getLogger(__name__)

SLACK_API = "https://slack.com/api"
SKIP_SUBTYPES = {
    "channel_join", "channel_leave", "channel_topic", "channel_purpose",
    "channel_name", "channel_archive", "channel_unarchive", "bot_message",
    "message_changed", "message_deleted", "thread_broadcast_join",
}
MIN_TEXT_CHARS = 3
FIRST_BACKFILL_DAYS = 30
# ponytail: every poll re-reads the last week so new replies on week-old threads
# are caught; replies on threads older than this are missed. Raise if that bites.
THREAD_LOOKBACK_DAYS = 7
RETENTION_DAYS = 180

UPSERT = """
insert into slack_messages
    (channel_id, message_ts, channel_name, sender_id, sender_name, text, thread_ts, ts)
values (%s, %s, %s, %s, %s, %s, %s, %s)
on conflict (channel_id, message_ts) do nothing
"""

SAVE_STATE = """
insert into slack_sync_state (channel_id, channel_name, backfilled_to, last_ts, updated_at)
values (%s, %s, to_timestamp(%s), %s, now())
on conflict (channel_id) do update
    set channel_name = excluded.channel_name,
        backfilled_to = least(slack_sync_state.backfilled_to, excluded.backfilled_to),
        last_ts = excluded.last_ts,
        updated_at = now()
"""

MENTION = re.compile(r"<@([A-Z0-9]+)(?:\|[^>]*)?>")
LINK = re.compile(r"<(https?://[^|>]+)(?:\|([^>]*))?>")
CHANNEL_REF = re.compile(r"<#([A-Z0-9]+)(?:\|([^>]*))?>")

_user_names: dict[str, str] = {}


class SlackError(RuntimeError):
    """A Slack call failed; the channel's watermark must not move."""


async def call(method: str, params: dict[str, Any]) -> dict[str, Any]:
    headers = {"Authorization": f"Bearer {load().slack_bot_token}"}
    async with httpx.AsyncClient(timeout=20) as client:
        for _ in range(3):
            res = await client.get(f"{SLACK_API}/{method}", headers=headers, params=params)
            if res.status_code != 429:
                return res.json()
            await asyncio.sleep(int(res.headers.get("Retry-After", "5")))
    return {"ok": False, "error": "ratelimited"}


async def pages(method: str, params: dict[str, Any], key: str) -> AsyncIterator[dict[str, Any]]:
    cursor = ""
    while True:
        body = await call(method, {**params, **({"cursor": cursor} if cursor else {})})
        if not body.get("ok"):
            raise SlackError(f"{method} failed: {body.get('error')}")
        for item in body.get(key, []):
            yield item
        cursor = (body.get("response_metadata") or {}).get("next_cursor", "")
        if not cursor:
            return


def should_index(message: dict[str, Any]) -> bool:
    if message.get("subtype") in SKIP_SUBTYPES:
        return False
    if message.get("bot_id") or message.get("app_id"):
        return False
    if not message.get("user"):
        return False
    return len((message.get("text") or "").strip()) >= MIN_TEXT_CHARS


def ts_to_datetime(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=UTC)


async def user_name(user_id: str) -> str:
    if user_id not in _user_names:
        body = await call("users.info", {"user": user_id})
        if not body.get("ok"):
            return user_id  # don't cache a failed lookup
        user = body.get("user") or {}
        _user_names[user_id] = (
            (user.get("profile") or {}).get("real_name") or user.get("name") or user_id
        )
    return _user_names[user_id]


async def resolve_text(text: str) -> str:
    """Turn Slack markup into readable, searchable text."""
    for user_id in set(MENTION.findall(text)):
        text = text.replace(f"<@{user_id}>", f"@{await user_name(user_id)}")
    text = MENTION.sub(lambda m: f"@{m.group(0).split('|')[-1].rstrip('>')}", text)
    text = CHANNEL_REF.sub(lambda m: f"#{m.group(2) or m.group(1)}", text)
    text = LINK.sub(lambda m: m.group(2) or m.group(1), text)
    return text.strip()


async def to_rows(messages: list[dict[str, Any]], channel_id: str, name: str, bot_uid: str) -> list[tuple]:
    rows = []
    for m in messages:
        if not should_index(m):
            continue
        if bot_uid in MENTION.findall(m["text"]):
            continue  # a question to the bot is not team discussion
        rows.append((
            channel_id, m["ts"], name, m["user"], await user_name(m["user"]),
            await resolve_text(m["text"]), m.get("thread_ts"), ts_to_datetime(m["ts"]),
        ))
    return rows


async def poll_channel(channel_id: str, name: str, bot_uid: str) -> int:
    """Store new messages and new thread replies for one channel. Returns rows stored."""
    state = await db.fetch("select last_ts from slack_sync_state where channel_id = %s", (channel_id,))
    last_ts = state[0]["last_ts"] if state else None
    now = datetime.now(tz=UTC)
    if last_ts:
        oldest = min(float(last_ts), (now - timedelta(days=THREAD_LOOKBACK_DAYS)).timestamp())
    else:
        oldest = (now - timedelta(days=FIRST_BACKFILL_DAYS)).timestamp()

    seen = [m async for m in pages(
        "conversations.history",
        {"channel": channel_id, "oldest": f"{oldest:.6f}", "limit": 200},
        "messages",
    )]
    for parent in list(seen):
        if parent.get("reply_count") and float(parent.get("latest_reply", 0)) > float(last_ts or 0):
            async for reply in pages(
                "conversations.replies",
                {"channel": channel_id, "ts": parent["ts"], "limit": 200},
                "messages",
            ):
                if reply["ts"] != parent["ts"]:
                    seen.append(reply)

    rows = await to_rows(seen, channel_id, name, bot_uid)
    if rows:
        await db.executemany(UPSERT, rows)
    newest = max((m["ts"] for m in seen), key=float, default=last_ts)
    await db.execute(SAVE_STATE, (channel_id, name, oldest, newest))
    return len(rows)


async def poll_all() -> int:
    """Poll every channel the bot is in. One bad channel does not stop the rest."""
    auth = await call("auth.test", {})
    if not auth.get("ok"):
        raise SlackError(f"auth.test failed: {auth.get('error')}")  # bot filter needs bot_uid
    bot_uid = auth["user_id"]
    total = 0
    async for ch in pages(
        "users.conversations",
        {"types": "public_channel,private_channel", "exclude_archived": "true", "limit": 200},
        "channels",
    ):
        try:
            total += await poll_channel(ch["id"], ch.get("name") or ch["id"], bot_uid)
        except Exception:
            logger.exception("Polling #%s failed", ch.get("name") or ch["id"])
    logger.info("Slack poll stored %d messages", total)
    return total


async def prune() -> None:
    """Keep the database from filling up (a full disk makes Postgres refuse every write)."""
    await db.execute(
        "delete from slack_messages where ts < now() - make_interval(days => %s)",
        (RETENTION_DAYS,),
    )
