"""Reconcile: overwrite recent candles with Alpaca's official bars.

Fills minutes the live pipeline missed (API/VM downtime) and replaces
candles built from IEX trades with SIP bars (all exchanges, full volume).

    python -m src.jobs reconcile --days 2

The API also runs it by itself (see run_schedule): once shortly after startup
with the live feed, to patch gaps without mixing feeds intraday, and nightly
at RECONCILE_AT_UTC with the history feed, after after-hours trading ends.
"""

import argparse
import asyncio
import logging
import os
from datetime import UTC, datetime, time, timedelta

import asyncpg

from .alpaca import AlpacaClient
from .backfill import backfill_minutes, history_feed, parse_symbols
from .db import create_pool
from .migrate import migrate
from .symbols import sync_symbol_metadata

log = logging.getLogger("api.jobs")


async def reconcile(pool: asyncpg.Pool, alpaca: AlpacaClient, symbols: list[str],
                    days: float, feed: str) -> None:
    await sync_symbol_metadata(pool, alpaca, symbols)
    await backfill_minutes(pool, alpaca, symbols, days, feed)


def next_run(now: datetime, at_utc: time) -> datetime:
    """The next occurrence of `at_utc` strictly after `now`."""
    candidate = datetime.combine(now.date(), at_utc, tzinfo=UTC)
    return candidate if candidate > now else candidate + timedelta(days=1)


def parse_hhmm(value: str) -> time:
    hours, minutes = value.split(":")
    return time(int(hours), int(minutes))


async def run_schedule(pool: asyncpg.Pool, alpaca: AlpacaClient, symbols: list[str]) -> None:
    """Background task inside the API. Failures are logged, never fatal."""
    at_utc = parse_hhmm(os.environ.get("RECONCILE_AT_UTC", "01:30"))
    live_feed = os.environ.get("ALPACA_FEED", "iex").lower()

    async def attempt(days: float, feed: str) -> None:
        try:
            await reconcile(pool, alpaca, symbols, days, feed)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Reconcile (%s days, feed=%s) failed", days, feed)

    await asyncio.sleep(float(os.environ.get("RECONCILE_STARTUP_DELAY_SEC", "30")))
    await attempt(1, live_feed)
    while True:
        wake = next_run(datetime.now(UTC), at_utc)
        log.info("Next reconcile at %s", wake.isoformat())
        await asyncio.sleep((wake - datetime.now(UTC)).total_seconds())
        await attempt(2, history_feed())


async def _cli(days: float, feed: str, symbols: list[str]) -> None:
    alpaca = AlpacaClient.from_env()
    if alpaca is None:
        raise SystemExit("ALPACA_API_KEY and ALPACA_API_SECRET are required")
    pool = await create_pool()
    try:
        await migrate(pool)
        await reconcile(pool, alpaca, symbols, days, feed)
    finally:
        await pool.close()
        await alpaca.aclose()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(description="Maintenance jobs")
    sub = parser.add_subparsers(dest="job", required=True)
    rec = sub.add_parser("reconcile", help="Overwrite recent candles with official bars")
    rec.add_argument("--days", type=float, default=2)
    rec.add_argument("--feed", default=history_feed(), choices=["sip", "iex"])
    rec.add_argument("--symbols", default=os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,TSLA"))
    args = parser.parse_args()
    asyncio.run(_cli(args.days, args.feed, parse_symbols(args.symbols)))


if __name__ == "__main__":
    main()
