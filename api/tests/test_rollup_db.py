import httpx

from src.db import get_pool
from src.main import app
from src.migrate import migrate


async def hourly(pg_pool, limit):
    app.dependency_overrides[get_pool] = lambda: pg_pool
    try:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        resp = await client.get("/candles", params={"symbol": "AAPL", "interval": "1h",
                                                    "limit": limit})
        assert resp.status_code == 200, resp.text
        return resp.json()
    finally:
        app.dependency_overrides.clear()


async def test_rollup_returns_complete_newest_candles(pg_pool, monkeypatch):
    monkeypatch.setenv("CANDLE_ROLLUP", "sql")
    await migrate(pg_pool)
    # 10:30-12:59: the 10:00 hour only has its second half.
    await pg_pool.execute(
        """INSERT INTO ohlcv_1m
           SELECT 'AAPL', ts, 1, 2, 0.5, 1.5, 10, 1, now()
           FROM generate_series(TIMESTAMPTZ '2026-11-02 10:30Z',
                                TIMESTAMPTZ '2026-11-02 12:59Z', interval '1 minute') ts""")
    two = await hourly(pg_pool, 2)
    assert [(c["bucket_start"], c["volume"]) for c in two] == [
        ("2026-11-02T11:00:00Z", 600.0), ("2026-11-02T12:00:00Z", 600.0),
    ]
    # Asking for more than exists returns everything, including the real first (partial) hour.
    assert [c["volume"] for c in await hourly(pg_pool, 5)] == [300.0, 600.0, 600.0]
