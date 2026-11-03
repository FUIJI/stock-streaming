"""Pick real stored articles for hand labelling; prints the case file to stdout (D3).

    dc run --rm -T api python -m src.news.export_eval --limit 30 > cases.json

Spread across what the model currently scores (1-5) so the set isn't 25 market
roundups; the model's own answers are NOT included, so they can't sway the labels.
"""

import argparse
import asyncio
import json
import os
import random
import sys

from ..backfill import parse_symbols
from ..db import create_pool

CANDIDATES_SQL = """
    SELECT n.id, n.headline, n.summary, n.symbols, n.created_at, a.importance
    FROM news n LEFT JOIN news_ai a ON a.news_id = n.id
    WHERE n.ai_status <> 'skipped' AND n.symbols && $1::text[]
    ORDER BY n.created_at DESC
    LIMIT 2000
"""


def pick(rows: list[dict], limit: int, seed: int) -> list[dict]:
    """Round-robin over the model's importance buckets (unscored last), random within each."""
    rng = random.Random(seed)
    buckets: dict[object, list[dict]] = {}
    for row in rows:
        buckets.setdefault(row["importance"], []).append(row)
    for rows_in in buckets.values():
        rng.shuffle(rows_in)
    order = sorted(buckets, key=lambda k: (k is None, -(k or 0)))
    chosen: list[dict] = []
    while len(chosen) < limit and any(buckets[k] for k in order):
        for k in order:
            if buckets[k] and len(chosen) < limit:
                chosen.append(buckets[k].pop())
    return sorted(chosen, key=lambda r: r["id"])


def to_case(row: dict, tracked: set[str]) -> dict:
    mine = [s for s in row["symbols"] if s in tracked]
    return {
        "id": row["id"],
        "created_at": row["created_at"].isoformat(),
        "headline": row["headline"],
        "summary": row["summary"],
        "tags": list(row["symbols"]),
        "tracked": mine,
        "label": {
            "importance": None,  # 1-5, see tests/news_eval/README.md
            "per_symbol": {s: {"sentiment": None, "relevant": None} for s in mine},
            "note": "",
        },
    }


async def run(limit: int, seed: int) -> dict:
    tracked = parse_symbols(os.environ.get("SYMBOLS", "AAPL,MSFT,NVDA,TSLA"))
    pool = await create_pool()
    try:
        rows = [dict(r) for r in await pool.fetch(CANDIDATES_SQL, tracked)]
    finally:
        await pool.close()
    cases = [to_case(r, set(tracked)) for r in pick(rows, limit, seed)]
    print(f"{len(cases)} cases from {len(rows)} candidates", file=sys.stderr)
    return {"cases": cases}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--seed", type=int, default=1)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run(args.limit, args.seed)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
