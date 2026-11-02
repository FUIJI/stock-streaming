import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from src.alpaca import AlpacaClient
from src.migrate import migrate
from src.news.poller import NewsStats, poll_once, run_poller
from src.news.store import Article, clean_text

# Shaped like the real /v1beta1/news response seen on the VM (content is empty on this plan).
RAW = {
    "author": "Rishabh Mishra",
    "content": "",
    "created_at": "2026-11-02T05:47:19Z",
    "headline": "Stock Market: Will S&P 500 Open Up or Down Today?",
    "id": 62767926,
    "images": [{"size": "thumb", "url": "https://cdn.benzinga.com/x.jpg"}],
    "source": "benzinga",
    "summary": "Will the S&amp;P 500 open higher? Wall Street weighs Apple earnings and "
               "Yardeni&#39;s <b>market</b> outlook.",
    "symbols": ["AAPL", "DIA", "PLTR", "POLUSD", "QQQ", "SPY"],
    "updated_at": "2026-11-02T05:47:19Z",
    "url": "https://www.benzinga.com/markets/62767926",
}
NOW = datetime(2026, 11, 2, 6, 0, tzinfo=UTC)


def test_clean_text_unescapes_entities_and_drops_tags():
    assert clean_text(RAW["summary"]) == (
        "Will the S&P 500 open higher? Wall Street weighs Apple earnings and "
        "Yardeni's market outlook.")
    assert clean_text(None) == ""


def test_article_from_alpaca():
    a = Article.from_alpaca(RAW)
    assert a.id == 62767926 and a.headline == "Stock Market: Will S&P 500 Open Up or Down Today?"
    assert a.symbols[0] == "AAPL" and a.created_at == datetime(2026, 11, 2, 5, 47, 19, tzinfo=UTC)


class FakeNews:
    """Serves pages of articles and records the requests."""

    def __init__(self, pages):
        self.pages = list(pages)
        self.requests = []

    def handler(self, request):
        self.requests.append(dict(request.url.params))
        page = self.pages.pop(0) if self.pages else {"news": [], "next_page_token": None}
        return httpx.Response(200, json=page)

    def client(self):
        return AlpacaClient("k", "s", "https://paper", transport=httpx.MockTransport(self.handler))


async def rows(pg_pool):
    return [dict(r) for r in await pg_pool.fetch(
        "SELECT id, summary, backfilled, ai_status, updated_at FROM news ORDER BY id")]


async def test_first_poll_backfills_and_follows_pages(pg_pool):
    await migrate(pg_pool)
    second = {**RAW, "id": 2, "created_at": "2026-11-02T05:50:00Z",
              "updated_at": "2026-11-02T05:50:00Z"}
    fake = FakeNews([{"news": [RAW], "next_page_token": "p2"},
                     {"news": [second], "next_page_token": None}])
    assert await poll_once(pg_pool, fake.client(), ["AAPL"], backfill_days=3, now=NOW) == 2
    assert fake.requests[0]["start"] == "2026-10-30T06:00:00+00:00"  # 3 days back
    assert fake.requests[0]["sort"] == "asc" and fake.requests[1]["page_token"] == "p2"
    stored = await rows(pg_pool)
    assert [r["backfilled"] for r in stored] == [True, True]
    assert stored[1]["summary"].startswith("Will the S&P 500")


async def test_later_polls_overlap_without_duplicates_and_requeue_edits(pg_pool):
    await migrate(pg_pool)
    await poll_once(pg_pool, FakeNews([{"news": [RAW]}]).client(), ["AAPL"], 3, now=NOW)
    await pg_pool.execute("UPDATE news SET ai_status = 'done'")

    edited = {**RAW, "summary": "Updated", "updated_at": "2026-11-02T06:10:00Z"}
    fresh = {**RAW, "id": 3, "created_at": "2026-11-02T06:20:00Z",
             "updated_at": "2026-11-02T06:20:00Z"}
    fake = FakeNews([{"news": [RAW, fresh]}])  # RAW unchanged: must stay done
    await poll_once(pg_pool, fake.client(), ["AAPL"], 3, now=NOW)
    assert fake.requests[0]["start"] == "2026-11-02T05:42:19+00:00"  # newest - 5 min overlap
    assert [(r["id"], r["ai_status"], r["backfilled"]) for r in await rows(pg_pool)] == [
        (3, "pending", False), (62767926, "done", True)]

    await poll_once(pg_pool, FakeNews([{"news": [edited]}]).client(), ["AAPL"], 3, now=NOW)
    edited_row = (await rows(pg_pool))[1]
    assert (edited_row["summary"], edited_row["ai_status"]) == ("Updated", "pending")
    assert edited_row["backfilled"] is True  # an edit does not make old news alert-worthy


async def test_poller_survives_errors_and_reports_them():
    def boom(request):
        return httpx.Response(500, text="upstream down")

    class Pool:
        async def fetchval(self, query):
            return None

    stats = NewsStats()
    alpaca = AlpacaClient("k", "s", "https://paper", transport=httpx.MockTransport(boom))
    task = asyncio.create_task(run_poller(Pool(), alpaca, ["AAPL"], stats, 0.01, 3))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert "500" in stats.last_error and stats.last_poll_at is None
