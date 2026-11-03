"""Try the configured LLM on the newest stored articles; prints results, writes nothing.

    python -m src.news.smoke --limit 25     # one triage batch, like the worker sends
"""

import argparse
import asyncio
import json
import logging
import os
import time

from ..backfill import parse_symbols
from ..db import create_pool
from .analyzer import analyze
from .managed import managed_provider_from_env
from .store import company_names, to_item


async def run(limit: int) -> None:
    provider = managed_provider_from_env()
    if provider is None:
        raise SystemExit("No LLM configured: set GEMINI_API_KEY (and LLM_MODEL if needed)")
    tracked = set(parse_symbols(os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,TSLA")))
    pool = await create_pool()
    try:
        rows = await pool.fetch(
            "SELECT * FROM news WHERE ai_status <> 'skipped' ORDER BY created_at DESC LIMIT $1",
            limit)
        items = [to_item(r, tracked) for r in rows]
        models = " -> ".join(p.model for p in provider.providers)
        print(f"models={models} articles={len(items)}")
        names = await company_names(pool)
        started = time.monotonic()
        result = await analyze(provider, items, names)
        elapsed = time.monotonic() - started
    finally:
        await pool.close()
        await provider.aclose()

    for item in items:
        print("\n==", item.id, item.headline)
        if item.id in result.done:
            final = result.done[item.id]
            print(json.dumps({**final.analysis.model_dump(exclude={"per_symbol"}),
                              "model": final.model,
                              "importance_after_rules": final.importance,
                              "per_symbol": [a.model_dump() for a in final.per_symbol]},
                             ensure_ascii=False, indent=2))
        else:
            print("FAILED:", result.failed.get(item.id))
    print(f"\n{len(result.done)}/{len(items)} triaged · LLM calls: {result.calls} · "
          f"batch splits: {result.splits} · tokens in/out: {result.prompt_tokens}/"
          f"{result.output_tokens} · {elapsed:.1f}s · quota used today: "
          f"{provider.quota.calls_today}")


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=25)
    asyncio.run(run(parser.parse_args().limit))


if __name__ == "__main__":
    main()
