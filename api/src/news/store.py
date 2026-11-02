"""Raw news from Alpaca into the `news` table."""

import html
import re
from dataclasses import dataclass
from datetime import datetime

import asyncpg

_TAGS = re.compile(r"<[^>]+>")


def clean_text(value: str | None) -> str:
    """Alpaca summaries carry HTML entities (S&amp;P, &#39;) and the odd tag."""
    return " ".join(html.unescape(_TAGS.sub(" ", value or "")).split())


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


@dataclass(frozen=True)
class Article:
    id: int
    headline: str
    summary: str
    author: str | None
    source: str | None
    url: str | None
    symbols: list[str]
    created_at: datetime
    updated_at: datetime

    @classmethod
    def from_alpaca(cls, raw: dict) -> "Article":
        return cls(
            id=int(raw["id"]),
            headline=clean_text(raw.get("headline")),
            summary=clean_text(raw.get("summary")),
            author=raw.get("author") or None,
            source=raw.get("source") or None,
            url=raw.get("url") or None,
            symbols=[s.upper() for s in raw.get("symbols") or []],
            created_at=_ts(raw["created_at"]),
            updated_at=_ts(raw.get("updated_at") or raw["created_at"]),
        )


# New articles are inserted pending. An existing article is only touched when
# Alpaca edited it (updated_at moved), and then goes back to the LLM queue.
UPSERT_NEWS_SQL = """
    INSERT INTO news (id, headline, summary, author, source, url, symbols,
                      created_at, updated_at, backfilled)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
    ON CONFLICT (id) DO UPDATE SET
        headline = EXCLUDED.headline,
        summary = EXCLUDED.summary,
        author = EXCLUDED.author,
        url = EXCLUDED.url,
        symbols = EXCLUDED.symbols,
        updated_at = EXCLUDED.updated_at,
        fetched_at = now(),
        ai_status = 'pending',
        ai_attempts = 0,
        ai_next_attempt_at = now(),
        ai_error = NULL
    WHERE news.updated_at IS DISTINCT FROM EXCLUDED.updated_at
"""


async def save_articles(pool: asyncpg.Pool, articles: list[Article], backfilled: bool) -> None:
    await pool.executemany(UPSERT_NEWS_SQL, [
        (a.id, a.headline, a.summary, a.author, a.source, a.url, a.symbols,
         a.created_at, a.updated_at, backfilled)
        for a in articles
    ])


async def latest_created_at(pool: asyncpg.Pool) -> datetime | None:
    return await pool.fetchval("SELECT max(created_at) FROM news")
