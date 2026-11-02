from datetime import UTC, datetime, time

import httpx
import pytest

from src.alpaca import AlpacaClient, AlpacaError
from src.backfill import backfill_minutes, bars_to_rows
from src.jobs import next_run, reconcile

NOW = datetime(2026, 11, 2, 15, 0, 30, tzinfo=UTC)
BAR = {"t": "2026-11-02T14:30:00Z", "o": 1.0, "h": 2.0, "l": 0.5, "c": 1.5, "v": 300, "n": 12}


def test_bars_to_rows_maps_alpaca_bars_to_candle_rows():
    assert bars_to_rows({"AAPL": [BAR], "MSFT": []}) == [
        ("AAPL", datetime(2026, 11, 2, 14, 30, tzinfo=UTC), 1.0, 2.0, 0.5, 1.5, 300.0, 12),
    ]


class FakePool:
    def __init__(self):
        self.rows = []

    async def executemany(self, query, rows):
        self.rows.extend(rows)


def alpaca_with(handler) -> AlpacaClient:
    return AlpacaClient("k", "s", "https://paper", transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(("feed", "expected_end"), [
    ("sip", "2026-11-02T14:44:00+00:00"),  # free plan: no SIP for the last 15 minutes
    ("iex", "2026-11-02T14:58:00+00:00"),  # just clear of the live candle
])
async def test_window_stops_short_of_what_the_feed_allows(feed, expected_end):
    seen = []

    def handler(request):
        seen.append(dict(request.url.params))
        return httpx.Response(200, json={"bars": {"AAPL": [BAR]}, "next_page_token": None})

    pool = FakePool()
    assert await backfill_minutes(pool, alpaca_with(handler), ["AAPL"], 1, feed, now=NOW) == 1
    assert seen[0]["end"] == expected_end and seen[0]["feed"] == feed
    assert pool.rows[0][0] == "AAPL"


async def test_follows_pagination():
    pages = iter([
        {"bars": {"AAPL": [BAR]}, "next_page_token": "p2"},
        {"bars": {"AAPL": [{**BAR, "t": "2026-11-02T14:31:00Z"}]}, "next_page_token": None},
    ])
    tokens = []

    def handler(request):
        tokens.append(request.url.params.get("page_token"))
        return httpx.Response(200, json=next(pages))

    pool = FakePool()
    assert await backfill_minutes(pool, alpaca_with(handler), ["AAPL"], 1, "iex", now=NOW) == 2
    assert tokens == [None, "p2"]


async def test_sip_refusal_falls_back_to_iex():
    feeds = []

    def handler(request):
        feeds.append(request.url.params["feed"])
        if request.url.params["feed"] == "sip":
            return httpx.Response(403, json={"message": "subscription does not permit"})
        return httpx.Response(200, json={"bars": {"AAPL": [BAR]}})

    pool = FakePool()
    assert await backfill_minutes(pool, alpaca_with(handler), ["AAPL"], 1, "sip", now=NOW) == 1
    assert feeds == ["sip", "iex"]


async def test_other_errors_are_not_swallowed():
    alpaca = alpaca_with(lambda request: httpx.Response(401, text="bad key"))
    with pytest.raises(AlpacaError, match="401"):
        await backfill_minutes(FakePool(), alpaca, ["AAPL"], 1, "sip", now=NOW)


@pytest.mark.parametrize(("now", "expected"), [
    ("2026-11-02T00:00:00", "2026-11-02T01:30:00"),
    ("2026-11-02T01:30:00", "2026-11-03T01:30:00"),  # exactly at the time: tomorrow
    ("2026-11-02T20:00:00", "2026-11-03T01:30:00"),
])
def test_next_run(now, expected):
    got = next_run(datetime.fromisoformat(now).replace(tzinfo=UTC), time(1, 30))
    assert got == datetime.fromisoformat(expected).replace(tzinfo=UTC)


async def test_reconcile_fills_gaps_and_overwrites_live_candles(pg_pool):
    from src.migrate import migrate

    await migrate(pg_pool)
    # A live (IEX) candle at 14:30 with tiny volume; 14:31 missing (downtime).
    await pg_pool.execute(
        "INSERT INTO ohlcv_1m VALUES ('AAPL', '2026-11-02T14:30:00Z', 1, 1, 1, 1, 3, 1, now())"
    )
    official = [BAR, {**BAR, "t": "2026-11-02T14:31:00Z", "c": 1.7}]

    def handler(request):
        return httpx.Response(200, json={"bars": {"AAPL": official}})

    await reconcile(pg_pool, alpaca_with(handler), ["AAPL"], 1, "sip")
    rows = await pg_pool.fetch(
        "SELECT bucket_start, close, volume FROM ohlcv_1m ORDER BY bucket_start")
    assert [(r["close"], r["volume"]) for r in rows] == [(1.5, 300.0), (1.7, 300.0)]
