import os
from datetime import UTC, datetime

from fastapi import APIRouter, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from ..feed import FeedStats

router = APIRouter()


def _feed_summary(stats: FeedStats) -> dict:
    last = stats.last_trade_at
    return {
        "connections": stats.connections,
        "trades_total": stats.trades_total,
        "last_trade_at": datetime.fromtimestamp(last, tz=UTC) if last else None,
        "seconds_since_trade": round(stats.seconds_since_trade(), 1),
    }


@router.get("/health")
async def health(request: Request) -> dict:
    """Liveness: always 200 while the process serves requests.

    version = deployed image tag; the deploy workflow waits for it to change.
    """
    news = getattr(request.app.state, "news", None)
    llm = getattr(request.app.state, "llm_stats", None)
    llm_provider = getattr(request.app.state, "llm", None)
    return {
        "status": "ok",
        "version": os.environ.get("APP_VERSION", "dev"),
        "feed": _feed_summary(request.app.state.feed),
        "news": None if news is None else {
            "last_poll_at": (datetime.fromtimestamp(news.last_poll_at, tz=UTC)
                             if news.last_poll_at else None),
            "last_error": news.last_error,
        },
        "llm": None if llm is None or llm_provider is None else {
            "models": llm.models,
            "calls_today": llm_provider.quota.calls_today,
            "daily_limit": llm_provider.quota.daily_limit,
            "pending": llm.pending,
            "done": llm.done,
            "failed": llm.failed,
            "last_success_at": (datetime.fromtimestamp(llm.last_success_at, tz=UTC)
                                if llm.last_success_at else None),
            # Read live from the provider: a round can take minutes (slow model + fallback).
            "last_error": llm_provider.last_error or llm.last_error,
            "last_model": llm_provider.last_model,
            "paused_until": llm.paused_until,
        },
    }


@router.get("/health/feed")
async def health_feed(request: Request) -> JSONResponse:
    """Data freshness for uptime monitors: 503 when live trades have stopped flowing."""
    stats: FeedStats = request.app.state.feed
    session = None
    market = getattr(request.app.state, "market", None)
    if market is not None:
        try:
            session = (await market.status())["session"]
        except Exception:
            session = None  # calendar down: judge by the connection alone
    problem = stats.problem(session, float(os.environ.get("FEED_STALE_SEC", "600")))
    body = {"status": "degraded" if problem else "ok", "problem": problem,
            "session": session, **_feed_summary(stats)}
    return JSONResponse(jsonable_encoder(body), status_code=503 if problem else 200)
