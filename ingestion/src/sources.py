import asyncio
import itertools
import json
import logging
import random
import time
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Protocol

from .schemas import Trade

log = logging.getLogger(__name__)


class TradeSource(Protocol):
    def stream(self) -> AsyncIterator[Trade]: ...


class SimulatorSource:
    """Synthetic trades via a per-symbol geometric random walk.

    Lets the whole pipeline run 24/7 without a market-data key, including
    while US markets are closed.
    """

    def __init__(self, symbols: list[str], trades_per_sec: float, seed: int | None = None):
        if not symbols:
            raise ValueError("SimulatorSource needs at least one symbol")
        self._symbols = symbols
        # trades_per_sec is per symbol; pace the combined round-robin stream accordingly.
        self._interval = 1.0 / (trades_per_sec * len(symbols))
        self._rng = random.Random(seed)
        self._prices = {s: self._rng.uniform(50.0, 500.0) for s in symbols}

    def _next_trade(self, symbol: str) -> Trade:
        drift = self._rng.gauss(0.0, 0.0008)
        self._prices[symbol] = max(0.01, self._prices[symbol] * (1.0 + drift))
        return Trade(
            symbol=symbol,
            price=round(self._prices[symbol], 2),
            volume=float(self._rng.randint(1, 500)),
            ts=int(time.time() * 1000),
        )

    async def stream(self) -> AsyncIterator[Trade]:
        for symbol in itertools.cycle(self._symbols):
            yield self._next_trade(symbol)
            await asyncio.sleep(self._interval)


class FinnhubSource:
    """Real trades from the Finnhub WebSocket API (requires FINNHUB_API_KEY).

    Only emits data during US market hours; outside of them the socket is
    silent — use SimulatorSource for round-the-clock development.
    """

    URL = "wss://ws.finnhub.io"

    def __init__(self, symbols: list[str], api_key: str):
        if not api_key:
            raise ValueError("FinnhubSource requires FINNHUB_API_KEY")
        self._symbols = symbols
        self._api_key = api_key

    async def stream(self) -> AsyncIterator[Trade]:
        import websockets

        # `async for ws in connect(...)` reconnects forever with backoff.
        async for ws in websockets.connect(f"{self.URL}?token={self._api_key}"):
            try:
                for symbol in self._symbols:
                    await ws.send(json.dumps({"type": "subscribe", "symbol": symbol}))
                log.info("Finnhub: subscribed to %s", self._symbols)
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("type") != "trade":
                        continue
                    for t in msg.get("data", []):
                        yield Trade(
                            symbol=t["s"], price=t["p"], volume=t.get("v", 0.0), ts=t["t"]
                        )
            except websockets.ConnectionClosed:
                log.warning("Finnhub connection closed, reconnecting")
                continue


def _parse_rfc3339_ms(value: str) -> int:
    """Alpaca timestamps carry nanoseconds ("...44.208123456Z"); keep microseconds."""
    head, dot, frac = value.removesuffix("Z").partition(".")
    if dot:
        head = f"{head}.{frac[:6]}"
    return int(datetime.fromisoformat(f"{head}+00:00").timestamp() * 1000)


def parse_alpaca_message(msg: dict) -> Trade | None:
    """One element of an Alpaca stream frame -> Trade, or None for non-trade messages.

    Raises RuntimeError on stream errors (bad credentials, connection limit, ...).
    """
    kind = msg.get("T")
    if kind == "error":
        raise RuntimeError(f"Alpaca stream error {msg.get('code')}: {msg.get('msg')}")
    if kind != "t":
        return None
    return Trade(
        symbol=msg["S"], price=msg["p"], volume=float(msg.get("s", 0)),
        ts=_parse_rfc3339_ms(msg["t"]),
    )


class AlpacaSource:
    """Real trades from the Alpaca market data WebSocket (free plan: IEX feed).

    IEX is a single exchange (a few % of US volume), so prices track the
    consolidated tape closely but volumes are much lower. feed="test" streams
    the synthetic FAKEPACA symbol around the clock for smoke tests.
    """

    URL = "wss://stream.data.alpaca.markets/v2/{feed}"

    def __init__(self, symbols: list[str], api_key: str, api_secret: str, feed: str = "iex"):
        if not (api_key and api_secret):
            raise ValueError("AlpacaSource requires ALPACA_API_KEY and ALPACA_API_SECRET")
        self._symbols = symbols
        self._api_key = api_key
        self._api_secret = api_secret
        self._url = self.URL.format(feed=feed)

    async def stream(self) -> AsyncIterator[Trade]:
        import websockets

        async for ws in websockets.connect(self._url):
            try:
                await ws.send(
                    json.dumps(
                        {"action": "auth", "key": self._api_key, "secret": self._api_secret}
                    )
                )
                await ws.send(json.dumps({"action": "subscribe", "trades": self._symbols}))
                log.info("Alpaca: subscribed to %s on %s", self._symbols, self._url)
                async for raw in ws:
                    for msg in json.loads(raw):
                        trade = parse_alpaca_message(msg)
                        if trade is not None:
                            yield trade
            except websockets.ConnectionClosed:
                log.warning("Alpaca connection closed, reconnecting")
                continue


def build_source(
    trade_source: str,
    symbols: list[str],
    sim_trades_per_sec: float,
    finnhub_api_key: str,
    alpaca_api_key: str = "",
    alpaca_api_secret: str = "",
    alpaca_feed: str = "iex",
) -> TradeSource:
    if trade_source == "simulator":
        return SimulatorSource(symbols, sim_trades_per_sec)
    if trade_source == "finnhub":
        return FinnhubSource(symbols, finnhub_api_key)
    if trade_source == "alpaca":
        return AlpacaSource(symbols, alpaca_api_key, alpaca_api_secret, alpaca_feed)
    raise ValueError(
        f"Unknown TRADE_SOURCE: {trade_source!r} (expected simulator|finnhub|alpaca)"
    )
