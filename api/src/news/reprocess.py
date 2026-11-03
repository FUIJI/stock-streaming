"""Send stored articles back through the LLM (e.g. after changing the prompt or model).

    python -m src.news.reprocess --since 2026-11-01 [--status done|failed|all]
                                 [--older-prompt v2] [--recheck]

--recheck: only articles whose stored summary fails today's output checks
(e.g. garbled text saved before a check existed).

Only resets queue state: existing summaries stay visible until replaced. The worker
handles newest articles first, so reprocessed old ones never delay fresh news.
"""

import argparse
import asyncio
from datetime import UTC, datetime

import asyncpg

from ..db import create_pool
from ..migrate import migrate
from .analysis import degeneration

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


async def failing_checks(pool: asyncpg.Pool, since: datetime) -> list[int]:
    """Articles whose stored AI text would be rejected by the current checks."""
    rows = await pool.fetch(
        """SELECT a.news_id, a.summary_th, a.why_it_matters FROM news_ai a
           JOIN news n ON n.id = a.news_id WHERE n.created_at >= $1""", since)
    return [r["news_id"] for r in rows
            if degeneration(r["summary_th"]) or degeneration(r["why_it_matters"])]


async def requeue(pool: asyncpg.Pool, ids: list[int]) -> int:
    result = await pool.execute(
        """UPDATE news SET ai_status = 'pending', ai_attempts = 0,
                          ai_next_attempt_at = now(), ai_error = NULL
           WHERE id = ANY($1::bigint[])""", ids)
    return int(result.split()[-1])


async def _run(args) -> None:
    since = datetime.fromisoformat(args.since)
    since = since if since.tzinfo else since.replace(tzinfo=UTC)
    pool = await create_pool()
    try:
        await migrate(pool)
        if args.recheck:
            n = await requeue(pool, await failing_checks(pool, since))
        else:
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
    parser.add_argument("--recheck", action="store_true",
                        help="only articles whose stored summary fails the current checks")
    asyncio.run(_run(parser.parse_args()))


if __name__ == "__main__":
    main()
