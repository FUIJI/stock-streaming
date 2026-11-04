"""D5: GET /digest and /digest/accuracy against a real Postgres."""

import httpx
import pytest

from src.db import get_pool
from src.main import app
from src.migrate import migrate
from src.news.digest import Outlook, gather, parse, save
from tests.test_news_outlook import NOW, article, card


@pytest.fixture
async def client(pg_pool):
    await migrate(pg_pool)
    app.dependency_overrides[get_pool] = lambda: pg_pool
    app.state.symbols = ["NVDA", "AAPL"]
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                 base_url="http://test") as c:
        yield c, pg_pool
    app.dependency_overrides.clear()


async def written(pool, ids, th=None):
    inputs = await gather(pool, "NVDA", NOW)
    draft = parse(card(*ids), inputs)
    return await save(pool, inputs, Outlook(draft=draft, th=th or {}), "gemma")


async def test_no_news_no_card(client):
    c, _ = client
    body = (await c.get("/digest", params={"symbol": "nvda"})).json()
    assert body["card"] is None and body["sources"] == [] and not body["card_is_current"]
    assert body["live"]["counts"] == {"positive": 0, "negative": 0, "neutral": 0}
    assert (await c.get("/digest", params={"symbol": "TSLA"})).status_code == 422


async def test_numbers_and_sources_without_a_card(client):
    c, pool = client
    await article(pool, 1, hours_ago=3, sentiment=0.7)
    await article(pool, 2, hours_ago=2, sentiment=-0.5, importance=5)
    body = (await c.get("/digest", params={"symbol": "NVDA"})).json()
    assert body["card"] is None  # the AI hasn't written one: the page still has something
    assert body["live"]["counts"] == {"positive": 1, "negative": 1, "neutral": 0}
    assert [s["id"] for s in body["sources"]] == [2, 1]  # most important first
    assert body["sources"][0]["headline_th"] == "หัวข่าว" and body["sources"][0]["sentiment"] == -0.5


async def test_the_card_and_every_source_it_cites(client):
    c, pool = client
    await article(pool, 1, hours_ago=3)
    await written(pool, [1], th={"title": "ข่าวดีหนุนราคา"})
    body = (await c.get("/digest", params={"symbol": "NVDA"})).json()
    got = body["card"]
    assert got["title"] == "ข่าวดีหนุนราคา" and got["title_en"] == "Good news lifts the stock"
    assert got["summary"] == "Buying pressure grew."  # not translated: English stays
    assert got["bullets"][0] == {"side": "bull", "text": "Broker raised its target",
                                 "text_en": "Broker raised its target", "news_ids": [1]}
    assert got["outlook"] == "positive" and body["card_is_current"]
    assert [s["id"] for s in body["sources"]] == [1]

    await article(pool, 2, hours_ago=1)  # new article since the card
    body = (await c.get("/digest", params={"symbol": "NVDA"})).json()
    assert not body["card_is_current"] and {s["id"] for s in body["sources"]} == {1, 2}


async def test_cards_older_than_the_window_are_not_shown(client):
    c, pool = client
    await article(pool, 1, hours_ago=3)
    card_id = await written(pool, [1])
    await pool.execute("UPDATE news_digest SET created_at = now() - interval '73 hours'"
                       " WHERE id = $1", card_id)
    assert (await c.get("/digest", params={"symbol": "NVDA"})).json()["card"] is None


async def test_accuracy(client):
    c, pool = client
    assert (await c.get("/digest/accuracy")).json() == {
        "evaluated": 0, "hits": 0, "rate": None, "baseline_rate": None, "by_confidence": {}}
    await article(pool, 1, hours_ago=3)
    for hit in (True, True, False):
        card_id = await written(pool, [1])
        await pool.execute("UPDATE news_digest SET hit = $2 WHERE id = $1", card_id, hit)
    body = (await c.get("/digest/accuracy", params={"symbol": "NVDA"})).json()
    assert (body["evaluated"], body["hits"], body["rate"]) == (3, 2, 0.667)
    assert body["by_confidence"]["medium"]["evaluated"] == 3
