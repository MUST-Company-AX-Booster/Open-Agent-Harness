from datetime import UTC, datetime, timedelta

import pytest

from collector import slack_poll

BOT = "UBOT"


def ts(days_ago: float = 0, hours_ago: float = 0) -> str:
    t = datetime.now(tz=UTC) - timedelta(days=days_ago, hours=hours_ago)
    return f"{t.timestamp():.6f}"


class FakeDB:
    def __init__(self, last_ts=None):
        self.state = [{"last_ts": last_ts}] if last_ts else []
        self.rows: list[tuple] = []
        self.writes: list[tuple] = []

    async def fetch(self, sql, args=()):
        return self.state

    async def execute(self, sql, args=()):
        self.writes.append((sql, args))

    async def executemany(self, sql, rows):
        self.rows.extend(rows)


@pytest.fixture
def fake(monkeypatch):
    """Install a fake DB and a fake Slack API. Returns (db, calls, responses)."""
    slack_poll._user_names.clear()
    db = FakeDB()
    calls: list[tuple[str, dict]] = []
    responses: dict = {"users.info": {"ok": True, "user": {"profile": {"real_name": "Ben"}}}}

    async def call(method, params):
        calls.append((method, params))
        r = responses[method]
        return r(params) if callable(r) else r

    monkeypatch.setattr(slack_poll, "call", call)
    for name in ("fetch", "execute", "executemany"):
        monkeypatch.setattr(slack_poll.db, name, getattr(db, name))
    return db, calls, responses


def msg(t, text="배포는 금요일에 합니다", user="U1", **extra):
    return {"ts": t, "user": user, "text": text, **extra}


def test_should_index_skips_bots_system_and_short():
    assert slack_poll.should_index(msg(ts()))
    assert not slack_poll.should_index(msg(ts(), bot_id="B1"))
    assert not slack_poll.should_index(msg(ts(), subtype="channel_join"))
    assert not slack_poll.should_index(msg(ts(), text="ok"))
    assert not slack_poll.should_index({"ts": ts(), "text": "no user here"})


async def test_resolve_text_replaces_mentions_and_links(fake):
    out = await slack_poll.resolve_text("<@U1> see <https://x.io|the doc> in <#C1|general>")
    assert out == "@Ben see the doc in #general"


async def test_first_poll_backfills_30_days_and_sets_watermark(fake):
    db, calls, responses = fake
    newest = ts(hours_ago=1)
    responses["conversations.history"] = {"ok": True, "messages": [msg(newest), msg(ts(days_ago=2))]}

    stored = await slack_poll.poll_channel("C1", "general", BOT)

    assert stored == 2
    oldest = float(calls[0][1]["oldest"])
    assert abs(oldest - float(ts(days_ago=30))) < 60
    sql, args = db.writes[-1]
    assert "slack_sync_state" in sql and newest in args


async def test_bot_mentions_are_not_stored(fake):
    db, _, responses = fake
    responses["conversations.history"] = {
        "ok": True,
        "messages": [msg(ts(), text=f"<@{BOT}> 배포 언제 해요?"), msg(ts(hours_ago=2))],
    }

    stored = await slack_poll.poll_channel("C1", "general", BOT)

    assert stored == 1
    assert all(BOT not in row[5] and "@" not in row[5] for row in db.rows)


async def test_reply_on_older_thread_is_stored(fake):
    db, calls, responses = fake
    db.state = [{"last_ts": ts(hours_ago=1)}]
    parent_ts, reply_ts = ts(days_ago=3), ts(hours_ago=0.1)
    responses["conversations.history"] = {
        "ok": True,
        "messages": [msg(parent_ts, reply_count=1, latest_reply=reply_ts, thread_ts=parent_ts)],
    }
    responses["conversations.replies"] = {
        "ok": True,
        "messages": [
            msg(parent_ts, thread_ts=parent_ts),
            msg(reply_ts, text="금요일 OK 입니다", thread_ts=parent_ts),
        ],
    }

    await slack_poll.poll_channel("C1", "general", BOT)

    stored_ts = {row[1] for row in db.rows}
    assert reply_ts in stored_ts
    assert ("conversations.replies", {"channel": "C1", "ts": parent_ts, "limit": 200}) in calls


async def test_old_threads_without_new_replies_are_not_refetched(fake):
    db, calls, responses = fake
    db.state = [{"last_ts": ts(hours_ago=1)}]
    parent_ts = ts(days_ago=3)
    responses["conversations.history"] = {
        "ok": True,
        "messages": [msg(parent_ts, reply_count=1, latest_reply=ts(days_ago=2), thread_ts=parent_ts)],
    }

    await slack_poll.poll_channel("C1", "general", BOT)

    assert not any(method == "conversations.replies" for method, _ in calls)


async def test_slack_error_does_not_advance_watermark(fake):
    db, _, responses = fake
    responses["conversations.history"] = {"ok": False, "error": "ratelimited"}

    with pytest.raises(slack_poll.SlackError):
        await slack_poll.poll_channel("C1", "general", BOT)

    assert db.writes == [] and db.rows == []


async def test_poll_all_lists_only_channels_and_survives_one_failure(fake):
    db, calls, responses = fake
    responses["auth.test"] = {"ok": True, "user_id": BOT}
    responses["users.conversations"] = {
        "ok": True,
        "channels": [{"id": "C_BAD", "name": "bad"}, {"id": "C_OK", "name": "ok"}],
    }
    responses["conversations.history"] = lambda p: (
        {"ok": False, "error": "not_in_channel"} if p["channel"] == "C_BAD"
        else {"ok": True, "messages": [msg(ts())]}
    )

    total = await slack_poll.poll_all()

    assert total == 1
    list_params = next(p for m, p in calls if m == "users.conversations")
    assert list_params["types"] == "public_channel,private_channel"


async def test_poll_all_raises_when_auth_test_fails(fake):
    db, _, responses = fake
    responses["auth.test"] = {"ok": False, "error": "invalid_auth"}

    with pytest.raises(slack_poll.SlackError):
        await slack_poll.poll_all()

    assert db.writes == [] and db.rows == []


async def test_failed_user_lookup_is_not_cached(fake):
    _, _, responses = fake
    responses["users.info"] = {"ok": False, "error": "ratelimited"}
    assert await slack_poll.user_name("U9") == "U9"

    responses["users.info"] = {"ok": True, "user": {"profile": {"real_name": "Ben"}}}
    assert await slack_poll.user_name("U9") == "Ben"


async def test_bot_mention_with_label_is_not_stored(fake):
    db, _, responses = fake
    responses["conversations.history"] = {
        "ok": True, "messages": [msg(ts(), text=f"<@{BOT}|openagent> 배포 언제 해요?")],
    }

    assert await slack_poll.poll_channel("C1", "general", BOT) == 0
    assert db.rows == []
