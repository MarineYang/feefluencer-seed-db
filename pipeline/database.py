from contextlib import asynccontextmanager
from typing import AsyncGenerator

import asyncpg
from loguru import logger

from config import settings

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            settings.database_url,
            min_size=2,
            max_size=10,
            command_timeout=60,
        )
        logger.info("DB 커넥션 풀 생성 완료")
    return _pool


async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None


@asynccontextmanager
async def get_conn() -> AsyncGenerator[asyncpg.Connection, None]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        yield conn


@asynccontextmanager
async def get_transaction() -> AsyncGenerator[asyncpg.Connection, None]:
    async with get_conn() as conn:
        async with conn.transaction():
            yield conn


async def fetch_one(query: str, *args):
    async with get_conn() as conn:
        return await conn.fetchrow(query, *args)


async def fetch_all(query: str, *args):
    async with get_conn() as conn:
        return await conn.fetch(query, *args)


async def execute(query: str, *args):
    async with get_conn() as conn:
        return await conn.execute(query, *args)


async def executemany(query: str, args_list: list):
    async with get_conn() as conn:
        return await conn.executemany(query, args_list)
