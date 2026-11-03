"""Send stored articles back through the LLM (e.g. after changing the prompt or model).

    python -m src.news.reprocess --since 2026-11-01 [--status done|failed|all]
                                 [--older-prompt v2]

Only resets queue state: existing summaries stay visible until replaced. The worker
handles newest articles first, so reprocessed old ones never delay fresh news.
"""

import argparse
import asyncio
from datetime import UTC, datetime

import asyncpg

from ..db import create_pool
from ..migrate import migrate

RESET_SQL = """
    UPDATE news SET ai_status = 'pending', ai_attempts = 0,
                    ai_next_attempt_at = now(), ai_error = NULL
    WHERE created_at >= $1
      AND ($2::text = 'all' OR ai_status = $2)
      AND ($3::text IS NULL OR NOT EXISTS (
            SELECT 1 FROM news_ai a WHERE a.news_id = news.id AND a.prompt_version = $3))
"""


async def reset(pool: asyncpg.Pool, since: datetime, status: str,
                older_than_prompt: str | None) -> int:
    result = await pool.execute(RESET_SQL, since, status, older_than_prompt)
    return int(result.split()[-1])


async def _run(args) -> None:
    since = datetime.fromisoformat(args.since)
    since = since if since.tzinfo else since.replace(tzinfo=UTC)
    pool = await create_pool()
    try:
        await migrate(pool)
        n = await reset(pool, since, args.status, args.older_prompt)
    finally:
        await pool.close()
    print(f"{n} articles queued for the LLM again")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", required=True, help="ISO date/time, e.g. 2026-11-01")
    parser.add_argument("--status", default="all", choices=["done", "failed", "all"])
    parser.add_argument("--older-prompt", default=None, metavar="VERSION",
                        help="only articles not yet processed with this prompt version")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
