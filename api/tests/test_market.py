from datetime import UTC, datetime

import httpx
import pytest

from src.alpaca import AlpacaClient
from src.market import MarketCalendar, market_status, parse_calendar

# Real-shaped /v2/calendar rows around the Nov 2026 DST change (Nov 1) and Thanksgiving.
CALENDAR = [
    {"date": "2026-10-30", "open": "09:30", "close": "16:00", "session_open": "0400", "session_close": "2000"},
    {"date": "2026-11-02", "open": "09:30", "close": "16:00", "session_open": "0400", "session_close": "2000"},
    {"date": "2026-11-25", "open": "09:30", "close": "16:00", "session_open": "0400", "session_close": "2000"},
    # 11-26 Thanksgiving: absent. 11-27: early close at 13:00 ET.
    {"date": "2026-11-27", "open": "09:30", "close": "13:00", "session_open": "0400", "session_close": "1700"},
]
DAYS = parse_calendar(CALENDAR)


def at(iso: str) -> datetime:
    return datetime.fromisoformat(iso).replace(tzinfo=UTC)


@pytest.mark.parametrize(
    ("now", "session", "next_session", "next_change"),
    [
        # Friday, still EDT (UTC-4): regular 13:30-20:00 UTC
        ("2026-10-30T07:59", "closed", "pre", "2026-10-30T08:00"),
        ("2026-10-30T08:00", "pre", "regular", "2026-10-30T13:30"),
        ("2026-10-30T13:30", "regular", "post", "2026-10-30T20:00"),
        ("2026-10-30T23:59", "post", "closed", "2026-10-31T00:00"),
        # Weekend -> Monday, now EST (UTC-5): pre-market opens 09:00 UTC
        ("2026-10-31T12:00", "closed", "pre", "2026-11-02T09:00"),
        ("2026-11-02T14:30", "regular", "post", "2026-11-02T21:00"),
        # Thanksgiving: closed all day, next is Friday's pre-market
        ("2026-11-26T15:00", "closed", "pre", "2026-11-27T09:00"),
        # Early close: regular ends 13:00 ET (18:00 UTC), post ends 17:00 ET (22:00 UTC)
        ("2026-11-27T18:30", "post", "closed", "2026-11-27T22:00"),
    ],
)
def test_sessions_follow_the_exchange_calendar(now, session, next_session, next_change):
    status = market_status(at(now), DAYS)
    assert status["session"] == session
    assert status["next_session"] == next_session
    assert status["next_change_at"] == at(next_change)


def test_today_is_none_on_a_holiday():
    assert market_status(at("2026-11-26T15:00"), DAYS)["today"] is None
    today = market_status(at("2026-11-02T14:30"), DAYS)["today"]
    assert today["open"] == at("2026-11-02T14:30") and today["post_close"] == at("2026-11-03T01:00")


async def test_calendar_keeps_serving_cache_when_alpaca_fails():
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(200, json=CALENDAR)
        return httpx.Response(500, text="boom")

    client = AlpacaClient("k", "s", "https://paper", transport=httpx.MockTransport(handler))
    cal = MarketCalendar(client)
    assert (await cal.status(at("2026-11-02T14:30")))["session"] == "regular"
    cal._expires_at = 0  # force a refresh, which fails
    assert (await cal.status(at("2026-11-02T14:30")))["session"] == "regular"
    assert calls["n"] == 2
