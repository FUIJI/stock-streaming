"""Background task: run pending articles through the LLM and store the results.

Queue = rows of `news` with ai_status 'pending' whose ai_next_attempt_at has come,
newest first (fresh news matters most; reprocessed old articles naturally go last).
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

from .analysis import PROMPT_VERSION, FinalAnalysis
from .analyzer import analyze
from .digest_jobs import enqueue_round, latest_round, process_digest_once, round_times
from .llm import LLMError
from .managed import ManagedProvider, QuotaExhausted
from .notify import ALERT_IMPORTANCE, Alert, Notifier, notifier_from_env
from .store import company_names, to_item

log = logging.getLogger("api.news.worker")

MAX_ATTEMPTS = 3
BACKOFF = [timedelta(minutes=1), timedelta(minutes=5), timedelta(minutes=30)]
# Bad key / bad request: every article would fail the same way. Wait, don't burn attempts.
CONFIG_ERROR_PAUSE = timedelta(minutes=10)
# 429: over Google's per-minute limits (e.g. 16K tokens/min on Gemma). Same idea, shorter.
RATE_LIMIT_PAUSE = timedelta(minutes=1)


@dataclass
class LLMStats:
    provider: str = ""
    models: str = ""
    pending: int = 0
    failed: int = 0
    done: int = 0
    skipped: int = 0
    last_success_at: float | None = None
    last_error: str | None = None
    paused_until: datetime | None = None
    outlooks_queued: int = 0  # outlook cards waiting to be written (digest_jobs)
    triage_calls: int = 0  # LLM calls since the API started, by kind
    digest_calls: int = 0


@dataclass
class Outcome:
    processed: int  # articles taken off the queue this round (done or failed attempt)
    done: int = 0  # of which stored successfully
    pause_until: datetime | None = None  # the whole worker should wait until then


# Fresh articles wait up to LLM_TRIAGE_WAIT_SEC (by fetched_at) so that a call carries
# a batch instead of one article each. Backfills and re-queued old articles go at once.
QUEUE_SQL = """
    SELECT * FROM news
    WHERE ai_status = 'pending' AND ai_next_attempt_at <= $1
    ORDER BY created_at DESC
    LIMIT $2
