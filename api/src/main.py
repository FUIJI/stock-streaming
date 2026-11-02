import asyncio
import logging
import os
from contextlib import asynccontextmanager, suppress

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .aggregator import CandleAggregator, flush_candles, load_recent_candles, run_flusher
from .alpaca import AlpacaClient
from .db import create_pool
from .backfill import parse_symbols
from .feed import FeedStats
from .jobs import run_schedule
from .market import MarketCalendar
from .migrate import migrate
from .quotes import SnapshotCache
from .symbols import register_symbols
from .routers import candles, health, market, quotes, stream
from .routers.stream import TradeBroadcaster, consume_trades

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("api")


@asynccontextmanager
async def lifespan(app: FastAPI):
    # kafka: full stack, trades come from Redpanda and Spark writes candles.
    # ingest: lite mode, ingestion pushes to /internal/ingest and we aggregate candles here.
    feed = os.environ.get("TRADE_FEED", "kafka").lower()
    if feed not in ("kafka", "ingest"):
        raise RuntimeError(f"Unknown TRADE_FEED: {feed!r} (expected kafka|ingest)")
    if feed == "ingest" and not os.environ.get("INGEST_TOKEN"):
        raise RuntimeError("TRADE_FEED=ingest requires INGEST_TOKEN")

    app.state.pool = await create_pool()
    await migrate(app.state.pool)
    app.state.broadcaster = TradeBroadcaster()
    app.state.feed = FeedStats()
    app.state.alpaca = AlpacaClient.from_env()
    app.state.market = MarketCalendar(app.state.alpaca) if app.state.alpaca else None
    app.state.snapshots = (SnapshotCache(app.state.alpaca, os.environ.get("ALPACA_FEED", "iex"))
                           if app.state.alpaca else None)
    app.state.symbols = parse_symbols(os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,TSLA"))
    await register_symbols(app.state.pool, app.state.symbols)
    app.state.aggregator = None
    if feed == "kafka":
        task = asyncio.create_task(consume_trades(app.state.broadcaster, app.state.feed))
    else:
        app.state.aggregator = CandleAggregator()
        app.state.aggregator.seed(await load_recent_candles(app.state.pool))
        interval = float(os.environ.get("CANDLE_FLUSH_SEC", "2"))
        task = asyncio.create_task(run_flusher(app.state.aggregator, app.state.pool, interval))
    tasks = [task]
    if app.state.alpaca is not None and os.environ.get("RECONCILE_AT_UTC", "01:30") != "off":
        tasks.append(asyncio.create_task(
            run_schedule(app.state.pool, app.state.alpaca, app.state.symbols)))
    log.info("Trade feed: %s", feed)
    yield
    for t in tasks:
        t.cancel()
    for t in tasks:
        with suppress(asyncio.CancelledError):
            await t
    if app.state.aggregator is not None:
        with suppress(Exception):
            await flush_candles(app.state.aggregator, app.state.pool)
    if app.state.alpaca is not None:
        await app.state.alpaca.aclose()
    await app.state.pool.close()


app = FastAPI(title="Stock Streaming API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=os.environ.get("CORS_ORIGINS", "http://localhost:3000").split(","),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(candles.router)
app.include_router(health.router)
app.include_router(market.router)
app.include_router(quotes.router)
app.include_router(stream.router)

