"""D4: the per-symbol 3-day outlook card."""

import json
from datetime import UTC, datetime, time, timedelta

import pytest

from src.migrate import migrate
from src.news.digest import count_tones, gather, latest_ids, parse, save
from src.news.digest_jobs import enqueue_round, latest_round, process_digest_once
from src.news.llm import LLMError
from src.news.managed import ManagedProvider, Quota
from src.news.store import Article, save_articles

NOW = datetime.now(UTC).replace(second=0, microsecond=0) + timedelta(minutes=5)


async def article(pool, news_id, hours_ago, importance=4, relevance=0.9, sentiment=0.5,
                  symbol="NVDA", status="done"):
    t = (NOW - timedelta(hours=hours_ago)).isoformat()
    await save_articles(pool, [Article.from_alpaca(
        {"id": news_id, "headline": f"News {news_id}", "summary": "s", "symbols": [symbol],
         "created_at": t, "updated_at": t})], False)
    await pool.execute(
        "INSERT INTO news_ai (news_id, headline_th, category, importance, importance_raw,"
        " is_market_wide, model, prompt_version) VALUES ($1, 'หัวข่าว', 'analyst', $2, $2,"
        " false, 'gemma', 't3')", news_id, importance)
    await pool.execute("INSERT INTO news_symbols VALUES ($1, $2, $3, $4)",
                       news_id, symbol, relevance, sentiment)
    await pool.execute("UPDATE news SET ai_status = $2 WHERE id = $1", news_id, status)


def card(*ids, outlook="positive", **extra):
    return json.dumps({"title_th": "ข่าวดีหนุนราคา", "summary_th": "แรงซื้อเพิ่มจากข่าวดี",
                       "bull": [{"text": "โบรกปรับเป้าขึ้น", "news_ids": list(ids)}], "bear": [],
                       "outlook": outlook, "confidence": "medium", **extra})


class Scripted:
    def __init__(self, *script):
        self.model, self.script = "gemma", list(script)

    async def generate_json(self, system, user, schema, max_tokens=None):
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def aclose(self):
        pass


def managed(*script):
    return ManagedProvider([Scripted(*script)],
                           Quota(daily_limit=100, per_minute=100, clock=lambda: NOW))


# --- selection and numbers -----------------------------------------------------------

async def test_selection_window_filters_and_cap(pg_pool):
    await migrate(pg_pool)
    await article(pg_pool, 1, hours_ago=80)                   # older than 72h
    await article(pg_pool, 2, hours_ago=5, relevance=0.3)     # incidental
    await article(pg_pool, 3, hours_ago=5, importance=2)      # unimportant
    await article(pg_pool, 4, hours_ago=5, status="skipped")  # rules said no
    await article(pg_pool, 5, hours_ago=5, symbol="AAPL")     # another stock
    for i in range(10, 22):                                   # 12 candidates
        await article(pg_pool, i, hours_ago=i - 9, importance=5 if i == 21 else 3)

    inputs = await gather(pg_pool, "NVDA", NOW)
    ids = [a["id"] for a in inputs.articles]
    assert len(ids) == 10 and ids[0] == 21  # highest importance first, even though oldest
    assert ids[1:] == [10, 11, 12, 13, 14, 15, 16, 17, 18]  # then newest first
    assert inputs.news_ids == sorted(ids)


async def test_counts_and_price_move_are_computed_in_code(pg_pool):
    await migrate(pg_pool)
    await article(pg_pool, 1, hours_ago=10, sentiment=0.6)
    await article(pg_pool, 2, hours_ago=5, sentiment=-0.4)
    await article(pg_pool, 3, hours_ago=2, sentiment=0.1)
    first = NOW - timedelta(hours=10)
    await pg_pool.executemany(
        "INSERT INTO ohlcv_1m (symbol, bucket_start, open, high, low, close) "
        "VALUES ('NVDA', $1, $2, $2, $2, $2)",
        [(first - timedelta(minutes=1), 200.0), (first + timedelta(hours=1), 210.0),
         (NOW - timedelta(minutes=10), 190.0)])
    inputs = await gather(pg_pool, "NVDA", NOW)
    assert inputs.counts == {"positive": 1, "negative": 1, "neutral": 1}
    assert (inputs.price_change_pct, inputs.price_now) == (-5.0, 190.0)
    assert count_tones([]) == {"positive": 0, "negative": 0, "neutral": 0}


# --- what the model writes -------------------------------------------------------------

class _Inputs:
    news_ids = [1, 2]


def test_a_valid_card_is_accepted():
    parsed = parse(card(1, 2), _Inputs())
    assert parsed.outlook == "positive" and parsed.cited() == {1, 2}