"""


async def _store(conn: asyncpg.Connection, row, final: FinalAnalysis, prompt_version: str) -> bool:
    """Save one result; False if the article was edited meanwhile (it stays queued)."""
    async with conn.transaction():
        status = await conn.execute(
            """UPDATE news SET ai_status = 'done', ai_attempts = ai_attempts + 1, ai_error = NULL
               WHERE id = $1 AND updated_at = $2 AND ai_status = 'pending'""",
            row["id"], row["updated_at"])
        if status != "UPDATE 1":
            return False
        a = final.analysis
        await conn.execute(
            """INSERT INTO news_ai (news_id, headline_th, category, importance, importance_raw,
                                    is_market_wide, model, prompt_version)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
               ON CONFLICT (news_id) DO UPDATE SET
                   headline_th = EXCLUDED.headline_th,
                   category = EXCLUDED.category, importance = EXCLUDED.importance,
                   importance_raw = EXCLUDED.importance_raw,
                   is_market_wide = EXCLUDED.is_market_wide, model = EXCLUDED.model,
                   prompt_version = EXCLUDED.prompt_version, processed_at = now()""",
            # summary_th / why_it_matters (Phase 3c) are left as they were
            row["id"], a.headline_th, a.category, final.importance, a.importance,
            a.is_market_wide, final.model, prompt_version)
        await conn.execute("DELETE FROM news_symbols WHERE news_id = $1", row["id"])
        await conn.executemany(
            "INSERT INTO news_symbols (news_id, symbol, relevance, sentiment) VALUES ($1, $2, $3, $4)",
            [(row["id"], s.symbol, s.relevance, s.sentiment) for s in final.per_symbol])
        return True


async def _record_failure(pool: asyncpg.Pool, row, error: LLMError, now: datetime) -> None:
    attempts = row["ai_attempts"] + 1
    if attempts >= MAX_ATTEMPTS:
        status, next_at = "failed", now
    else:
        status, next_at = "pending", now + BACKOFF[attempts - 1]
    await pool.execute(
        """UPDATE news SET ai_status = $3, ai_attempts = $4, ai_next_attempt_at = $5, ai_error = $6
           WHERE id = $1 AND updated_at = $2 AND ai_status = 'pending'""",
        row["id"], row["updated_at"], status, attempts, next_at, str(error)[:500])


async def process_once(pool: asyncpg.Pool, provider: ManagedProvider, tracked: set[str],
                       names: dict[str, str], batch_size: int, prompt_version: str,
                       now: datetime | None = None,
                       batch_wait: timedelta = timedelta(0),
                       notifier: Notifier | None = None) -> Outcome:
    now = now or datetime.now(UTC)
    rows = await pool.fetch(QUEUE_SQL, now, batch_size)
    if not rows:
        return Outcome(processed=0)
    if (batch_wait and len(rows) < batch_size
            and min(r["fetched_at"] for r in rows) > now - batch_wait):
        return Outcome(processed=0)  # let the batch fill up a little longer

    result = await analyze(provider, [to_item(r, tracked) for r in rows], names)
    errors = list(result.failed.values())
    if errors and all(isinstance(e, QuotaExhausted) for e in errors) and not result.done:
        return Outcome(processed=0, pause_until=errors[0].resets_at)  # no attempt spent
    if errors and all(getattr(e, "rate_limited", False) for e in errors) and not result.done:
        log.warning("LLM rate limited (%s); pausing %s", errors[0], RATE_LIMIT_PAUSE)
        return Outcome(processed=0, pause_until=now + RATE_LIMIT_PAUSE)
    if errors and all(not e.retryable for e in errors) and not result.done:
        log.error("LLM rejects requests (%s); pausing %s", errors[0], CONFIG_ERROR_PAUSE)
        return Outcome(processed=0, pause_until=now + CONFIG_ERROR_PAUSE)

    stored = 0
    alerts: list[Alert] = []
    # Alert once per article: one that already had a result was alerted (or not) back then.
    scored_before = {r["news_id"] for r in await pool.fetch(
        "SELECT news_id FROM news_ai WHERE news_id = ANY($1::bigint[])", [r["id"] for r in rows])}
    async with pool.acquire() as conn:
        for row in rows:
            final = result.done.get(row["id"])
            if final is None or not await _store(conn, row, final, prompt_version):
                continue
            stored += 1
            if (final.importance >= ALERT_IMPORTANCE and not row["backfilled"]
                    and row["id"] not in scored_before):
                alerts.append(Alert(
                    news_id=row["id"], importance=final.importance,
                    symbols=[a.symbol for a in final.per_symbol], headline=row["headline"],
                    headline_th=final.analysis.headline_th, url=row["url"]))
    for alert in alerts:
        try:
            await (notifier or notifier_from_env()).notify(alert)
        except Exception:
            log.exception("Notifier failed for %s", alert.news_id)
    for row in rows:
        if row["id"] in result.failed:
            await _record_failure(pool, row, result.failed[row["id"]], now)
    return Outcome(processed=len(rows), done=stored)


async def refresh_counts(pool: asyncpg.Pool, stats: LLMStats) -> None:
    counts = {r["ai_status"]: r["n"] for r in await pool.fetch(
        "SELECT ai_status, count(*) AS n FROM news GROUP BY ai_status")}
    stats.pending, stats.failed, stats.done, stats.skipped = (
        counts.get("pending", 0), counts.get("failed", 0), counts.get("done", 0),
        counts.get("skipped", 0))


async def run_worker(pool: asyncpg.Pool, provider: ManagedProvider, tracked: set[str],
                     stats: LLMStats) -> None:
    """Never raises: errors are logged and reported through `stats` (/health)."""
    batch_size = int(os.environ.get("LLM_TRIAGE_BATCH", "25"))
    batch_wait = timedelta(seconds=float(os.environ.get("LLM_TRIAGE_WAIT_SEC", "45")))
    idle = float(os.environ.get("NEWS_WORKER_IDLE_SEC", "15"))
    stats.provider = "gemini"
    stats.models = " -> ".join(p.model for p in provider.providers)
    names: dict[str, str] = {}
    names_at = 0.0
    try:
        await refresh_counts(pool, stats)  # /health shows the real backlog from the start
    except Exception:
        log.exception("Could not count the news queue")
    times = round_times()
    # Calls kept for outlook cards: triage stops this short of the daily cap (D-Q10).
    reserve = min(int(os.environ.get("LLM_DIGEST_RESERVE", "60")),
                  provider.quota.daily_limit // 2)  # a low daily cap still triages
    last_round: datetime | None = None
    while True:
        try:
            if time.monotonic() - names_at > 3600:
                names, names_at = await company_names(pool), time.monotonic()
            now = datetime.now(UTC)
            due = latest_round(now, times)
            if due != last_round:
                queued = await enqueue_round(pool, sorted(tracked), due)
                if queued:
                    log.info("Outlook round %s: %s", due.isoformat(), ", ".join(queued))
                last_round = due

            before = provider.quota.calls_today
            if provider.quota.remaining() > reserve:
                outcome = await process_once(pool, provider, tracked, names, batch_size,
                                             PROMPT_VERSION, batch_wait=batch_wait)
            else:
                outcome = Outcome(processed=0)  # what's left today is for outlook cards
            stats.triage_calls += max(0, provider.quota.calls_today - before)
            if not outcome.processed and not outcome.pause_until:
                before = provider.quota.calls_today
                card = await process_digest_once(pool, provider, names)
                stats.digest_calls += max(0, provider.quota.calls_today - before)
                outcome = Outcome(processed=card.processed, pause_until=card.pause_until)

            stats.last_error = provider.last_error
            if outcome.done:
                stats.last_success_at = time.time()
            await refresh_counts(pool, stats)
            stats.outlooks_queued = await pool.fetchval(
                "SELECT count(*) FROM digest_jobs WHERE status = 'pending'")
            stats.paused_until = outcome.pause_until
            if outcome.pause_until:
                await asyncio.sleep(max(0.0, (outcome.pause_until - datetime.now(UTC))
                                        .total_seconds()))
            elif not outcome.processed:
                await asyncio.sleep(idle)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            stats.last_error = str(exc)[:300]
            log.exception("News worker round failed")
            await asyncio.sleep(idle)
