"""When outlook cards get (re)written: rounds at DIGEST_TIMES_ET, only for symbols whose
selected articles changed since their last card (D-Q6), retried like the news queue."""

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import asyncpg

from .digest import gather, latest_ids, save, write_outlook
from .llm import LLMError
from .managed import ManagedProvider, QuotaExhausted

log = logging.getLogger("api.news.digest")

ET = ZoneInfo("America/New_York")
MAX_ATTEMPTS = 3
BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]
RATE_LIMIT_PAUSE = timedelta(minutes=1)
CONFIG_ERROR_PAUSE = timedelta(minutes=10)


def round_times() -> list[time]:
    raw = os.environ.get("DIGEST_TIMES_ET", "08:45,12:30,16:30")
    return sorted(time(*map(int, t.strip().split(":"))) for t in raw.split(",") if t.strip())


def latest_round(now: datetime, times: list[time]) -> datetime:
    """The most recent round at or before `now` (looking back into earlier days)."""
    local = now.astimezone(ET)
    for days_back in range(0, 3):
        day = local.date() - timedelta(days=days_back)
        for t in reversed(times):
            slot = datetime.combine(day, t, tzinfo=ET)
            if slot <= local:
                return slot.astimezone(UTC)
    raise ValueError("DIGEST_TIMES_ET is empty")


async def selected_ids(pool: asyncpg.Pool, symbol: str, now: datetime) -> list[int]:
    return (await gather(pool, symbol, now)).news_ids


async def enqueue_round(pool: asyncpg.Pool, symbols: list[str], due_at: datetime) -> list[str]:
    """Queue a card for each symbol whose article set changed; returns those symbols."""
    queued = []
    for symbol in symbols:
        ids = await selected_ids(pool, symbol, due_at)
        if not ids or ids == await latest_ids(pool, symbol):
            continue
        status = await pool.execute(
            """INSERT INTO digest_jobs (scope, symbol, due_at) VALUES ('symbol', $1, $2)
               ON CONFLICT DO NOTHING""", symbol, due_at)
        if status.endswith("1"):
            queued.append(symbol)
    return queued


@dataclass
class DigestOutcome:
    processed: int = 0
    pause_until: datetime | None = None


async def _finish(pool, job_id: int, status: str, error: str | None = None) -> None:
    await pool.execute("UPDATE digest_jobs SET status = $2, error = $3 WHERE id = $1",
                       job_id, status, error)


async def process_digest_once(pool: asyncpg.Pool, provider: ManagedProvider,
                              names: dict[str, str], now: datetime | None = None
                              ) -> DigestOutcome:
    """Write at most one card. The article set is re-read now, not taken from the round."""
    now = now or datetime.now(UTC)
    job = await pool.fetchrow(
        """SELECT * FROM digest_jobs WHERE status = 'pending' AND next_attempt_at <= $1
           ORDER BY due_at, id LIMIT 1""", now)
    if job is None:
        return DigestOutcome()
    symbol = job["symbol"]
    inputs = await gather(pool, symbol, now)
    if not inputs.articles or inputs.news_ids == await latest_ids(pool, symbol):
        await _finish(pool, job["id"], "skipped")
        return DigestOutcome(processed=1)
    try:
        card, model = await write_outlook(provider, inputs, names.get(symbol, symbol))
    except QuotaExhausted as exc:
        return DigestOutcome(pause_until=exc.resets_at)
    except LLMError as exc:
        if exc.rate_limited:
            return DigestOutcome(pause_until=now + RATE_LIMIT_PAUSE)
        if not exc.retryable:
            log.error("LLM rejects outlook requests (%s); pausing", exc)
            return DigestOutcome(pause_until=now + CONFIG_ERROR_PAUSE)
        attempts = job["attempts"] + 1
        done = attempts >= MAX_ATTEMPTS
        await pool.execute(
            """UPDATE digest_jobs SET attempts = $2, status = $3, next_attempt_at = $4,
                                      error = $5 WHERE id = $1""",
            job["id"], attempts, "failed" if done else "pending",
            now if done else now + BACKOFF[attempts - 1], str(exc)[:500])
        log.warning("Outlook for %s failed (attempt %d): %s", symbol, attempts, exc)
        return DigestOutcome(processed=1)
    await save(pool, inputs, card, model)
    await _finish(pool, job["id"], "done")
    log.info("Outlook for %s: %s (%s)", symbol, card.outlook, card.confidence)
    return DigestOutcome(processed=1)
