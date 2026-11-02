"""Thin async client for the Alpaca REST APIs this service uses.

Market data (bars, snapshots) lives on data.alpaca.markets; the trading
calendar and asset metadata on the trading API (paper keys work for both).
"""

import os
from datetime import datetime

import httpx

DATA_URL = "https://data.alpaca.markets"


class AlpacaError(RuntimeError):
    pass


class AlpacaClient:
    def __init__(self, api_key: str, api_secret: str, trading_url: str,
                 transport: httpx.AsyncBaseTransport | None = None):
        headers = {"APCA-API-KEY-ID": api_key, "APCA-API-SECRET-KEY": api_secret}
        self._data = httpx.AsyncClient(base_url=DATA_URL, headers=headers, timeout=30,
                                       transport=transport)
        self._trading = httpx.AsyncClient(base_url=trading_url, headers=headers, timeout=30,
                                          transport=transport)

    @classmethod
    def from_env(cls) -> "AlpacaClient | None":
        key, secret = os.environ.get("ALPACA_API_KEY", ""), os.environ.get("ALPACA_API_SECRET", "")
        if not (key and secret):
            return None
        return cls(key, secret,
                   os.environ.get("ALPACA_TRADING_URL", "https://paper-api.alpaca.markets"))

    async def aclose(self) -> None:
        await self._data.aclose()
        await self._trading.aclose()

    @staticmethod
    async def _get(client: httpx.AsyncClient, path: str, params: dict | None = None):
        resp = await client.get(path, params=params)
        if resp.status_code >= 400:
            raise AlpacaError(f"GET {path} -> {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    async def calendar(self, start: str, end: str) -> list[dict]:
        """Trading days between two YYYY-MM-DD dates (inclusive)."""
        return await self._get(self._trading, "/v2/calendar", {"start": start, "end": end})

    async def asset(self, symbol: str) -> dict:
        return await self._get(self._trading, f"/v2/assets/{symbol}")

    async def snapshots(self, symbols: list[str], feed: str) -> dict:
        return await self._get(self._data, "/v2/stocks/snapshots",
                               {"symbols": ",".join(symbols), "feed": feed})

    async def bars(self, symbols: list[str], timeframe: str, start: datetime, end: datetime,
                   feed: str) -> dict[str, list[dict]]:
        """All bars in [start, end], following next_page_token; keyed by symbol."""
        params = {
            "symbols": ",".join(symbols), "timeframe": timeframe,
            "start": start.isoformat(), "end": end.isoformat(),
            "feed": feed, "limit": 10000, "adjustment": "raw",
        }
        out: dict[str, list[dict]] = {}
        while True:
            payload = await self._get(self._data, "/v2/stocks/bars", params)
            for symbol, bars in (payload.get("bars") or {}).items():
                out.setdefault(symbol, []).extend(bars)
            token = payload.get("next_page_token")
            if not token:
                return out
            params["page_token"] = token
