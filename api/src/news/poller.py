"""Background task: pull new articles for tracked symbols from Alpaca every NEWS_POLL_SEC."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from ..alpaca import AlpacaClient
from .store import Article, latest_created_at, save_articles

log = logging.getLogger("api.news")

# Re-read a little before the newest stored article: Alpaca can publish slightly
# out of order, and the upsert makes re-reads free.
OVERLAP = timedelta(minutes=5)
MAX_PAGES = 20  # 50 articles each; only a first-start backfill comes anywhere near


@dataclass
class NewsStats:
    last_poll_at: float | None = None  # wall clock of the last successful poll
    last_error: str | None = None
    stored_last_poll: int = 0


async def poll_once(pool: asyncpg.Pool, alpaca: AlpacaClient, symbols: list[str],
                    backfill_days: float, now: datetime | None = None) -> int:
    """Fetch and store everything since the newest stored article; returns articles seen."""
    now = now or datetime.now(UTC)
    newest = await latest_created_at(pool)
    backfilling = newest is None
    start = now - timedelta(days=backfill_days) if backfilling else newest - OVERLAP

    seen = 0
    token = None
    for _ in range(MAX_PAGES):
        page = await alpaca.news(symbols, start, token)
        articles = [Article.from_alpaca(raw) for raw in page.get("news") or []]
        if articles:
            await save_articles(pool, articles, backfilled=backfilling)
            seen += len(articles)
        token = page.get("next_page_token")
        if not token:
            break
    else:
        log.warning("News: stopped after %d pages; the rest follows next poll", MAX_PAGES)
    return seen


async def run_poller(pool: asyncpg.Pool, alpaca: AlpacaClient, symbols: list[str],
                     stats: NewsStats, interval: float, backfill_days: float) -> None:
    """Never raises: a failed poll is logged and retried on the next tick."""
    while True:
        try:
            stats.stored_last_poll = await poll_once(pool, alpaca, symbols, backfill_days)
            stats.last_poll_at = time.time()
            stats.last_error = None
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stats.last_error = str(exc)[:300]
            log.warning("News poll failed: %s", exc)
        await asyncio.sleep(interval)
