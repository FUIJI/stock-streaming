import json

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.aggregator import CandleAggregator
from src.main import app
from src.routers.stream import TradeBroadcaster

TRADE = {"symbol": "AAPL", "price": 100.0, "volume": 5.0, "ts": 1_700_000_040_000}


@pytest.fixture
def lite_client(monkeypatch):
    monkeypatch.setenv("INGEST_TOKEN", "secret")
    # No `with` block: skip lifespan (it would open a real DB pool) and wire state by hand.
    app.state.broadcaster = TradeBroadcaster()
    app.state.aggregator = CandleAggregator()
    yield TestClient(app)
    app.state.aggregator = None


def test_ingest_fans_out_and_aggregates(lite_client):
    queue = app.state.broadcaster.subscribe("AAPL")
    with lite_client.websocket_connect(
        "/internal/ingest", headers={"Authorization": "Bearer secret"}
    ) as ws:
        ws.send_text(json.dumps(TRADE))
        ws.send_text("not json")  # malformed frames are dropped, socket stays open
        ws.send_text(json.dumps({**TRADE, "price": 101.0, "ts": TRADE["ts"] + 1}))

    assert json.loads(queue.get_nowait())["price"] == 100.0
    assert json.loads(queue.get_nowait())["price"] == 101.0
    [row] = app.state.aggregator.pop_dirty()
    assert row[2:6] == (100.0, 101.0, 100.0, 101.0) and row[7] == 2


@pytest.mark.parametrize("headers", [{}, {"Authorization": "Bearer wrong"}])
def test_ingest_rejects_bad_token(lite_client, headers):
    with pytest.raises(WebSocketDisconnect):
        with lite_client.websocket_connect("/internal/ingest", headers=headers):
            pass


def test_ingest_disabled_outside_lite_mode(lite_client):
    app.state.aggregator = None  # TRADE_FEED=kafka
    with pytest.raises(WebSocketDisconnect):
        with lite_client.websocket_connect(
            "/internal/ingest", headers={"Authorization": "Bearer secret"}
        ):
            pass
