"""asyncpg connection pool and query helpers."""

from __future__ import annotations

import json
from typing import Any

from app.core.logging import get_logger

logger = get_logger(__name__)

_pool: Any = None


async def init_pool(database_url: str, *, min_size: int = 2, max_size: int = 10) -> Any:
    global _pool
    if _pool is not None:
        return _pool

    import asyncpg

    async def _init_connection(connection):
        # Register vector and JSONB codecs once per connection.
        await connection.set_type_codec(
            "jsonb", encoder=json.dumps, decoder=json.loads, schema="pg_catalog"
        )

    _pool = await asyncpg.create_pool(
        dsn=database_url,
        min_size=min_size,
        max_size=max_size,
        command_timeout=60,
        init=_init_connection,
        server_settings={"application_name": "clauseguard"},
    )
    logger.info("database pool created", extra={"min_size": min_size, "max_size": max_size})
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None


def get_pool() -> Any:
    if _pool is None:
        raise RuntimeError("The database pool has not been initialised.")
    return _pool


async def fetch_all(query: str, *args) -> list[dict]:
    async with get_pool().acquire() as connection:
        rows = await connection.fetch(query, *args)
    return [dict(row) for row in rows]


async def fetch_one(query: str, *args) -> dict | None:
    async with get_pool().acquire() as connection:
        row = await connection.fetchrow(query, *args)
    return dict(row) if row else None


async def fetch_value(query: str, *args) -> Any:
    async with get_pool().acquire() as connection:
        return await connection.fetchval(query, *args)


async def execute(query: str, *args) -> str:
    async with get_pool().acquire() as connection:
        return await connection.execute(query, *args)


async def health_check() -> bool:
    try:
        return await fetch_value("SELECT 1") == 1
    except Exception:  # noqa: BLE001
        return False


def to_vector_literal(vector: list[float]) -> str:
    """pgvector accepts a bracketed literal; asyncpg has no native codec for it."""
    return "[" + ",".join(f"{v:.7f}" for v in vector) + "]"
