"""Try the configured LLM on the newest stored articles; prints results, writes nothing.

    python -m src.news.smoke --limit 3
"""

import argparse
import asyncio
import json
import logging
import os

from ..backfill import parse_symbols
from ..db import create_pool
from .analyzer import analyze
from .llm import provider_from_env
from .store import company_names, to_item


async def run(limit: int) -> None:
    provider = provider_from_env()
    if provider is None:
        raise SystemExit("No LLM configured: set GEMINI_API_KEY (and LLM_MODEL if needed)")
    tracked = set(parse_symbols(os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,TSLA")))
    pool = await create_pool()
    try:
        rows = await pool.fetch("SELECT * FROM news ORDER BY created_at DESC LIMIT $1", limit)
        items = [to_item(r, tracked) for r in rows]
        print(f"provider={provider.name} model={provider.model} articles={len(items)}")
        result = await analyze(provider, items, await company_names(pool))
    finally:
        await pool.close()
        await provider.aclose()

    for item in items:
        print("\n==", item.id, item.headline)
        if item.id in result.done:
            final = result.done[item.id]
            print(json.dumps({**final.analysis.model_dump(exclude={"per_symbol"}),
                              "importance_after_rules": final.importance,
                              "per_symbol": [a.model_dump() for a in final.per_symbol]},
                             ensure_ascii=False, indent=2))
        else:
            print("FAILED:", result.failed.get(item.id))
    print(f"\nLLM calls: {result.calls}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=3)
    asyncio.run(run(parser.parse_args().limit))


if __name__ == "__main__":
    main()
