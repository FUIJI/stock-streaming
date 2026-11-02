"""Latest price and change vs. the previous close, per symbol.

Alpaca snapshots give the day's bars; the live feed gives a fresher last
price when one arrived after the snapshot's latest trade.
"""

import asyncio
import time
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient

ET = ZoneInfo("America/New_York")
CACHE_TTL_SEC = 15


def _ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def compute_quote(symbol: str, snapshot: dict, live: tuple[float, int] | None,
                  today_et: date) -> dict:
    """One /quotes row. `live` is (price, epoch ms) from the trade feed, if any.

    Before today's first trade (e.g. early pre-market) the snapshot's dailyBar
    is still yesterday's, so yesterday's close is the reference, not prevDailyBar.
    """
    daily = snapshot.get("dailyBar")
    prev = snapshot.get("prevDailyBar")
    daily_is_today = bool(daily) and _ts(daily["t"]).astimezone(ET).date() == today_et
    if daily_is_today:
        prev_close = prev["c"] if prev else None
        day = daily
    else:
        prev_close = daily["c"] if daily else None
        day = None

    price, price_at = None, None
    if trade := snapshot.get("latestTrade"):
        price, price_at = trade["p"], _ts(trade["t"])
    if live and (price_at is None or live[1] / 1000 >= price_at.timestamp()):
        price, price_at = live[0], datetime.fromtimestamp(live[1] / 1000, tz=UTC)

    change = None if price is None or not prev_close else price - prev_close
    return {
        "symbol": symbol,
        "price": price,
        "price_at": price_at,
        "prev_close": prev_close,
        "change": None if change is None else round(change, 4),
        "change_pct": None if change is None else round(change / prev_close * 100, 2),
        "day_open": day["o"] if day else None,
        "day_high": day["h"] if day else None,
        "day_low": day["l"] if day else None,
        "day_volume": day["v"] if day else None,
    }


class SnapshotCache:
    """One Alpaca request per CACHE_TTL_SEC for all tracked symbols."""

    def __init__(self, alpaca: AlpacaClient, feed: str):
        self._alpaca = alpaca
        self._feed = feed
        self._data: dict[str, dict] = {}
        self._key: tuple[str, ...] = ()
        self._expires_at = 0.0
        self._lock = asyncio.Lock()

    async def get(self, symbols: list[str]) -> dict[str, dict]:
        key = tuple(sorted(symbols))
        async with self._lock:
            if key != self._key or time.monotonic() >= self._expires_at:
                self._data = await self._alpaca.snapshots(list(key), self._feed)
                self._key = key
                self._expires_at = time.monotonic() + CACHE_TTL_SEC
            return self._data
