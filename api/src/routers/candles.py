import os
from datetime import datetime, timedelta

import asyncpg
from fastapi import APIRouter, Depends, HTTPException, Query

from ..db import get_pool
from ..models import Candle, SymbolInfo

router = APIRouter()

# Whitelist: interval -> relation. Never interpolate user input into SQL directly.
INTERVAL_SOURCES = {
    "1m": "ohlcv_1m",
    "5m": "ohlcv_5m",
    "1h": "ohlcv_1h",
    "1d": "ohlcv_1d",
}
INTERVAL_WIDTHS = {
    "1m": timedelta(minutes=1),
    "5m": timedelta(minutes=5),
    "1h": timedelta(hours=1),
}

# Days before today come from Alpaca's daily bars; today (in New York) is rolled up
# from the 1-minute candles so it moves with the live feed. Same in both rollup modes.
DAILY_SQL = """
    WITH today AS (
        SELECT (date_trunc('day', now() AT TIME ZONE 'America/New_York')
                AT TIME ZONE 'America/New_York') AS start
    )
    SELECT * FROM (
        SELECT symbol, bucket_start, open, high, low, close, volume, trade_count
        FROM ohlcv_1d, today
        WHERE symbol = $1 AND bucket_start < today.start
          AND ($2::timestamptz IS NULL OR bucket_start >= $2)
          AND ($3::timestamptz IS NULL OR bucket_start <= $3)
        UNION ALL
        SELECT $1::text, today.start,
               (array_agg(open ORDER BY bucket_start))[1],
               max(high), min(low),
               (array_agg(close ORDER BY bucket_start DESC))[1],
               sum(volume), sum(trade_count)::bigint
        FROM ohlcv_1m, today
        WHERE symbol = $1 AND bucket_start >= today.start
          AND ($2::timestamptz IS NULL OR today.start >= $2)
          AND ($3::timestamptz IS NULL OR today.start <= $3)
        GROUP BY today.start
    ) days
    ORDER BY bucket_start DESC
    LIMIT $4
"""

# CANDLE_ROLLUP=sql: plain Postgres (Supabase/Neon have no continuous aggregates),
# so coarser intervals are rolled up from ohlcv_1m at query time.
ROLLUP_SQL = """
    SELECT symbol,
           date_bin($5::interval, bucket_start, TIMESTAMPTZ '2000-01-01') AS bucket_start,
           (array_agg(open ORDER BY bucket_start))[1]       AS open,
           max(high)                                        AS high,
           min(low)                                         AS low,
           (array_agg(close ORDER BY bucket_start DESC))[1] AS close,
           sum(volume)                                      AS volume,
           sum(trade_count)::bigint                         AS trade_count
    FROM ohlcv_1m
    WHERE symbol = $1
      AND ($2::timestamptz IS NULL OR bucket_start >= $2)
      AND ($3::timestamptz IS NULL OR bucket_start <= $3)
    GROUP BY 1, 2
    ORDER BY 2 DESC
    LIMIT $4
"""


@router.get("/candles", response_model=list[Candle])
async def get_candles(
    symbol: str,
    interval: str = "1m",
    start: datetime | None = None,
    end: datetime | None = None,
    limit: int = Query(default=500, ge=1, le=5000),
    pool: asyncpg.Pool = Depends(get_pool),
) -> list[Candle]:
    table = INTERVAL_SOURCES.get(interval)
    if table is None:
        raise HTTPException(
            status_code=422,
            detail=f"Unknown interval {interval!r}; expected one of {sorted(INTERVAL_SOURCES)}",
        )
    args = [symbol.upper(), start, end, limit]
    if interval == "1d":
        query = DAILY_SQL
    elif interval != "1m" and os.environ.get("CANDLE_ROLLUP", "timescale").lower() == "sql":
        query = ROLLUP_SQL
        args.append(INTERVAL_WIDTHS[interval])
    else:
        query = f"""
            SELECT symbol, bucket_start, open, high, low, close, volume, trade_count
            FROM {table}
            WHERE symbol = $1
              AND ($2::timestamptz IS NULL OR bucket_start >= $2)
              AND ($3::timestamptz IS NULL OR bucket_start <= $3)
            ORDER BY bucket_start DESC
            LIMIT $4
        """
    rows = await pool.fetch(query, *args)
    # Query newest-first to honor the limit, return oldest-first for charting.
    return [Candle(**dict(row)) for row in reversed(rows)]


@router.get("/symbols", response_model=list[str])
async def get_symbols(pool: asyncpg.Pool = Depends(get_pool)) -> list[str]:
    rows = await pool.fetch("SELECT symbol FROM symbols WHERE active ORDER BY symbol")
    return [row["symbol"] for row in rows]


@router.get("/symbols/details", response_model=list[SymbolInfo])
async def get_symbol_details(pool: asyncpg.Pool = Depends(get_pool)) -> list[SymbolInfo]:
    rows = await pool.fetch(
        "SELECT symbol, name, exchange FROM symbols WHERE active ORDER BY symbol")
    return [SymbolInfo(**dict(row)) for row in rows]
