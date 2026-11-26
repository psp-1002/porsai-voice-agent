"""Test setup: a throwaway Postgres database rebuilt from db/*.sql.

Set TEST_DATABASE_URL to an EMPTY database you don't care about
(e.g. the local Postgres from docker compose). Never point it at Supabase prod:
the schema is dropped and recreated.
"""
import asyncio
import datetime as dt
import os
from pathlib import Path
from zoneinfo import ZoneInfo

import asyncpg
import pytest
import pytest_asyncio

DB_URL = os.environ.get("TEST_DATABASE_URL",
                        "postgresql://postgres:postgres@localhost:5433/voice_test")
SQL_DIR = Path(__file__).resolve().parent.parent / "db"
IST = ZoneInfo("Asia/Kolkata")

# Fixed "now" so tests don't depend on the real clock: Thu 26 Nov 2026, 08:00 IST.
NOW = dt.datetime(2026, 11, 26, 8, 0, tzinfo=IST)


async def _rebuild():
    conn = await asyncpg.connect(DB_URL)
    try:
        await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
        for f in sorted(SQL_DIR.glob("*.sql")):
            await conn.execute(f.read_text())
    finally:
        await conn.close()


@pytest.fixture(scope="session", autouse=True)
def database():
    asyncio.run(_rebuild())


@pytest_asyncio.fixture
async def pool():
    p = await asyncpg.create_pool(DB_URL, min_size=1, max_size=25, statement_cache_size=0)
    async with p.acquire() as c:
        await c.execute("TRUNCATE bookings")
    yield p
    await p.close()


@pytest_asyncio.fixture
async def salon(pool):
    return await pool.fetchval("SELECT id FROM businesses WHERE slug = 'sunrise-salon'")
