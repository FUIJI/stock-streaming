"""Historical bars from Alpaca into ohlcv_1m.

    python -m src.backfill --days 5 [--symbols AAPL,MSFT] [--feed sip|iex]

Reads ALPACA_API_KEY / ALPACA_API_SECRET, ALPACA_HISTORY_FEED (default sip) and
the usual PG_* vars. Also used by the reconcile job (src/jobs.py).
"""

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import asyncpg

from .aggregator import UPSERT_SQL, CandleRow
from .alpaca import AlpacaClient, AlpacaError
from .db import create_pool
from .migrate import migrate

log = logging.getLogger("api.backfill")

# The free plan serves SIP history except the most recent 15 minutes. IEX only
# needs to stay clear of the candle the live aggregator is still building.
SAFETY_MARGIN = {"sip": timedelta(minutes=16), "iex": timedelta(minutes=2)}


def bars_to_rows(bars_by_symbol: dict[str, list[dict]]) -> list[CandleRow]:
    rows: list[CandleRow] = []
    for symbol, bars in bars_by_symbol.items():
        for b in bars:
            bucket_start = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            rows.append((symbol, bucket_start, b["o"], b["h"], b["l"], b["c"],
                         float(b["v"]), int(b.get("n", 0))))
    return rows


async def fetch_bars(alpaca: AlpacaClient, symbols: list[str], timeframe: str,
                     start: datetime, end: datetime, feed: str) -> dict[str, list[dict]]:
    """Like AlpacaClient.bars, but falls back to IEX if the plan refuses SIP."""
    try:
        return await alpaca.bars(symbols, timeframe, start, end, feed)
    except AlpacaError as exc:
        if feed != "sip" or "-> 403" not in str(exc):
            raise
        log.warning("SIP history refused (%s); falling back to IEX", exc)
        return await alpaca.bars(symbols, timeframe, start, end, "iex")


async def backfill_minutes(pool: asyncpg.Pool, alpaca: AlpacaClient, symbols: list[str],
                           days: float, feed: str, now: datetime | None = None) -> int:
    """Upsert 1-minute bars for the last `days` days; returns the number of rows."""
    now = (now or datetime.now(UTC)).replace(second=0, microsecond=0)
    end = now - SAFETY_MARGIN.get(feed, SAFETY_MARGIN["sip"])
    start = end - timedelta(days=days)
    rows = bars_to_rows(await fetch_bars(alpaca, symbols, "1Min", start, end, feed))
    if rows:
        await pool.executemany(UPSERT_SQL, rows)
    log.info("1m: upserted %d bars for %s (%s -> %s, feed=%s)", len(rows), symbols,
             start, end, feed)
    return len(rows)


def history_feed() -> str:
    return os.environ.get("ALPACA_HISTORY_FEED", "sip").lower()


def parse_symbols(raw: str) -> list[str]:
    return [s.strip().upper() for s in raw.split(",") if s.strip()]


async def run(symbols: list[str], days: float, feed: str) -> None:
    alpaca = AlpacaClient.from_env()
    if alpaca is None:
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    pool = await create_pool()
    try:
        await migrate(pool)
        await backfill_minutes(pool, alpaca, symbols, days, feed)
    finally:
        await pool.close()
        await alpaca.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=float, default=5)
    parser.add_argument("--symbols", default=os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,TSLA"))
    parser.add_argument("--feed", default=history_feed(), choices=["sip", "iex"])
    args = parser.parse_args()
    asyncio.run(run(parse_symbols(args.symbols), args.days, args.feed))


if __name__ == "__main__":
    main()
