from datetime import UTC, datetime, timedelta

from src.migrate import migrate
from src.news.llm import LLMError
from src.news.managed import ManagedProvider, Quota
from src.news.reprocess import failing_checks, requeue, reset
from src.news.store import Article, save_articles
from src.news.worker import process_once

# A little ahead of the real clock: rows get now() from Postgres for fetched_at and
# ai_next_attempt_at, and a fixed date would turn into "the future" as time passes.
NOW = datetime.now(UTC).replace(microsecond=0) + timedelta(minutes=5)


def article(news_id, minutes_ago, symbols=("AAPL",)):
    t = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return Article.from_alpaca({"id": news_id, "headline": f"News {news_id}", "summary": "s",
                                "symbols": list(symbols), "created_at": t, "updated_at": t})


def answer(news_id, importance=4, symbol="AAPL"):
    return ('{"items": [{"id": %d, "headline_th": "หัวข่าว %d",'
            ' "category": "product", "importance": %d, "is_market_wide": false,'
            ' "per_symbol": [{"symbol": "%s", "relevance": 0.9, "sentiment": 0.3}]}]}'
            % (news_id, news_id, importance, symbol))


class Scripted:
    def __init__(self, model, *script, on_call=None):
        self.model, self.script, self.on_call = model, list(script), on_call

    async def generate_json(self, system, user, schema, max_tokens=None):
        if self.on_call:
            await self.on_call()
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def aclose(self):
        pass


def managed(*script, daily=100, on_call=None):
    return ManagedProvider([Scripted("gemma", *script, on_call=on_call)],
                           Quota(daily_limit=daily, per_minute=100, clock=lambda: NOW))


async def run(pg_pool, provider):
    return await process_once(pg_pool, provider, {"AAPL", "MSFT"}, {}, 1, "v2", now=NOW)


async def status(pg_pool, news_id):
    return dict(await pg_pool.fetchrow(
        "SELECT ai_status, ai_attempts, ai_next_attempt_at, ai_error FROM news WHERE id = $1",
        news_id))


async def test_newest_article_first_and_results_are_stored(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 30), article(2, 5, ("AAPL", "SPY"))], False)
    outcome = await run(pg_pool, managed(answer(2)))
    assert (outcome.processed, outcome.done) == (1, 1)
    ai = await pg_pool.fetchrow("SELECT * FROM news_ai WHERE news_id = 2")
    assert (ai["headline_th"], ai["importance"], ai["model"], ai["prompt_version"]) == (
        "หัวข่าว 2", 4, "gemma", "v2")
    syms = await pg_pool.fetch("SELECT symbol, relevance FROM news_symbols WHERE news_id = 2")
    assert [(r["symbol"], r["relevance"]) for r in syms] == [("AAPL", 0.9)]  # SPY not tracked
    assert (await status(pg_pool, 2))["ai_status"] == "done"
    assert (await status(pg_pool, 1))["ai_status"] == "pending"


async def test_failures_back_off_then_give_up(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 5)], False)
    overloaded = LLMError("503 overloaded", retryable=True)

    await run(pg_pool, managed(overloaded))
    s = await status(pg_pool, 1)
    assert (s["ai_status"], s["ai_attempts"]) == ("pending", 1)
    assert s["ai_next_attempt_at"] == NOW + timedelta(minutes=1)
    assert "503" in s["ai_error"]
    assert (await run(pg_pool, managed(overloaded))).processed == 0  # not due yet

    await pg_pool.execute("UPDATE news SET ai_next_attempt_at = $1", NOW)
    await run(pg_pool, managed(overloaded))
    await pg_pool.execute("UPDATE news SET ai_next_attempt_at = $1", NOW)
    await run(pg_pool, managed(overloaded))
    assert (await status(pg_pool, 1))["ai_status"] == "failed"


async def test_an_article_edited_while_in_flight_stays_queued(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 5)], False)

    async def edit_meanwhile():
        await pg_pool.execute("UPDATE news SET updated_at = updated_at + interval '1 minute'")

    await run(pg_pool, managed(answer(1), on_call=edit_meanwhile))
    assert (await status(pg_pool, 1))["ai_status"] == "pending"
    assert await pg_pool.fetchval("SELECT count(*) FROM news_ai") == 0


