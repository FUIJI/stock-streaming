import json

import pytest
from fastapi.testclient import TestClient

from src.aggregator import CandleAggregator
from src.feed import FeedStats
from src.main import app
from src.routers.stream import TradeBroadcaster

TRADE = {"symbol": "AAPL", "price": 100.0, "volume": 5.0, "ts": 1_700_000_040_000}


class FakeMarket:
    def __init__(self, session):
        self.session = session

    async def status(self):
        return {"session": self.session}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "secret")
    monkeypatch.setenv("APP_VERSION", "sha-abc1234")
    app.state.broadcaster = TradeBroadcaster()
    app.state.aggregator = CandleAggregator()
    app.state.feed = FeedStats()
    app.state.market = FakeMarket("regular")
    yield TestClient(app)
    app.state.aggregator = None
    app.state.market = None


def test_health_is_200_and_reports_version_even_without_a_feed(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert '"version":"sha-abc1234"' in resp.text  # what the deploy workflow greps for
    assert resp.json()["feed"]["connections"] == 0


def test_feed_check_follows_the_ingestion_connection(client):
    assert client.get("/health/feed").status_code == 503  # nothing connected
    with client.websocket_connect("/internal/ingest",
                                  headers={"Authorization": "Bearer secret"}) as ws:
        ws.send_text(json.dumps(TRADE))
        ws.send_text(json.dumps(TRADE))
        # The server may still be processing frames; the second request runs after them.
        client.get("/health")
        body = client.get("/health/feed").json()
        assert body["status"] == "ok" and body["connections"] == 1
    assert client.get("/health").json()["feed"]["trades_total"] == 2
    assert client.get("/health/feed").json()["problem"] == "no trade feed connected"


@pytest.mark.parametrize(
    ("session", "idle", "expected"),
    [
        ("regular", 601, "no trades for 601s during the regular session"),
        ("regular", 599, None),
        ("pre", 3600, None),  # IEX is often silent pre-market
        ("post", 3600, None),
        ("closed", 86400, None),
        (None, 86400, None),  # calendar unavailable: only the connection counts
    ],
)
def test_silence_only_counts_during_the_regular_session(session, idle, expected):
    stats = FeedStats(connections=1, last_trade_at=1_000.0)
    assert stats.problem(session, stale_after=600, now=1_000.0 + idle) == expected


def test_silence_since_startup_counts_when_no_trade_ever_arrived():
    stats = FeedStats(connections=1, started_at=1_000.0)
    assert stats.problem("regular", stale_after=600, now=1_700.0) is not None


def test_cors_allows_exact_origins_and_the_preview_pattern(monkeypatch):
    # CORS settings are read at import time, so build a fresh app with the env applied.
    import importlib

    import src.main as main_module

    monkeypatch.setenv("CORS_ORIGINS", "https://stock-streaming.vercel.app")
    monkeypatch.setenv("CORS_ORIGIN_REGEX", r"https://stock-streaming-[a-z0-9]+-fuiji\.vercel\.app")
    fresh = importlib.reload(main_module).app
    fresh.state.feed = FeedStats()
    client = TestClient(fresh)
    try:
        def allowed(origin):
            resp = client.get("/health", headers={"Origin": origin})
            return resp.headers.get("access-control-allow-origin") == origin

        assert allowed("https://stock-streaming.vercel.app")
        assert allowed("https://stock-streaming-eksr9qg7w-fuiji.vercel.app")
        assert not allowed("https://stock-streaming-x-someone-else.vercel.app")
        assert not allowed("https://evil.example")
    finally:
        monkeypatch.delenv("CORS_ORIGIN_REGEX")
        importlib.reload(main_module)
