"""GET /news: articles for tracked symbols, their AI analysis and the price reaction (N5)."""

from datetime import UTC, datetime

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel

from ..db import get_pool
from ..news.analysis import RELEVANCE_THRESHOLD
from ..news.reaction import reactions

router = APIRouter()


class SymbolView(BaseModel):
    symbol: str
    relevance: float
    sentiment: float


class AIView(BaseModel):
    summary_th: str
    why_it_matters: str
    category: str
    importance: int
    is_market_wide: bool
    model: str
    prompt_version: str
    processed_at: datetime


class Reaction(BaseModel):
    m5: float | None  # % change 5 minutes after the article; None = no price yet
    m30: float | None


class NewsView(BaseModel):
    id: int
    headline: str
    summary: str
    author: str | None
    source: str | None
    url: str | None
    symbols: list[str]  # tracked symbols among the article's tags
    created_at: datetime
    updated_at: datetime
    backfilled: bool  # loaded at first start: the UI never alerts on these
    ai_status: str  # pending | done | failed | skipped (a re-queued article keeps its old `ai`)
    skip_reason: str | None  # why the rules kept it from the LLM (ai_status = skipped)
    ai: AIView | None
    per_symbol: list[SymbolView]
    reaction: dict[str, Reaction]  # per tracked symbol
    hidden: bool  # the symbol(s) are only mentioned in passing (relevance < 0.5)


# Hidden: the AI judged the article incidental to the symbol asked for (or, without
# `symbol`, to every tracked symbol). Articles without AI output are never hidden.
NEWS_SQL = f"""
    SELECT n.*, a.news_id IS NOT NULL AS has_ai,
           a.summary_th, a.why_it_matters, a.category, a.importance, a.is_market_wide,
           a.model, a.prompt_version, a.processed_at,
           a.news_id IS NOT NULL AND coalesce(r.relevance, 0) < {RELEVANCE_THRESHOLD} AS hidden
    FROM news n
    LEFT JOIN news_ai a ON a.news_id = n.id
    LEFT JOIN LATERAL (
        SELECT max(relevance) AS relevance FROM news_symbols s
        WHERE s.news_id = n.id AND s.symbol = ANY($2::text[])
    ) r ON true
    WHERE n.symbols && $2::text[]
      AND ($1::timestamptz IS NULL OR n.created_at >= $1)
      AND ($3::timestamptz IS NULL OR n.created_at < $3)
      AND ($4::int IS NULL OR a.importance >= $4)
      AND ($5 OR a.news_id IS NULL OR coalesce(r.relevance, 0) >= {RELEVANCE_THRESHOLD})
      AND ($7 OR n.ai_status <> 'skipped')
    ORDER BY n.created_at DESC, n.id DESC
    LIMIT $6
"""


@router.get("/news", response_model=list[NewsView])
async def get_news(
    request: Request,
    symbol: str | None = None,
    min_importance: int | None = Query(default=None, ge=1, le=5,
                                       description="Articles without AI output are excluded"),
    include_hidden: bool = False,
    include_skipped: bool = Query(default=False, description="Articles the rules kept from the LLM"),
    since: datetime | None = None,
    before: datetime | None = Query(default=None, description="Exclusive; for paging back"),
    limit: int = Query(default=50, ge=1, le=200),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[NewsView]:
    tracked: list[str] = request.app.state.symbols
    if symbol is not None:
        symbol = symbol.strip().upper()
        if symbol not in tracked:
            raise HTTPException(status_code=422, detail=f"Untracked symbol: {symbol!r}")
    wanted = [symbol] if symbol else tracked
    rows = await pool.fetch(NEWS_SQL, since, wanted, before, min_importance, include_hidden,
                            limit, include_skipped)
    if not rows:
        return []

    ids = [r["id"] for r in rows]
    per_symbol: dict[int, list[SymbolView]] = {}
    for s in await pool.fetch(
            "SELECT news_id, symbol, relevance, sentiment FROM news_symbols "
            "WHERE news_id = ANY($1::bigint[]) ORDER BY news_id, symbol", ids):
        per_symbol.setdefault(s["news_id"], []).append(
            SymbolView(symbol=s["symbol"], relevance=s["relevance"], sentiment=s["sentiment"]))

    shown = {r["id"]: [s for s in r["symbols"] if s in wanted] for r in rows}
    moves = await reactions(pool, [(r["id"], s, r["created_at"]) for r in rows
                                   for s in shown[r["id"]]], datetime.now(UTC))
    empty = {"m5": None, "m30": None}
    return [
        NewsView(
            id=r["id"], headline=r["headline"], summary=r["summary"], author=r["author"],
            source=r["source"], url=r["url"],
            symbols=[s for s in r["symbols"] if s in tracked],
            created_at=r["created_at"], updated_at=r["updated_at"], backfilled=r["backfilled"],
            ai_status=r["ai_status"], skip_reason=r["skip_reason"],
            ai=AIView(**{k: r[k] for k in AIView.model_fields}) if r["has_ai"] else None,
            per_symbol=per_symbol.get(r["id"], []),
            reaction={s: Reaction(**moves.get((r["id"], s), empty)) for s in shown[r["id"]]},
            hidden=r["hidden"],
        )
        for r in rows
    ]
