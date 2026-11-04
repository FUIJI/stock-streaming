"""D11: judging outlook cards 3 trading sessions later."""

from datetime import UTC, datetime, timedelta

import pytest

from src.migrate import migrate
from src.news.accuracy import direction, evaluate_due, judge, prior_close, report, target_close


def close(day: int) -> datetime:
    """21:00 UTC = 16:00 New York (EST) on 2026-11-<day>."""
    return datetime(2026, 11, day, 21, 0, tzinfo=UTC)


# Mon 2 ... Fri 6, then Mon 9 ... ; Wed 11 is a holiday in this calendar.
CLOSES = [close(d) for d in (2, 3, 4, 5, 6, 9, 10, 12, 13)]


@pytest.mark.parametrize("pct, outlook", [(1.5, "positive"), (-1.5, "negative"),
                                          (0.4, "mixed"), (-1.0, "mixed"), (1.0, "mixed")])
def test_direction_uses_a_flat_band(pct, outlook):
    assert direction(pct, 1.0) == outlook
    assert judge(outlook, pct, 1.0)


def test_three_sessions_after_the_card_skipping_weekends_and_holidays():
    midday_mon = datetime(2026, 11, 2, 17, 30, tzinfo=UTC)
    assert target_close(midday_mon, CLOSES) == close(4)  # Mon, Tue, Wed closes
    after_close_mon = datetime(2026, 11, 2, 21, 30, tzinfo=UTC)
    assert target_close(after_close_mon, CLOSES) == close(5)
    friday = datetime(2026, 11, 6, 17, 0, tzinfo=UTC)
    assert target_close(friday, CLOSES) == close(10)  # Fri, Mon, Tue
    tuesday = datetime(2026, 11, 10, 17, 0, tzinfo=UTC)
    assert target_close(tuesday, CLOSES) == close(13)  # Tue, (Wed holiday) Thu, Fri
    assert target_close(close(13), CLOSES) is None  # calendar doesn't reach that far
    assert prior_close(datetime(2026, 11, 5, 17, 0, tzinfo=UTC), CLOSES) == close(2)


async def card(pool, created_at, outlook, price, confidence="medium", symbol="NVDA"):
    return await pool.fetchval(
        """INSERT INTO news_digest (scope, symbol, period_start, period_end, title_th, bullets,
                                    tone, news_ids, model, prompt_version, confidence,
                                    price_at_create, created_at)
           VALUES ('symbol', $1, $2, $2, 't', '[]', $3, '{}', 'gemma', 'd2', $4, $5, $2)
           RETURNING id""", symbol, created_at, outlook, confidence, price)


async def candle(pool, at, price, symbol="NVDA"):
    await pool.execute("INSERT INTO ohlcv_1m (symbol, bucket_start, open, high, low, close)"
                       " VALUES ($1, $2, $3, $3, $3, $3)", symbol, at, price)


async def test_cards_are_judged_once_their_sessions_are_over(pg_pool):
    await migrate(pg_pool)
    thu_noon = datetime(2026, 11, 5, 17, 0, tzinfo=UTC)
    right = await card(pg_pool, thu_noon, "positive", 100.0)
    wrong = await card(pg_pool, thu_noon, "negative", 100.0, confidence="high")
    later = await card(pg_pool, datetime(2026, 11, 9, 17, 0, tzinfo=UTC), "mixed", 100.0)
    # Price 3 sessions before the card (Mon close) and at the target (Thu, Fri, Mon -> Mon 9)
    await candle(pg_pool, close(2) - timedelta(minutes=1), 95.0)
    await candle(pg_pool, close(9) - timedelta(minutes=1), 103.0)
    await candle(pg_pool, close(9) + timedelta(minutes=30), 90.0)  # after hours: ignored

    now = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)  # Monday the 9th's close has passed
    assert await evaluate_due(pg_pool, CLOSES, now) == 2
    rows = {r["id"]: r for r in await pg_pool.fetch("SELECT * FROM news_digest")}
    assert (rows[right]["hit"], rows[right]["outcome_pct"]) == (True, 3.0)
    assert rows[wrong]["hit"] is False
    assert rows[right]["outcome_at"] == close(9)
    # Baseline: Mon 95 -> Thu 100 = +5.26% "positive", and +3% followed: right
    assert rows[right]["baseline_pct"] == 5.26 and rows[right]["baseline_hit"] is True
    assert rows[later]["hit"] is None  # its sessions end on the 12th

    assert await evaluate_due(pg_pool, CLOSES, now) == 0  # judged cards stay judged
    by_conf = {r["grp"]: r for r in (await report(pg_pool))["confidence"]}
    assert (by_conf["high"]["hits"], by_conf["high"]["n"]) == (0, 1)


async def test_missing_prices_wait_then_give_up(pg_pool):
    await migrate(pg_pool)
    card_id = await card(pg_pool, datetime(2026, 11, 2, 17, 0, tzinfo=UTC), "mixed", 100.0)
    soon = close(4) + timedelta(days=1)
    assert await evaluate_due(pg_pool, CLOSES, soon) == 0
    assert await pg_pool.fetchval("SELECT outcome_at FROM news_digest") is None  # will retry
    await evaluate_due(pg_pool, CLOSES, close(4) + timedelta(days=15))
    row = await pg_pool.fetchrow("SELECT hit, outcome_at FROM news_digest WHERE id = $1",
                                 card_id)
    assert row["hit"] is None and row["outcome_at"] is not None  # given up, not counted
