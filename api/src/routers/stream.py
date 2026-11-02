import asyncio
import json
import logging
import os
import secrets
from collections import defaultdict

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from ..feed import FeedStats
from ..limits import ConnectionLimiter

log = logging.getLogger("api.stream")

router = APIRouter()


class TradeBroadcaster:
    """Fans incoming trade messages out to per-symbol WebSocket subscribers."""

    def __init__(self, queue_size: int = 100):
        self._queue_size = queue_size
        self._subscribers: dict[str, set[asyncio.Queue[str]]] = defaultdict(set)

    def subscribe(self, symbol: str) -> asyncio.Queue[str]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=self._queue_size)
        self._subscribers[symbol].add(queue)
        return queue

    def unsubscribe(self, symbol: str, queue: asyncio.Queue[str]) -> None:
        self._subscribers[symbol].discard(queue)
        if not self._subscribers[symbol]:
            del self._subscribers[symbol]

    def publish(self, symbol: str, message: str) -> None:
        for queue in self._subscribers.get(symbol, ()):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                # Slow client: drop the message rather than stall everyone else.
                pass


async def consume_trades(broadcaster: TradeBroadcaster, stats: FeedStats) -> None:
    """Background task: trades.raw -> broadcaster. Retries until Kafka is up."""
    from aiokafka import AIOKafkaConsumer

    broker = os.environ.get("KAFKA_BROKER", "redpanda:9092")
    topic = os.environ.get("TRADES_TOPIC", "trades.raw")

    while True:
        consumer = AIOKafkaConsumer(
            topic,
            bootstrap_servers=broker,
            auto_offset_reset="latest",
            enable_auto_commit=False,
        )
        try:
            await consumer.start()
            stats.connections = 1
            log.info("Consuming %s from %s", topic, broker)
            async for msg in consumer:
                payload = msg.value.decode()
                try:
                    trade = json.loads(payload)
                    symbol = msg.key.decode() if msg.key else str(trade["symbol"])
                    stats.record_trade(symbol, float(trade["price"]), int(trade["ts"]))
                except (ValueError, KeyError, TypeError):
                    log.warning("Dropping malformed trade: %.200s", payload)
                    continue
                broadcaster.publish(symbol, payload)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Kafka consumer failed; retrying in 5s")
            await asyncio.sleep(5)
        finally:
            stats.connections = 0
            await consumer.stop()


@router.websocket("/ws/{symbol}")
async def ws_trades(websocket: WebSocket, symbol: str) -> None:
    state = websocket.app.state
    symbol = symbol.upper()
    ip = websocket.client.host if websocket.client else "unknown"
    limiter: ConnectionLimiter | None = getattr(state, "ws_limiter", None)
    if symbol not in state.symbols:
        await websocket.close(code=1008)  # rejected during the handshake (HTTP 403)
        return
    if limiter is not None and not limiter.acquire(ip):
        await websocket.close(code=1013)
        return

    await websocket.accept()
    broadcaster: TradeBroadcaster = state.broadcaster
    queue = broadcaster.subscribe(symbol)

    async def forward() -> None:
        while True:
            await websocket.send_text(await queue.get())

    async def until_client_leaves() -> None:
        # Without reading, a client that disconnects while no trades flow (market
        # closed) would only be noticed on the next send, holding its slot meanwhile.
        while (await websocket.receive())["type"] != "websocket.disconnect":
            pass

    tasks = [asyncio.create_task(forward()), asyncio.create_task(until_client_leaves())]
    try:
        await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        broadcaster.unsubscribe(symbol, queue)
        if limiter is not None:
            limiter.release(ip)


@router.websocket("/internal/ingest")
async def ws_ingest(websocket: WebSocket) -> None:
    """Lite mode (TRADE_FEED=ingest): the ingestion service pushes trades here directly.

    Same JSON as the Kafka messages. Each trade is fanned out to /ws/{symbol}
    subscribers and folded into the 1-minute candle aggregator.
    """
    aggregator = getattr(websocket.app.state, "aggregator", None)
    token = os.environ.get("INGEST_TOKEN", "")
    supplied = websocket.headers.get("authorization", "").removeprefix("Bearer ")
    if aggregator is None or not token or not secrets.compare_digest(supplied, token):
        await websocket.close(code=1008)  # rejected during the handshake (HTTP 403)
        return

    await websocket.accept()
    broadcaster: TradeBroadcaster = websocket.app.state.broadcaster
    stats: FeedStats = websocket.app.state.feed
    stats.connections += 1
    try:
        async for payload in websocket.iter_text():
            try:
                trade = json.loads(payload)
                symbol = str(trade["symbol"])
                price, ts = float(trade["price"]), int(trade["ts"])
                aggregator.add(symbol, price, float(trade["volume"]), ts)
            except (ValueError, KeyError, TypeError):
                log.warning("Dropping malformed trade: %.200s", payload)
                continue
            stats.record_trade(symbol, price, ts)
            broadcaster.publish(symbol, payload)
    except WebSocketDisconnect:
        pass
    finally:
        stats.connections -= 1