async def test_exhausted_quota_pauses_without_spending_attempts(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 5)], False)
    outcome = await run(pg_pool, managed(daily=0))
    assert outcome.pause_until == datetime.combine(NOW.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)
    assert (await status(pg_pool, 1))["ai_attempts"] == 0


async def test_rejected_requests_pause_the_worker_instead_of_failing_articles(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 5)], False)
    outcome = await run(pg_pool, managed(LLMError("400 API key not valid", retryable=False)))
    assert outcome.pause_until == NOW + timedelta(minutes=10)
    assert (await status(pg_pool, 1))["ai_attempts"] == 0


async def test_reprocess_requeues_by_status_and_prompt_version(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 30), article(2, 5)], False)
    await run(pg_pool, managed(answer(2)))
    await run(pg_pool, managed(answer(1)))
    await pg_pool.execute("UPDATE news_ai SET prompt_version = 'v1' WHERE news_id = 1")

    assert await reset(pg_pool, NOW - timedelta(days=1), "done", "v2") == 1  # only the v1 one
    assert (await status(pg_pool, 1))["ai_status"] == "pending"
    assert (await status(pg_pool, 2))["ai_status"] == "done"
    # The old summary stays visible until the new one replaces it.
    assert await pg_pool.fetchval("SELECT count(*) FROM news_ai WHERE news_id = 1") == 1


async def test_recheck_requeues_only_summaries_that_fail_todays_checks(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 30), article(2, 5)], False)
    await run(pg_pool, managed(answer(2)))
    await run(pg_pool, managed(answer(1)))
    # Saved before the leaked-reasoning check existed:
    await pg_pool.execute(
        "UPDATE news_ai SET summary_th = 'ปัจจัยพื้นฐานและปัจthought: [thought] {' WHERE news_id = 1")

    bad = await failing_checks(pg_pool, NOW - timedelta(days=1))
    assert bad == [1]
    assert await requeue(pg_pool, bad) == 1
    assert (await status(pg_pool, 1))["ai_status"] == "pending"
    assert (await status(pg_pool, 2))["ai_status"] == "done"


def batch_answer(*ids):
    items = ",".join(answer(i)[len('{"items": ['):-2] for i in ids)
    return '{"items": [' + items + ']}'


async def test_fresh_articles_wait_briefly_so_one_call_carries_a_batch(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 5), article(2, 3)], False)
    await pg_pool.execute("UPDATE news SET fetched_at = $1", NOW - timedelta(seconds=10))
    provider = managed(batch_answer(2, 1))
    wait = timedelta(seconds=45)

    outcome = await process_once(pg_pool, provider, {"AAPL"}, {}, 25, "t1", now=NOW,
                                 batch_wait=wait)
    assert outcome.processed == 0  # 2 < 25 and the oldest was fetched only 10s ago
    later = NOW + timedelta(seconds=40)
    outcome = await process_once(pg_pool, provider, {"AAPL"}, {}, 25, "t1", now=later,
                                 batch_wait=wait)
    assert (outcome.processed, outcome.done) == (2, 2)  # one call for both


async def test_triage_keeps_the_phase_3c_summary(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 5)], False)
    await run(pg_pool, managed(answer(1)))
    await pg_pool.execute("UPDATE news_ai SET summary_th = 'สรุปเดิม' WHERE news_id = 1")
    await reset(pg_pool, NOW - timedelta(days=1), "done", None)
    await run(pg_pool, managed(answer(1, importance=2)))
    ai = await pg_pool.fetchrow("SELECT * FROM news_ai WHERE news_id = 1")
    assert (ai["summary_th"], ai["headline_th"], ai["importance"]) == ("สรุปเดิม", "หัวข่าว 1", 2)


async def test_rate_limits_pause_the_worker_without_spending_attempts(pg_pool):
    await migrate(pg_pool)
    await save_articles(pg_pool, [article(1, 5)], False)
    limited = LLMError("Gemini 429: quota", retryable=True, rate_limited=True)
    outcome = await run(pg_pool, managed(limited))
    assert outcome.pause_until == NOW + timedelta(minutes=1)
    assert (await status(pg_pool, 1))["ai_attempts"] == 0
