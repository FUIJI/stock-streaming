"""N4 (price reaction) and N5 (GET /news) against a real Postgres."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.db import get_pool
from src.main import app
from src.migrate import migrate
from src.news.reaction import pct_change, reactions
from src.news.store import Article, save_articles

T = datetime(2026, 11, 2, 15, 0, 30, tzinfo=UTC)  # 10:00:30 New York, market open


def article(news_id, at, symbols=("AAPL",)):
    return Article.from_alpaca({"id": news_id, "headline": f"News {news_id}", "summary": "s",
                                "symbols": list(symbols), "created_at": at.isoformat()})


async def candles(pool, symbol, closes: dict[datetime, float]):
    await pool.executemany(
        "INSERT INTO ohlcv_1m (symbol, bucket_start, open, high, low, close) "
        "VALUES ($1, $2, $3, $3, $3, $3)", [(symbol, t, c) for t, c in closes.items()])


async def analysed(pool, news_id, importance, relevance: dict[str, float], sentiment=0.5):
    await pool.execute(
        "INSERT INTO news_ai (news_id, summary_th, why_it_matters, category, importance,"
        " importance_raw, is_market_wide, model, prompt_version)"
        " VALUES ($1, 'สรุป', 'เพราะ', 'product', $2, $2, false, 'gemma', 'v3')",
        news_id, importance)
    await pool.executemany(
        "INSERT INTO news_symbols VALUES ($1, $2, $3, $4)",
        [(news_id, s, r, sentiment) for s, r in relevance.items()])
    await pool.execute("UPDATE news SET ai_status = 'done' WHERE id = $1", news_id)


def minute(m):
    return datetime(2026, 11, 2, 15, 0, tzinfo=UTC) + timedelta(minutes=m)


def test_pct_change():
    assert pct_change(100, 101.234) == 1.23
    assert pct_change(None, 1) is None and pct_change(1, None) is None
    assert pct_change(0, 1) is None


# --- N4 -------------------------------------------------------------------------

async def test_reaction_during_market_hours(pg_pool):
    await migrate(pg_pool)
    # 09:59 is the last candle that ended before the article (10:00:30).
    await candles(pg_pool, "AAPL", {minute(-2): 90, minute(-1): 100, minute(0): 100.5,
                                    minute(4): 102, minute(5): 150, minute(29): 99})
    got = await reactions(pg_pool, [(1, "AAPL", T)], now=T + timedelta(hours=1))
    # m5 = last candle in [10:00, 10:05) -> 10:04; the 10:05 candle is outside the window
    assert got[(1, "AAPL")] == {"m5": 2.0, "m30": -1.0}


async def test_reaction_waits_for_the_window_to_pass(pg_pool):
    await migrate(pg_pool)
    await candles(pg_pool, "AAPL", {minute(-1): 100, minute(4): 102, minute(10): 103})
    got = await reactions(pg_pool, [(1, "AAPL", T)], now=T + timedelta(minutes=12))
    assert got[(1, "AAPL")] == {"m5": 2.0, "m30": None}  # 30 minutes not over yet


async def test_reaction_is_none_while_the_market_is_closed(pg_pool):
    await migrate(pg_pool)
    night = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)  # 22:00 New York, no trading
    await candles(pg_pool, "AAPL", {datetime(2026, 11, 3, 0, 59, tzinfo=UTC): 100})
    got = await reactions(pg_pool, [(1, "AAPL", night)], now=night + timedelta(hours=2))
    assert got[(1, "AAPL")] == {"m5": None, "m30": None}


async def test_reaction_is_per_symbol(pg_pool):
    await migrate(pg_pool)
    await candles(pg_pool, "AAPL", {minute(-1): 100, minute(3): 110})
    await candles(pg_pool, "MSFT", {minute(-1): 200, minute(3): 190})
    got = await reactions(pg_pool, [(1, "AAPL", T), (1, "MSFT", T)], now=T + timedelta(hours=1))
    assert got[(1, "AAPL")]["m5"] == 10.0 and got[(1, "MSFT")]["m5"] == -5.0
    assert await reactions(pg_pool, [], now=T) == {}


# --- N5 -------------------------------------------------------------------------

@pytest.fixture
async def client(pg_pool):
    await migrate(pg_pool)
    app.dependency_overrides[get_pool] = lambda: pg_pool
    app.state.symbols = ["AAPL", "MSFT"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c, pg_pool
    app.dependency_overrides.clear()


async def ids(client, **params):
    resp = await client.get("/news", params=params)
    assert resp.status_code == 200, resp.text
    return [n["id"] for n in resp.json()]


async def test_news_shape_and_pending_articles(client):
    c, pool = client
    await save_articles(pool, [article(1, T, ("AAPL", "SPY"))], backfilled=True)
    await save_articles(pool, [article(2, T + timedelta(minutes=1), ("MSFT", "AAPL"))], False)
    await analysed(pool, 2, 4, {"MSFT": 0.9, "AAPL": 0.6})
    await candles(pool, "AAPL", {minute(-1): 100, minute(2): 101})

    [second, first] = (await c.get("/news")).json()  # newest first
    assert first["ai"] is None and first["ai_status"] == "pending"  # not analysed: still shown
    assert first["symbols"] == ["AAPL"] and first["backfilled"] is True  # SPY not tracked
    assert first["reaction"] == {"AAPL": {"m5": 1.0, "m30": 1.0}}
    assert second["ai"]["importance"] == 4 and second["ai"]["model"] == "gemma"
    assert [p["symbol"] for p in second["per_symbol"]] == ["AAPL", "MSFT"]
    assert set(second["reaction"]) == {"MSFT", "AAPL"}
    assert second["hidden"] is False


async def test_filters(client):
    c, pool = client
    await save_articles(pool, [article(i, T + timedelta(minutes=i), s) for i, s in
                               [(1, ("AAPL",)), (2, ("MSFT",)), (3, ("AAPL",)), (4, ("TSLA",))]],
                        False)
    await analysed(pool, 1, 5, {"AAPL": 0.9})
    await analysed(pool, 3, 2, {"AAPL": 0.8})

    assert await ids(c) == [3, 2, 1]  # TSLA isn't tracked here
    assert await ids(c, symbol="aapl") == [3, 1]
    assert await ids(c, min_importance=4) == [1]  # unanalysed articles have no importance
    assert await ids(c, since=(T + timedelta(minutes=2)).isoformat()) == [3, 2]
    assert await ids(c, before=(T + timedelta(minutes=3)).isoformat(), limit=1) == [2]
    assert (await c.get("/news", params={"symbol": "TSLA"})).status_code == 422
    assert (await c.get("/news", params={"limit": 0})).status_code == 422


async def test_hidden_depends_on_the_symbol_asked_for(client):
    c, pool = client
    await save_articles(pool, [article(1, T, ("AAPL", "MSFT"))], False)
    await analysed(pool, 1, 4, {"AAPL": 0.2, "MSFT": 0.9})  # about MSFT, mentions AAPL

    assert await ids(c) == [1]
    assert await ids(c, symbol="MSFT") == [1]
    assert await ids(c, symbol="AAPL") == []
    [shown] = (await c.get("/news", params={"symbol": "AAPL", "include_hidden": "true"})).json()
    assert shown["hidden"] is True
    assert list(shown["reaction"]) == ["AAPL"]
