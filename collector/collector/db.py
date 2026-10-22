"""Async Postgres pool and three helpers. Connects as the `collector` DB user."""
from __future__ import annotations

from typing import Any

from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

from collector.config import load

_pool: AsyncConnectionPool | None = None


async def pool() -> AsyncConnectionPool:
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            load().database_url, min_size=1, max_size=4, open=False,
            kwargs={"row_factory": dict_row},
        )
        await _pool.open()
    return _pool


async def fetch(sql: str, args: tuple = ()) -> list[dict[str, Any]]:
    async with (await pool()).connection() as conn:
        cur = await conn.execute(sql, args)
        return await cur.fetchall()


async def execute(sql: str, args: tuple = ()) -> None:
    async with (await pool()).connection() as conn:
        await conn.execute(sql, args)


async def executemany(sql: str, rows: list[tuple]) -> None:
    async with (await pool()).connection() as conn, conn.cursor() as cur:
        await cur.executemany(sql, rows)
