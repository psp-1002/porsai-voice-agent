"""Database connection pool.

One pool per process. Every agent session borrows a connection only for the
duration of one query/transaction, so many calls can share a small pool.
"""
import os

import asyncpg

_pool: asyncpg.Pool | None = None


async def get_pool() -> asyncpg.Pool:
    global _pool
    if _pool is None:
        _pool = await asyncpg.create_pool(
            dsn=os.environ["DATABASE_URL"],
            min_size=1,
            max_size=int(os.getenv("DB_POOL_SIZE", "10")),
            # Supabase's pooler can route a session to different backends;
            # disabling the prepared-statement cache avoids "prepared statement
            # does not exist" errors.
            statement_cache_size=0,
        )
    return _pool


async def close_pool() -> None:
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
