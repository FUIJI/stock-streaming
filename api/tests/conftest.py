import os
import uuid

import asyncpg
import pytest

# Integration tests need a throwaway Postgres server, e.g.
#   docker run -d -e POSTGRES_PASSWORD=test -p 127.0.0.1:55432:5432 postgres:16.4-alpine
#   TEST_PG_DSN=postgresql://postgres:test@127.0.0.1:55432/postgres uv run pytest
TEST_PG_DSN = os.environ.get("TEST_PG_DSN")


@pytest.fixture
async def pg_pool():
    """A pool on a brand-new empty database, dropped afterwards."""
    if not TEST_PG_DSN:
        pytest.skip("TEST_PG_DSN not set")
    name = f"test_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(TEST_PG_DSN)
    await admin.execute(f'CREATE DATABASE "{name}"')
    pool = await asyncpg.create_pool(TEST_PG_DSN.rsplit("/", 1)[0] + f"/{name}", min_size=1, max_size=4)
    try:
        yield pool
    finally:
        await pool.close()
        await admin.execute(f'DROP DATABASE "{name}"')
        await admin.close()
