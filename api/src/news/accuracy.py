"""Did the outlook come true? (Phase 3d, D11)

    python -m src.news.accuracy           # evaluate what's due, then print the report

A card is judged at the close of the 3rd regular session that ends after it was written
(holidays don't count: Alpaca's calendar), against the price when it was written:
positive is right above +T%, negative below -T%, mixed within ±T% (T = OUTLOOK_FLAT_PCT).
The baseline guesses that the 3 sessions before the card keep their direction; the AI is
only useful if it beats that.
"""

import logging
import os
from datetime import UTC, datetime, timedelta

import asyncpg

from ..alpaca import AlpacaClient
from ..market import parse_calendar
from .digest import pct

log = logging.getLogger("api.news.accuracy")

SESSIONS = 3
GIVE_UP_AFTER = timedelta(days=14)  # no price for the target close by then: stop trying


def flat_pct() -> float:
    return float(os.environ.get("OUTLOOK_FLAT_PCT", "1.0"))


def direction(change_pct: float, flat: float) -> str:
    if change_pct > flat:
        return "positive"
    if change_pct < -flat:
        return "negative"
    return "mixed"


def judge(outlook: str, change_pct: float, flat: float) -> bool:
    return direction(change_pct, flat) == outlook


def target_close(created_at: datetime, closes: list[datetime], n: int = SESSIONS
                 ) -> datetime | None:
    """The n-th regular-session close strictly after `created_at`."""
    after = [c for c in sorted(closes) if c > created_at]
    return after[n - 1] if len(after) >= n else None


def prior_close(created_at: datetime, closes: list[datetime], n: int = SESSIONS
                ) -> datetime | None:
    """The n-th regular-session close at or before `created_at`, counting back."""
    before = [c for c in sorted(closes) if c <= created_at]
    return before[-n] if len(before) >= n else None


PRICE_AT_SQL = """
    SELECT close FROM ohlcv_1m
    WHERE symbol = $1 AND bucket_start < $2 AND bucket_start >= $2 - interval '8 hours'
    ORDER BY bucket_start DESC LIMIT 1
"""


async def price_at_close(pool: asyncpg.Pool, symbol: str, close: datetime) -> float | None:
    """Last regular-session price of the day that closes at `close`."""
    return await pool.fetchval(PRICE_AT_SQL, symbol, close)


async def session_closes(alpaca: AlpacaClient, start: datetime, end: datetime) -> list[datetime]:
    rows = await alpaca.calendar(start.date().isoformat(), end.date().isoformat())
    return [day.close for day in parse_calendar(rows)]


async def evaluate_due(pool: asyncpg.Pool, closes: list[datetime],
                       now: datetime | None = None) -> int:
    """Fill outcome / hit / baseline for every card whose target close has passed."""
    now = now or datetime.now(UTC)
    flat = flat_pct()
    cards = await pool.fetch(
        """SELECT id, symbol, created_at, tone, price_at_create FROM news_digest
           WHERE scope = 'symbol' AND hit IS NULL AND outcome_at IS NULL""")
    judged = 0
    for card in cards:
        target = target_close(card["created_at"], closes)
        if target is None or target > now:
            continue  # its 3 sessions aren't over yet
        outcome = await price_at_close(pool, card["symbol"], target)
        start = card["price_at_create"]
        if outcome is None or start is None:
            if now - target > GIVE_UP_AFTER:  # no price will come any more: stop trying
                await pool.execute("UPDATE news_digest SET outcome_at = $2 WHERE id = $1",
                                   card["id"], now)
            continue
        change = pct(start, outcome)
        baseline_pct = baseline_hit = None
        prior = prior_close(card["created_at"], closes)
        if prior is not None:
            before = await price_at_close(pool, card["symbol"], prior)
            baseline_pct = pct(before, start)
            if baseline_pct is not None:
                baseline_hit = judge(direction(baseline_pct, flat), change, flat)
        await pool.execute(
            """UPDATE news_digest SET outcome_close = $2, outcome_pct = $3, outcome_at = $4,
                                      hit = $5, baseline_pct = $6, baseline_hit = $7
               WHERE id = $1""",
            card["id"], outcome, change, target, judge(card["tone"], change, flat),
            baseline_pct, baseline_hit)
        judged += 1
    return judged


async def evaluate_outlooks(pool: asyncpg.Pool, alpaca: AlpacaClient,
                            now: datetime | None = None) -> int:
    """The scheduled job: the calendar from the oldest unjudged card to today."""
    now = now or datetime.now(UTC)
    oldest = await pool.fetchval(
        """SELECT min(created_at) FROM news_digest
           WHERE scope = 'symbol' AND hit IS NULL AND outcome_at IS NULL""")
    if oldest is None:
        return 0
    # A week before the oldest card too, for the baseline's 3 sessions back.
    closes = await session_closes(alpaca, oldest - timedelta(days=7), now)
    judged = await evaluate_due(pool, closes, now)
    if judged:
        log.info("Judged %d outlook cards", judged)
    return judged


REPORT_SQL = """
    SELECT {group} AS grp, count(*) AS n, count(*) FILTER (WHERE hit) AS hits,
           count(baseline_hit) AS bn, count(*) FILTER (WHERE baseline_hit) AS bhits
    FROM news_digest
    WHERE scope = 'symbol' AND hit IS NOT NULL
    GROUP BY 1 ORDER BY 1
"""


async def report(pool: asyncpg.Pool) -> dict[str, list[dict]]:
    out = {}
    for name, group in (("all", "'all'"), ("confidence", "coalesce(confidence, '?')"),
                        ("outlook", "tone"), ("symbol", "symbol")):
        out[name] = [dict(r) for r in await pool.fetch(REPORT_SQL.format(group=group))]
    return out


def _rate(hits: int, n: int) -> str:
    return f"{hits / n * 100:5.1f}% ({hits}/{n})" if n else "   n/a"


async def _cli() -> None:
    from ..db import create_pool
    from ..migrate import migrate

    pool = await create_pool()
    alpaca = AlpacaClient.from_env()
    try:
        await migrate(pool)
        if alpaca is not None:
            print(f"judged now: {await evaluate_outlooks(pool, alpaca)}")
        else:
            print("ALPACA_API_KEY/SECRET not set: only printing what was judged before")
        rows = await report(pool)
    finally:
        await pool.close()
        if alpaca is not None:
            await alpaca.aclose()
    print(f"threshold ±{flat_pct()}% · {SESSIONS} sessions after each card")
    for section, items in rows.items():
        print(f"\n== {section}")
        for r in items:
            print(f"{str(r['grp']):10} AI {_rate(r['hits'], r['n'])} · "
                  f"baseline {_rate(r['bhits'], r['bn'])}")
    if not rows["all"]:
        print("no card has finished its 3 sessions yet")


def main() -> None:
    import asyncio

    logging.basicConfig(level=logging.WARNING)
    asyncio.run(_cli())


if __name__ == "__main__":
    main()
