import asyncio
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.errors import FloodWaitError

from collector import telegram


def tg_msg(text, id_=10):
    return SimpleNamespace(
        message=text, chat_id=-100, id=id_, sender_id=7, reply_to_msg_id=None,
        date=datetime(2026, 9, 30, tzinfo=UTC),
    )


def test_row_skips_media_only_messages():
    assert telegram.row(tg_msg(""), "Team", "Ben") is None
    assert telegram.row(tg_msg("   "), "Team", "Ben") is None


def test_row_keeps_fields_in_table_order():
    assert telegram.row(tg_msg(" 배포 완료 "), "Team", "Ben") == (
        -100, 10, "Team", 7, "Ben", "배포 완료", None, datetime(2026, 9, 30, tzinfo=UTC),
    )


async def test_flood_wait_sleeps_and_keeps_watermark(monkeypatch):
    slept, watermarks = [], []

    class Client:
        async def get_entity(self, chat_id):
            return SimpleNamespace(title="Team")

        def iter_messages(self, *a, **k):
            raise FloodWaitError(request=None, capture=42)

    async def fake_sleep(s):
        slept.append(s)

    async def fake_watermark(chat_id):
        return 5

    async def fake_set_watermark(chat_id, mid):
        watermarks.append(mid)

    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(telegram, "watermark", fake_watermark)
    monkeypatch.setattr(telegram, "set_watermark", fake_set_watermark)
    monkeypatch.setattr(telegram, "get_display_name", lambda e: "Team")

    stored = await telegram.sync_group(Client(), -100, limit=10)

    assert stored == 0
    assert slept == [42]
    assert watermarks == []


async def test_store_live_writes_row_without_touching_watermark(monkeypatch):
    written = []

    async def fake_executemany(sql, rows):
        written.extend(rows)

    async def boom(*a):
        raise AssertionError("live handler must not move the watermark")

    monkeypatch.setattr(telegram.db, "executemany", fake_executemany)
    monkeypatch.setattr(telegram, "set_watermark", boom)

    await telegram.store_live(tg_msg("hello"), "Team", "Ben")

    assert [r[5] for r in written] == ["hello"]


async def no_sender():
    return None


async def test_sync_group_reverse_so_watermark_only_covers_stored(monkeypatch):
    wm, stored = [], []

    class Client:
        async def get_entity(self, chat_id):
            return SimpleNamespace(title="Team")

        def iter_messages(self, entity, limit, min_id, reverse=False):
            ids = list(range(min_id + 1, 6))  # 5 pending
            ids = ids if reverse else ids[::-1]  # Telethon: newest first unless reverse
            async def gen():
                for i in ids[:limit]:
                    m = tg_msg("m", i)
                    m.get_sender = no_sender
                    yield m
            return gen()

    async def fake_watermark(c):
        return 0

    async def fake_set(c, m):
        wm.append(m)

    async def fake_many(sql, rows):
        stored.extend(rows)

    monkeypatch.setattr(telegram, "watermark", fake_watermark)
    monkeypatch.setattr(telegram, "set_watermark", fake_set)
    monkeypatch.setattr(telegram.db, "executemany", fake_many)
    monkeypatch.setattr(telegram, "get_display_name", lambda e: "Team")

    await telegram.sync_group(Client(), -100, limit=2)

    assert [r[1] for r in stored] == [1, 2]
    assert wm == [2]


async def test_catchup_reconnects_when_disconnected(monkeypatch):
    calls = []

    class Client:
        def is_connected(self):
            return False

        async def connect(self):
            calls.append("connect")

        def on(self, *a):
            return lambda f: f

    async def fake_sync_all(client, limit=None):
        calls.append("sync")
        if len(calls) > 3:
            raise asyncio.CancelledError

    async def fake_sleep(s):
        pass

    monkeypatch.setattr(telegram, "sync_all", fake_sync_all)
    monkeypatch.setattr(telegram.asyncio, "sleep", fake_sleep)
    monkeypatch.setattr(telegram, "load", lambda: SimpleNamespace(telegram_groups=[-100]))

    with pytest.raises(asyncio.CancelledError):
        await telegram.run_collector(Client())

    assert calls[:3] == ["sync", "connect", "sync"]


async def test_sync_group_marks_synced_even_with_nothing_new(monkeypatch):
    wm = []

    class Client:
        async def get_entity(self, chat_id):
            return SimpleNamespace(title="Team")

        def iter_messages(self, entity, limit, min_id, reverse=False):
            async def gen():
                return
                yield
            return gen()

    async def fake_watermark(c):
        return 7

    async def fake_set(c, m):
        wm.append(m)

    monkeypatch.setattr(telegram, "watermark", fake_watermark)
    monkeypatch.setattr(telegram, "set_watermark", fake_set)
    monkeypatch.setattr(telegram, "get_display_name", lambda e: "Team")

    await telegram.sync_group(Client(), -100)

    assert wm == [7]  # watermark unchanged, but updated_at refreshed
