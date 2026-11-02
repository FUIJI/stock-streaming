from datetime import UTC, date, datetime

import pytest
from fastapi.testclient import TestClient

from src.feed import FeedStats
from src.main import app
from src.quotes import compute_quote

TODAY = date(2026, 11, 3)  # Tuesday
# Daily bars are stamped at midnight ET: 05:00Z in November (EST).
MON = {"t": "2026-11-02T05:00:00Z", "o": 100.0, "h": 104.0, "l": 99.0, "c": 102.0, "v": 5e7}
TUE = {"t": "2026-11-03T05:00:00Z", "o": 103.0, "h": 106.0, "l": 102.5, "c": 105.0, "v": 2e7}


def test_regular_session_compares_with_the_previous_close():
    snap = {"latestTrade": {"t": "2026-11-03T16:00:00Z", "p": 105.0},
            "dailyBar": TUE, "prevDailyBar": MON}
    q = compute_quote("AAPL", snap, None, TODAY)
    assert (q["price"], q["prev_close"], q["change"], q["change_pct"]) == (105.0, 102.0, 3.0, 2.94)
    assert (q["day_open"], q["day_high"], q["day_low"]) == (103.0, 106.0, 102.5)


def test_before_todays_first_trade_yesterdays_close_is_the_reference():
    # Pre-market, no daily bar for today yet: dailyBar is Monday, prevDailyBar is Friday.
    snap = {"latestTrade": {"t": "2026-11-02T23:59:00Z", "p": 101.0},
            "dailyBar": MON, "prevDailyBar": {**MON, "c": 90.0}}
    q = compute_quote("AAPL", snap, None, TODAY)
    assert (q["prev_close"], q["change"], q["day_open"]) == (102.0, -1.0, None)


def test_a_newer_live_trade_wins_over_the_snapshot():
    snap = {"latestTrade": {"t": "2026-11-03T16:00:00Z", "p": 105.0},
            "dailyBar": TUE, "prevDailyBar": MON}
    live_ts = int(datetime(2026, 11, 3, 16, 0, 5, tzinfo=UTC).timestamp() * 1000)
    q = compute_quote("AAPL", snap, (106.0, live_ts), TODAY)
    assert q["price"] == 106.0 and q["price_at"] == datetime(2026, 11, 3, 16, 0, 5, tzinfo=UTC)
    older = compute_quote("AAPL", snap, (99.0, live_ts - 60_000), TODAY)
    assert older["price"] == 105.0


def test_missing_data_gives_nulls_not_errors():
    q = compute_quote("AAPL", {}, None, TODAY)
    assert q["price"] is None and q["change_pct"] is None


class FakeSnapshots:
    def __init__(self):
        self.requested = []

    async def get(self, symbols):
        self.requested.append(symbols)
        return {"AAPL": {"latestTrade": {"t": "2026-11-03T16:00:00Z", "p": 105.0},
                         "dailyBar": TUE, "prevDailyBar": MON}}


@pytest.fixture
def client():
    app.state.symbols = ["AAPL", "MSFT"]
    app.state.snapshots = FakeSnapshots()
    app.state.feed = FeedStats()
    yield TestClient(app)
    app.state.snapshots = None


def test_quotes_endpoint_returns_requested_tracked_symbols(client):
    body = client.get("/quotes", params={"symbols": "aapl"}).json()
    assert [q["symbol"] for q in body] == ["AAPL"]
    # One cached upstream call covers every tracked symbol.
    assert app.state.snapshots.requested == [["AAPL", "MSFT"]]
    assert [q["symbol"] for q in client.get("/quotes").json()] == ["AAPL", "MSFT"]


def test_untracked_symbols_are_rejected_without_calling_alpaca(client):
    resp = client.get("/quotes", params={"symbols": "AAPL,GME"})
    assert resp.status_code == 422 and "GME" in resp.text
    assert app.state.snapshots.requested == []
