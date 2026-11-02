"""Apply api/src/migrations/*.sql in filename order, once each.

storage/timescale/init/001_schema.sql only runs when a database volume is
first created, so tables added later reach existing databases (the VM,
Supabase later) through here instead. Every file must be idempotent-safe on
a database that the init scripts already set up (use IF NOT EXISTS).
"""

import logging
from pathlib import Path

import asyncpg

log = logging.getLogger("api.migrate")

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
# Arbitrary constant: serializes the API and one-shot jobs starting at the same time.
LOCK_KEY = 7_315_002


async def migrate(pool: asyncpg.Pool) -> list[str]:
    """Apply pending migrations; returns the versions applied by this call."""
    applied_now: list[str] = []
    async with pool.acquire() as conn:
        await conn.execute("SELECT pg_advisory_lock($1)", LOCK_KEY)
        try:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version    TEXT PRIMARY KEY,
                    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
                )
                """
            )
            done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
            for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
                if path.stem in done:
                    continue
                async with conn.transaction():
                    await conn.execute(path.read_text())
                    await conn.execute(
                        "INSERT INTO schema_migrations (version) VALUES ($1)", path.stem
                    )
                log.info("Applied migration %s", path.stem)
                applied_now.append(path.stem)
        finally:
            await conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
    return applied_now
