import asyncio
import json

import pytest
import websockets

from src.schemas import Trade
from src.sinks import ApiSink, build_sink


async def trades_of(*symbols):
    for i, symbol in enumerate(symbols, start=1):
        yield Trade(symbol=symbol, price=float(i), volume=1.0, ts=i)


async def test_api_sink_sends_trades_with_bearer_token():
    received, auth_headers = [], []

    async def handler(ws):
        auth_headers.append(ws.request.headers["Authorization"])
        async for msg in ws:
            received.append(json.loads(msg))

    async with websockets.serve(handler, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        sink = ApiSink(f"ws://127.0.0.1:{port}", token="secret")
        await sink.consume(trades_of("AAPL", "MSFT"))
        # Give the server a moment to drain the last frame.
        for _ in range(50):
            if len(received) == 2:
                break
            await asyncio.sleep(0.01)

    assert auth_headers == ["Bearer secret"]
    assert [t["symbol"] for t in received] == ["AAPL", "MSFT"]


def test_api_sink_requires_token():
    with pytest.raises(ValueError):
        ApiSink("ws://api:8000/internal/ingest", token="")


def test_build_sink_rejects_unknown():
    with pytest.raises(ValueError):
        build_sink("carrier-pigeon", "b", "t", "ws://x", "tok")
