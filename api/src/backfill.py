"""One-shot backfill of historical 1-minute bars from Alpaca into ohlcv_1m.

    python -m src.backfill --days 5 --symbols AAPL,MSFT

Reads ALPACA_API_KEY / ALPACA_API_SECRET / ALPACA_FEED and the usual PG_* vars.
Stops two minutes short of now so it never overwrites the candle the live
aggregator is still building.
"""

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime, timedelta

import httpx

from .aggregator import UPSERT_SQL, CandleRow
from .db import create_pool

log = logging.getLogger("api.backfill")

BARS_URL = "https://data.alpaca.markets/v2/stocks/bars"


def parse_bars(payload: dict) -> list[CandleRow]:
    rows: list[CandleRow] = []
    for symbol, bars in (payload.get("bars") or {}).items():
        for b in bars:
            bucket_start = datetime.fromisoformat(b["t"].replace("Z", "+00:00"))
            rows.append((symbol, bucket_start, b["o"], b["h"], b["l"], b["c"],
                         float(b["v"]), int(b.get("n", 0))))
    return rows


async def fetch_bars(client: httpx.AsyncClient, symbols: list[str], start: datetime,
                     end: datetime, feed: str) -> list[CandleRow]:
    params = {
        "symbols": ",".join(symbols),
        "timeframe": "1Min",
        "start": start.isoformat(),
        "end": end.isoformat(),
        "feed": feed,
        "limit": 10000,
        "adjustment": "raw",
    }
    rows: list[CandleRow] = []
    while True:
        resp = await client.get(BARS_URL, params=params)
        resp.raise_for_status()
        payload = resp.json()
        rows.extend(parse_bars(payload))
        token = payload.get("next_page_token")
        if not token:
            return rows
        params["page_token"] = token


async def run(symbols: list[str], days: int) -> None:
    key, secret = os.environ.get("ALPACA_API_KEY", ""), os.environ.get("ALPACA_API_SECRET", "")
    if not (key and secret):
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    end = datetime.now(UTC).replace(second=0, microsecond=0) - timedelta(minutes=2)
    start = end - timedelta(days=days)
    headers = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    async with httpx.AsyncClient(headers=headers, timeout=30) as client:
        rows = await fetch_bars(client, symbols, start, end,
                                os.environ.get("ALPACA_FEED", "iex"))
    log.info("Fetched %d bars for %s (%s -> %s)", len(rows), symbols, start, end)
    if not rows:
        return
    pool = await create_pool()
    try:
        await pool.executemany(UPSERT_SQL, rows)
    finally:
        await pool.close()
    log.info("Upserted %d bars into ohlcv_1m", len(rows))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=5)
    parser.add_argument("--symbols", default=os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,TSLA"))
    args = parser.parse_args()
    symbols = [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
    asyncio.run(run(symbols, args.days))


if __name__ == "__main__":
    main()
