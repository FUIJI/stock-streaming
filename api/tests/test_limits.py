import time

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from src.feed import FeedStats
from src.limits import ConnectionLimiter, RateLimiter
from src.main import app
from src.routers.stream import TradeBroadcaster


def test_rate_limiter_allows_a_burst_then_refills_over_time():
    rl = RateLimiter(per_minute=60)  # 1 token per second
    assert all(rl.allow("ip", now=0.0) for _ in range(60))
    assert not rl.allow("ip", now=0.0)
    assert rl.allow("other-ip", now=0.0)  # buckets are per client
    assert rl.allow("ip", now=1.0) and not rl.allow("ip", now=1.0)


def test_connection_limiter_caps_total_and_per_ip():
    cl = ConnectionLimiter(max_total=3, max_per_ip=2)
    assert cl.acquire("a") and cl.acquire("a") and not cl.acquire("a")
    assert cl.acquire("b") and not cl.acquire("c")  # total reached
    cl.release("a")
    assert cl.acquire("c")


@pytest.fixture
def client():
    app.state.symbols = ["AAPL"]
    app.state.broadcaster = TradeBroadcaster()
    app.state.feed = FeedStats()
    app.state.ws_limiter = ConnectionLimiter(max_total=100, max_per_ip=2)
    app.state.rate_limiter = RateLimiter(per_minute=3)
    yield TestClient(app)
    app.state.ws_limiter = None
    app.state.rate_limiter = None


def test_rest_gets_429_after_the_quota_but_health_stays_open(client):
    codes = [client.get("/market/status").status_code for _ in range(4)]
    assert codes[-1] == 429 and 429 not in codes[:3]
    assert client.get("/health").status_code == 200


def test_ws_rejects_untracked_symbols(client):
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws/GME"):
            pass


def test_ws_per_ip_cap_and_slots_are_freed_on_disconnect(client):
    with client.websocket_connect("/ws/aapl"), client.websocket_connect("/ws/AAPL"):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws/AAPL"):
                pass
    # Both closed while no trades were flowing: their slots must come back.
    deadline = time.monotonic() + 2
    while app.state.ws_limiter.total and time.monotonic() < deadline:
        time.sleep(0.01)
    assert app.state.ws_limiter.total == 0
    assert app.state.broadcaster._subscribers == {}


def test_ws_still_forwards_trades(client):
    with client.websocket_connect("/ws/AAPL") as ws:
        app.state.broadcaster.publish("AAPL", '{"symbol":"AAPL","price":1}')
        assert ws.receive_text() == '{"symbol":"AAPL","price":1}'
