"""GET /digest: the outlook card for one tracked symbol (Phase 3d, D5).

The card is what the LLM wrote at the last round; `live` is recomputed on every request
(article set, good/bad counts, price move), so the page can show numbers and sources even
when no card exists yet or the AI is down (D-Q9b).
"""

import json
from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

from ..db import get_pool
from ..news.digest import WINDOW, gather

router = APIRouter()


class Bullet(BaseModel):
    side: str  # bull | bear
    text: str  # Thai, or English where the translation failed the checks
    text_en: str | None = None
    news_ids: list[int]


class Card(BaseModel):
    id: int
    created_at: datetime
    title: str
    summary: str | None
    title_en: str | None
    summary_en: str | None
    outlook: str  # positive | negative | mixed
    confidence: str | None  # low | medium | high
    bullets: list[Bullet]
    counts: dict[str, int] | None
    price_change_pct: float | None
    news_ids: list[int]
    model: str
    prompt_version: str


class Live(BaseModel):
    counts: dict[str, int]
    price_change_pct: float | None
    news_ids: list[int]


class Source(BaseModel):
    id: int
    headline: str
    headline_th: str | None
    source: str | None
    url: str | None
    created_at: datetime
    importance: int | None
    sentiment: float | None  # for this symbol


class DigestView(BaseModel):
    symbol: str
    window_hours: int
    card: Card | None  # None: never written, or older than the window
    card_is_current: bool  # the card was written from exactly the articles selected now
    live: Live
    sources: list[Source]  # every article the card cites, plus the live selection


SOURCES_SQL = """
    SELECT n.id, n.headline, n.source, n.url, n.created_at, a.headline_th, a.importance,
           s.sentiment
    FROM news n
    LEFT JOIN news_ai a ON a.news_id = n.id
    LEFT JOIN news_symbols s ON s.news_id = n.id AND s.symbol = $2
    WHERE n.id = ANY($1::bigint[])
    ORDER BY a.importance DESC NULLS LAST, n.created_at DESC
"""


def tracked_symbol(request: Request, symbol: str) -> str:
    symbol = symbol.strip().upper()
    if symbol not in request.app.state.symbols:
        raise HTTPException(status_code=422, detail=f"Untracked symbol: {symbol!r}")
    return symbol


def to_card(row) -> Card:
    return Card(
        id=row["id"], created_at=row["created_at"], title=row["title_th"],
        summary=row["summary_th"], title_en=row["title_en"], summary_en=row["summary_en"],
        outlook=row["tone"], confidence=row["confidence"],
        bullets=[Bullet(**b) for b in json.loads(row["bullets"])],
        counts=json.loads(row["counts"]) if row["counts"] else None,
        price_change_pct=row["price_change_pct"], news_ids=list(row["news_ids"]),
        model=row["model"], prompt_version=row["prompt_version"])


@router.get("/digest", response_model=DigestView)
async def get_digest(request: Request, symbol: str,
                     pool: asyncpg.Pool = Depends(get_pool)) -> DigestView:
    symbol = tracked_symbol(request, symbol)
    now = datetime.now(UTC)
    inputs = await gather(pool, symbol, now)
    row = await pool.fetchrow(
        """SELECT * FROM news_digest WHERE scope = 'symbol' AND symbol = $1
             AND created_at > $2 ORDER BY created_at DESC LIMIT 1""", symbol, now - WINDOW)
    card = to_card(row) if row else None
    ids = sorted(set(inputs.news_ids) | set(card.news_ids if card else []))
    sources = [Source(**dict(r)) for r in await pool.fetch(SOURCES_SQL, ids, symbol)]
    return DigestView(
        symbol=symbol, window_hours=int(WINDOW.total_seconds() // 3600), card=card,
        card_is_current=card is not None and sorted(card.news_ids) == inputs.news_ids,
        live=Live(counts=inputs.counts, price_change_pct=inputs.price_change_pct,
                  news_ids=inputs.news_ids),
        sources=sources)


class Accuracy(BaseModel):
    evaluated: int  # cards whose 3 trading days are over (D11)
    hits: int
    rate: float | None
    # "the previous 3 sessions' direction continues", judged on the same cards
    baseline_rate: float | None
    by_confidence: dict[str, dict[str, float | int | None]]


@router.get("/digest/accuracy", response_model=Accuracy)
async def get_accuracy(request: Request, symbol: str | None = None,
                       pool: asyncpg.Pool = Depends(get_pool)) -> Accuracy:
    if symbol is not None:
        symbol = tracked_symbol(request, symbol)
    rows = await pool.fetch(
        """SELECT coalesce(confidence, 'unknown') AS confidence, count(*) AS n,
                  count(*) FILTER (WHERE hit) AS hits,
                  count(baseline_hit) AS bn, count(*) FILTER (WHERE baseline_hit) AS bhits
           FROM news_digest
           WHERE scope = 'symbol' AND hit IS NOT NULL AND ($1::text IS NULL OR symbol = $1)
           GROUP BY 1""", symbol)
    total, hits = sum(r["n"] for r in rows), sum(r["hits"] for r in rows)
    bn, bhits = sum(r["bn"] for r in rows), sum(r["bhits"] for r in rows)
    return Accuracy(
        evaluated=total, hits=hits, rate=round(hits / total, 3) if total else None,
        baseline_rate=round(bhits / bn, 3) if bn else None,
        by_confidence={r["confidence"]: {"evaluated": r["n"], "hits": r["hits"],
                                         "rate": round(r["hits"] / r["n"], 3)} for r in rows})
