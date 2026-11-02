from src.migrate import MIGRATIONS_DIR, migrate


async def test_applies_every_migration_once(pg_pool):
    versions = sorted(p.stem for p in MIGRATIONS_DIR.glob("*.sql"))
    assert await migrate(pg_pool) == versions
    assert await migrate(pg_pool) == []  # second run is a no-op
    rows = await pg_pool.fetch("SELECT version FROM schema_migrations ORDER BY version")
    assert [r["version"] for r in rows] == versions


async def test_existing_init_schema_is_left_alone(pg_pool):
    # Databases created by storage/timescale/init already have ohlcv_1m with data.
    await pg_pool.execute(
        """CREATE TABLE ohlcv_1m (symbol TEXT, bucket_start TIMESTAMPTZ, open FLOAT8, high FLOAT8,
           low FLOAT8, close FLOAT8, volume FLOAT8, trade_count BIGINT, updated_at TIMESTAMPTZ,
           PRIMARY KEY (symbol, bucket_start))"""
    )
    await pg_pool.execute("INSERT INTO ohlcv_1m VALUES ('AAPL', now(), 1, 1, 1, 1, 1, 1, now())")
    await migrate(pg_pool)
    assert await pg_pool.fetchval("SELECT count(*) FROM ohlcv_1m") == 1
