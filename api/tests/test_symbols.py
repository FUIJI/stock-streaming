import httpx

from src.alpaca import AlpacaClient
from src.db import get_pool
from src.main import app
from src.migrate import migrate
from src.symbols import register_symbols, sync_symbol_metadata


async def test_registered_set_becomes_the_active_set(pg_pool):
    await migrate(pg_pool)
    await register_symbols(pg_pool, ["AAPL", "MSFT"])
    await register_symbols(pg_pool, ["AAPL", "NVDA"])  # SYMBOLS changed
    rows = await pg_pool.fetch("SELECT symbol, active FROM symbols ORDER BY symbol")
    assert [(r["symbol"], r["active"]) for r in rows] == [
        ("AAPL", True), ("MSFT", False), ("NVDA", True),
    ]


async def test_metadata_sync_skips_symbols_alpaca_does_not_know(pg_pool):
    await migrate(pg_pool)
    await register_symbols(pg_pool, ["AAPL", "NOPE"])

    def handler(request):
        if request.url.path.endswith("/AAPL"):
            return httpx.Response(200, json={"name": "Apple Inc. Common Stock",
                                             "exchange": "NASDAQ"})
        return httpx.Response(404, json={"message": "asset not found"})

    alpaca = AlpacaClient("k", "s", "https://paper", transport=httpx.MockTransport(handler))
    await sync_symbol_metadata(pg_pool, alpaca, ["AAPL", "NOPE"])
    rows = await pg_pool.fetch("SELECT symbol, name, exchange FROM symbols ORDER BY symbol")
    assert [tuple(r) for r in rows] == [
        ("AAPL", "Apple Inc. Common Stock", "NASDAQ"), ("NOPE", None, None),
    ]


async def test_symbols_endpoints_list_only_active_symbols(pg_pool):
    await migrate(pg_pool)
    await register_symbols(pg_pool, ["MSFT", "AAPL"])
    await pg_pool.execute("UPDATE symbols SET name = 'Apple Inc.' WHERE symbol = 'AAPL'")
    await pg_pool.execute("INSERT INTO symbols (symbol, active) VALUES ('OLD', false)")
    app.dependency_overrides[get_pool] = lambda: pg_pool
    try:
        client = httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")
        assert (await client.get("/symbols")).json() == ["AAPL", "MSFT"]
        details = (await client.get("/symbols/details")).json()
        assert details[0] == {"symbol": "AAPL", "name": "Apple Inc.", "exchange": None}
    finally:
        app.dependency_overrides.clear()
