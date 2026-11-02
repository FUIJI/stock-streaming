import httpx

from src.db import get_pool
from src.main import app
from src.migrate import migrate

TODAY_ET = ("(date_trunc('day', now() AT TIME ZONE 'America/New_York') "
            "AT TIME ZONE 'America/New_York')")


async def get_daily(pg_pool, **params):
    app.dependency_overrides[get_pool] = lambda: pg_pool
    try:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        resp = await client.get("/candles", params={"symbol": "AAPL", "interval": "1d", **params})
        assert resp.status_code == 200, resp.text
        return resp.json()
    finally:
        app.dependency_overrides.clear()


async def test_daily_candles_end_with_today_rolled_up_from_minutes(pg_pool):
    await migrate(pg_pool)
    # Two past trading days from Alpaca, plus a stale partial "today" row that must be ignored.
    for days_ago, close in ((2, 100.0), (1, 101.0), (0, 999.0)):
        await pg_pool.execute(
            f"INSERT INTO ohlcv_1d VALUES ('AAPL', {TODAY_ET} - make_interval(days => $1),"
            " 1, 200, 0.5, $2, 1000, 10, now())", days_ago, close)
    # Today's live minutes (any time after midnight New York).
    for minute, (o, h, low, c) in enumerate([(102, 103, 101.5, 102.5), (102.5, 105, 102, 104)]):
        await pg_pool.execute(
            f"INSERT INTO ohlcv_1m VALUES ('AAPL', {TODAY_ET} + make_interval(mins => $1),"
            " $2, $3, $4, $5, 50, 5, now())", 60 + minute, o, h, low, c)

    days = await get_daily(pg_pool)
    assert [d["close"] for d in days] == [100.0, 101.0, 104.0]
    today = days[-1]
    assert (today["open"], today["high"], today["low"], today["volume"]) == (102, 105, 101.5, 100)
    assert len(await get_daily(pg_pool, limit=2)) == 2  # newest two: yesterday + today


async def test_no_row_for_today_before_the_first_trade(pg_pool):
    await migrate(pg_pool)
    await pg_pool.execute(
        f"INSERT INTO ohlcv_1d VALUES ('AAPL', {TODAY_ET} - interval '1 day',"
        " 1, 2, 0.5, 1.5, 10, 1, now())")
    assert [d["close"] for d in await get_daily(pg_pool)] == [1.5]
