import logging

import asyncpg

from .alpaca import AlpacaClient

log = logging.getLogger("api.symbols")


async def register_symbols(pool: asyncpg.Pool, symbols: list[str]) -> None:
    """Make `symbols` exactly the active set (others stay, with their history, but inactive)."""
    async with pool.acquire() as conn, conn.transaction():
        await conn.executemany(
            """INSERT INTO symbols (symbol) VALUES ($1)
               ON CONFLICT (symbol) DO UPDATE SET active = true, updated_at = now()""",
            [(s,) for s in symbols],
        )
        await conn.execute(
            "UPDATE symbols SET active = false, updated_at = now() "
            "WHERE active AND symbol <> ALL($1::text[])",
            symbols,
        )


async def sync_symbol_metadata(pool: asyncpg.Pool, alpaca: AlpacaClient,
                               symbols: list[str]) -> None:
    """Company name and exchange from Alpaca; a failing symbol doesn't stop the rest."""
    for symbol in symbols:
        try:
            asset = await alpaca.asset(symbol)
        except Exception as exc:
            log.warning("Asset lookup failed for %s: %s", symbol, exc)
            continue
        await pool.execute(
            "UPDATE symbols SET name = $2, exchange = $3, updated_at = now() WHERE symbol = $1",
            symbol, asset.get("name"), asset.get("exchange"),
        )
