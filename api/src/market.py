"""US equity market sessions (pre / regular / post / closed) from Alpaca's calendar."""

import asyncio
import logging
import time
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from .alpaca import AlpacaClient

log = logging.getLogger("api.market")

ET = ZoneInfo("America/New_York")
CACHE_TTL_SEC = 6 * 3600


@dataclass(frozen=True)
class TradingDay:
    date: date
    pre_open: datetime  # all UTC
    open: datetime
    close: datetime
    post_close: datetime


def _at(day: date, hhmm: str) -> datetime:
    hhmm = hhmm.replace(":", "")
    local = datetime(day.year, day.month, day.day, int(hhmm[:2]), int(hhmm[2:]), tzinfo=ET)
    return local.astimezone(UTC)


def parse_calendar(rows: list[dict]) -> list[TradingDay]:
    """Alpaca /v2/calendar rows (times are ET, e.g. open "09:30", session_open "0400")."""
    days = []
    for row in rows:
        d = date.fromisoformat(row["date"])
        days.append(TradingDay(d, _at(d, row["session_open"]), _at(d, row["open"]),
                               _at(d, row["close"]), _at(d, row["session_close"])))
    return sorted(days, key=lambda t: t.date)


def market_status(now: datetime, days: list[TradingDay]) -> dict:
    """Current session and the next change, for a list of trading days around `now`."""
    today = next((d for d in days if d.date == now.astimezone(ET).date()), None)
    session = "closed"
    if today:
        if today.pre_open <= now < today.open:
            session = "pre"
        elif today.open <= now < today.close:
            session = "regular"
        elif today.close <= now < today.post_close:
            session = "post"

    boundaries = [
        (at, label)
        for d in days
        for at, label in ((d.pre_open, "pre"), (d.open, "regular"),
                          (d.close, "post"), (d.post_close, "closed"))
    ]
    upcoming = min(((at, label) for at, label in boundaries if at > now), default=None)
    return {
        "session": session,
        "next_session": upcoming[1] if upcoming else None,
        "next_change_at": upcoming[0] if upcoming else None,
        "today": None if today is None else {
            "date": today.date.isoformat(),
            "pre_open": today.pre_open, "open": today.open,
            "close": today.close, "post_close": today.post_close,
        },
    }


class MarketCalendar:
    """Caches ~2 weeks of trading days; keeps serving the stale copy if Alpaca is down."""

    def __init__(self, alpaca: AlpacaClient):
        self._alpaca = alpaca
        self._days: list[TradingDay] = []
        self._expires_at = 0.0  # time.monotonic() deadline
        self._lock = asyncio.Lock()

    async def days(self) -> list[TradingDay]:
        async with self._lock:
            if time.monotonic() >= self._expires_at:
                today = datetime.now(ET).date()
                try:
                    rows = await self._alpaca.calendar(
                        (today - timedelta(days=3)).isoformat(),
                        (today + timedelta(days=14)).isoformat(),
                    )
                    self._days = parse_calendar(rows)
                    self._expires_at = time.monotonic() + CACHE_TTL_SEC
                except Exception:
                    if not self._days:
                        raise
                    self._expires_at = time.monotonic() + 60  # don't retry on every request
                    log.exception("Calendar refresh failed; serving cached days")
            return self._days

    async def status(self, now: datetime | None = None) -> dict:
        return market_status(now or datetime.now(UTC), await self.days())
