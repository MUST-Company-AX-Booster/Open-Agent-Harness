"""One-time Telegram login and group-id discovery.

    docker compose run --rm collector python -m collector.login          # prints TELEGRAM_SESSION_STRING
    docker compose run --rm collector python -m collector.login --list   # lists groups and ids
"""
from __future__ import annotations

import argparse
import asyncio

from telethon.utils import get_display_name

from collector.session import build_client, connected_client


async def do_login() -> int:
    client = build_client(session="")
    await client.start()  # prompts for phone / code / 2FA
    me = await client.get_me()
    print(f"\nLogged in as {(me.first_name or '').strip()} @{me.username or '-'}")
    print("\nPut this in .env. It is a full credential — treat it like a password:\n")
    print(f"TELEGRAM_SESSION_STRING={client.session.save()}")
    await client.disconnect()
    return 0


async def do_list() -> int:
    client = await connected_client()
    async for dialog in client.iter_dialogs():
        if dialog.is_group or dialog.is_channel:
            print(f"{dialog.id:>16}  {get_display_name(dialog.entity)}")
    await client.disconnect()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list", action="store_true", help="list groups and ids")
    args = parser.parse_args()
    return asyncio.run(do_list() if args.list else do_login())


if __name__ == "__main__":
    raise SystemExit(main())
