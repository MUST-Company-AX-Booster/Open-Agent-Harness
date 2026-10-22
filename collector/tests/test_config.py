from collector import config


def test_load_parses_groups_and_defaults(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://x")
    monkeypatch.setenv("TELEGRAM_GROUPS", "-5597538916, -5359244781,")
    monkeypatch.delenv("BACKFILL_LIMIT", raising=False)
    monkeypatch.delenv("TELEGRAM_API_ID", raising=False)
    config.load.cache_clear()

    cfg = config.load()

    assert cfg.telegram_groups == [-5597538916, -5359244781]
    assert cfg.backfill_limit == 2000
    assert cfg.telegram_api_id == 0
