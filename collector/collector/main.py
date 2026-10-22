"""Collector entrypoint: Slack polling + Telegram listening, forever."""
from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from collector import slack_poll, telegram
from collector.config import load
from collector.session import connected_client

logger = logging.getLogger("collector")

SLACK_POLL_SECONDS = 300
PRUNE_SECONDS = 24 * 3600


async def every(seconds: int, fn: Callable[[], Awaitable[object]], name: str) -> None:
    while True:
        try:
            await fn()
        except Exception:
            logger.exception("%s failed", name)
        await asyncio.sleep(seconds)


async def telegram_job() -> None:
    try:
        await telegram.run_collector(await connected_client())
    except Exception:
        logger.exception("Telegram collector failed to start; Slack polling continues")


async def main() -> None:
    cfg = load()
    jobs = []
    if cfg.slack_bot_token:
        jobs += [
            every(SLACK_POLL_SECONDS, slack_poll.poll_all, "slack poll"),
            every(PRUNE_SECONDS, slack_poll.prune, "slack prune"),
        ]
    if cfg.telegram_session_string:
        jobs.append(telegram_job())
    if not jobs:
        raise SystemExit("Nothing to collect: set SLACK_BOT_TOKEN and/or TELEGRAM_SESSION_STRING")
    await asyncio.gather(*jobs)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