@pytest.mark.parametrize("raw", [
    card(1, 99),                                   # cites an article we never sent
    card(1, outlook="up"),                         # not one of the 3 levels
    card(1, summary_th="แรงซื้อ" + "ๆ" * 400),         # too long
    card(1, title_th="ความเสan์เสีย"),               # garbled Thai
    card(1, bear=[{"text": "x", "news_ids": []}]),  # a factor without a source
    "{not json",
])
def test_unusable_cards_are_bad_output(raw):
    with pytest.raises(LLMError) as raised:
        parse(raw, _Inputs())
    assert raised.value.bad_output


# --- rounds and the job queue ---------------------------------------------------------

def test_latest_round_is_in_new_york_time():
    times = [time(8, 45), time(12, 30), time(16, 30)]
    # 14:00 UTC on a November day = 09:00 New York (EST) -> the 08:45 round
    assert latest_round(datetime(2026, 11, 3, 14, 0, tzinfo=UTC), times) == datetime(
        2026, 11, 3, 13, 45, tzinfo=UTC)
    # 12:00 UTC = 07:00 New York -> yesterday's 16:30 round
    assert latest_round(datetime(2026, 11, 3, 12, 0, tzinfo=UTC), times) == datetime(
        2026, 11, 2, 21, 30, tzinfo=UTC)


async def test_a_round_queues_only_symbols_whose_news_changed(pg_pool):
    await migrate(pg_pool)
    await article(pg_pool, 1, hours_ago=3)
    assert await enqueue_round(pg_pool, ["NVDA", "AAPL"], NOW) == ["NVDA"]  # AAPL: no news
    assert await enqueue_round(pg_pool, ["NVDA"], NOW) == []  # same round twice: one job

    await process_digest_once(pg_pool, managed(card(1)), {}, now=NOW)
    assert await latest_ids(pg_pool, "NVDA") == [1]
    later = NOW + timedelta(hours=4)
    assert await enqueue_round(pg_pool, ["NVDA"], later) == []  # nothing new: no card, no call
    await article(pg_pool, 2, hours_ago=1)
    assert await enqueue_round(pg_pool, ["NVDA"], later) == ["NVDA"]


async def test_a_card_is_written_and_stored(pg_pool):
    await migrate(pg_pool)
    await article(pg_pool, 1, hours_ago=3, sentiment=0.7)
    await article(pg_pool, 2, hours_ago=2, sentiment=-0.7)
    await enqueue_round(pg_pool, ["NVDA"], NOW)
    reply = card(1, bear=[{"text": "ต้นทุนเพิ่ม", "news_ids": [2]}], outlook="mixed")
    assert (await process_digest_once(pg_pool, managed(reply), {}, now=NOW)).processed == 1

    row = await pg_pool.fetchrow("SELECT * FROM news_digest")
    assert (row["symbol"], row["tone"], row["confidence"], row["prompt_version"]) == (
        "NVDA", "mixed", "medium", "d1")
    assert sorted(row["news_ids"]) == [1, 2]
    assert json.loads(row["counts"]) == {"positive": 1, "negative": 1, "neutral": 0}
    assert [b["side"] for b in json.loads(row["bullets"])] == ["bull", "bear"]
    assert await pg_pool.fetchval("SELECT status FROM digest_jobs") == "done"


async def test_bad_cards_back_off_and_rate_limits_pause(pg_pool):
    await migrate(pg_pool)
    await article(pg_pool, 1, hours_ago=3)
    await enqueue_round(pg_pool, ["NVDA"], NOW)

    await process_digest_once(pg_pool, managed(card(99)), {}, now=NOW)  # cites unknown id
    job = await pg_pool.fetchrow("SELECT * FROM digest_jobs")
    assert (job["status"], job["attempts"]) == ("pending", 1)
    assert job["next_attempt_at"] == NOW + timedelta(minutes=1) and "99" in job["error"]

    limited = LLMError("Gemini 429", retryable=True, rate_limited=True)
    later = NOW + timedelta(minutes=2)
    outcome = await process_digest_once(pg_pool, managed(limited), {}, now=later)
    assert outcome.pause_until == later + timedelta(minutes=1)
    assert await pg_pool.fetchval("SELECT attempts FROM digest_jobs") == 1  # not spent


async def test_a_job_whose_news_did_not_change_is_skipped_without_a_call(pg_pool):
    await migrate(pg_pool)
    await article(pg_pool, 1, hours_ago=3)
    inputs = await gather(pg_pool, "NVDA", NOW)
    await save(pg_pool, inputs, parse(card(1), inputs), "gemma")  # e.g. written by the CLI
    await pg_pool.execute(
        "INSERT INTO digest_jobs (scope, symbol, due_at) VALUES ('symbol', 'NVDA', $1)", NOW)
    await process_digest_once(pg_pool, managed(), {}, now=NOW)  # no script: must not call
    assert await pg_pool.fetchval("SELECT status FROM digest_jobs") == "skipped"
