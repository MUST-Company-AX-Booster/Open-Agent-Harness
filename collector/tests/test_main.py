from types import SimpleNamespace

from collector import main as m


async def test_telegram_failure_does_not_stop_slack(monkeypatch):
    ran = []

    async def fake_every(seconds, fn, name):
        ran.append(name)

    async def bad_connect():
        raise RuntimeError("revoked")

    monkeypatch.setattr(m, "load", lambda: SimpleNamespace(slack_bot_token="x", telegram_session_string="s"))
    monkeypatch.setattr(m, "every", fake_every)
    monkeypatch.setattr(m, "connected_client", bad_connect)

    await m.main()

    assert "slack poll" in ran
