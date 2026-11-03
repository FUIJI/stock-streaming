from datetime import UTC, datetime

import pytest

from src.migrate import migrate
from src.news.reprocess import rerun_rules, reset
from src.news.rules import skip_reason
from src.news.store import Article, save_articles

SINCE = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.mark.parametrize("headline", [
    "Apple, Palantir, Marvell Technology, Oracle and Aehr Test Systems: "
    "Why These 5 Stocks Are on Investors' Radars Today",
    "Apple Unusual Options Activity For November 03",
    "Check Out What Whales Are Doing With TSLA",
    "Smart Money Is Betting Big In NVDA Options",
    "A Closer Look at Microsoft's Options Market Dynamics",
    "3 Stocks To Buy Before Earnings",
    "Top Gainers And Losers In Pre-Market Trading",
    "Price Over Earnings Overview: Apple",
    "P/E Ratio Insights for Tesla",
    "Here's How Much You Would Have Made Owning NVIDIA Stock In The Last 10 Years",
    "If You Invested $1000 In Apple Stock 5 Years Ago, You Would Have This Much Today",
])
def test_noise_headlines_are_skipped(headline):
    assert skip_reason(headline, ["AAPL"]) is not None


@pytest.mark.parametrize("headline", [
    "Apple Q4 Earnings Beat: iPhone Revenue Record, Services Up 15%",
    "Morgan Stanley Downgrades Tesla To Underweight, Cuts Price Target",
    "Jim Cramer Calls Out 'Pretty Thick' Negativity on Apple, Palantir and Marvell",
    "iPhone Owners Can Get Up to $95 From Apple Over Siri Promises: Here's How to Claim",
    "Nvidia Stock Falls 5% After China Export Curbs",
    "Microsoft To Invest $10 Billion In Japan Data Centers",
])
def test_real_news_is_kept(headline):
    assert skip_reason(headline, ["AAPL", "MSFT"]) is None


def test_too_many_tags_is_a_roundup():
    six = ["AAPL", "DIA", "PLTR", "POLUSD", "QQQ", "SPY"]
    assert "6 symbols" in skip_reason("Will S&P 500 Open Up or Down Today?", six)
    assert skip_reason("Will S&P 500 Open Up or Down Today?", six[:5]) is None
    assert skip_reason("x", six, tag_limit=10) is None


def article(news_id, headline, symbols=("AAPL",)):
    t = "2026-11-02T15:00:00Z"
    return Article.from_alpaca({"id": news_id, "headline": headline, "symbols": list(symbols),
                                "created_at": t, "updated_at": t})


async def statuses(pool):
    return {r["id"]: (r["ai_status"], r["skip_reason"] is not None)
            for r in await pool.fetch("SELECT id, ai_status, skip_reason FROM news")}


async def test_skipped_articles_are_stored_but_not_queued(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, "Apple Unusual Options Activity"),
                                  article(2, "Apple beats earnings")], False)
    assert await statuses(pg_pool) == {1: ("skipped", True), 2: ("pending", False)}
    # "reprocess --status all" must not push skipped articles to the LLM
    assert await reset(pg_pool, SINCE, "all", None) == 1
    assert (await statuses(pg_pool))[1] == ("skipped", True)


async def test_rerun_rules_moves_articles_both_ways(pg_pool, monkeypatch):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, "Apple beats earnings", ["AAPL", "MSFT", "SPY"]),
                                  article(2, "Apple ships", ["AAPL", "MSFT", "SPY"]),
                                  article(3, "Apple Unusual Options Activity")], False)
    await pg_pool.execute("UPDATE news SET ai_status = 'done' WHERE id = 2")  # analysed: kept

    monkeypatch.setenv("NEWS_MAX_TAGS", "2")
    assert await rerun_rules(pg_pool, SINCE) == (1, 0)
    assert await statuses(pg_pool) == {1: ("skipped", True), 2: ("done", False),
                                       3: ("skipped", True)}
    monkeypatch.setenv("NEWS_MAX_TAGS", "5")
    assert await rerun_rules(pg_pool, SINCE) == (0, 1)
    assert (await statuses(pg_pool))[1] == ("pending", False)
