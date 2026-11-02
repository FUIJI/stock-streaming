from datetime import datetime, timedelta, timezone

import httpx
import pytest

from src.db import get_pool
from src.main import app


class FakePool:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    async def fetch(self, query, *args):
        self.calls.append((query, args))
        return self.rows


CANDLE_ROW = {
    "symbol": "AAPL",
    "bucket_start": datetime(2026, 7, 17, 10, 0, tzinfo=timezone.utc),
    "open": 100.0,
    "high": 105.0,
    "low": 99.0,
    "close": 104.0,
    "volume": 1234.0,
    "trade_count": 42,
}


@pytest.fixture
def client_with_pool():
    pool = FakePool([CANDLE_ROW])
    app.dependency_overrides[get_pool] = lambda: pool
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url="http://test")
    yield client, pool
    app.dependency_overrides.clear()


async def test_get_candles(client_with_pool):
    client, pool = client_with_pool
    resp = await client.get("/candles", params={"symbol": "aapl", "interval": "1m"})
    assert resp.status_code == 200
    [candle] = resp.json()
    assert candle["symbol"] == "AAPL"
    assert candle["close"] == 104.0
    # Symbol must be uppercased before hitting the DB.
    assert pool.calls[0][1][0] == "AAPL"


async def test_get_candles_rejects_unknown_interval(client_with_pool):
    client, _ = client_with_pool
    resp = await client.get("/candles", params={"symbol": "AAPL", "interval": "3d"})
    assert resp.status_code == 422


async def test_get_candles_uses_cagg_table(client_with_pool):
    client, pool = client_with_pool
    resp = await client.get("/candles", params={"symbol": "AAPL", "interval": "5m"})
    assert resp.status_code == 200
    assert "ohlcv_5m" in pool.calls[0][0]


async def test_sql_rollup_queries_ohlcv_1m_with_date_bin(client_with_pool, monkeypatch):
    monkeypatch.setenv("CANDLE_ROLLUP", "sql")
    client, pool = client_with_pool
    resp = await client.get("/candles", params={"symbol": "AAPL", "interval": "1h"})
    assert resp.status_code == 200
    query, args = pool.calls[0]
    assert "date_bin" in query and "FROM ohlcv_1m" in query
    assert args[-1] == timedelta(hours=1)


async def test_sql_rollup_keeps_1m_on_base_table(client_with_pool, monkeypatch):
    monkeypatch.setenv("CANDLE_ROLLUP", "sql")
    client, pool = client_with_pool
    await client.get("/candles", params={"symbol": "AAPL", "interval": "1m"})
    assert "date_bin" not in pool.calls[0][0]
