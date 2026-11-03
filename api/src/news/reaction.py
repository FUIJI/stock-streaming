"""How the price moved after an article: % change 5 and 30 minutes later (spec N4).

Computed at query time from ohlcv_1m, so it fills in as candles arrive:
- reference = close of the last 1-minute candle that ended before the article's minute
- after k minutes = close of the last candle inside [article minute, article minute + k)
- None when there is no candle in that window yet (market closed, thin trading) or the
  window hasn't fully passed, so a still-moving candle is never reported as the result.
"""

from datetime import datetime

import asyncpg

WINDOWS = {"m5": 5, "m30": 30}

REACTION_SQL = """
    SELECT p.news_id, p.symbol, ref.close AS ref, m5.close AS m5, m30.close AS m30
    FROM unnest($1::bigint[], $2::text[], $3::timestamptz[]) AS p(news_id, symbol, t)
    CROSS JOIN LATERAL (SELECT date_trunc('minute', p.t) AS minute) m
    LEFT JOIN LATERAL (
        SELECT close FROM ohlcv_1m
        WHERE symbol = p.symbol AND bucket_start < m.minute
        ORDER BY bucket_start DESC LIMIT 1
    ) ref ON true
    LEFT JOIN LATERAL (
        SELECT close FROM ohlcv_1m
        WHERE symbol = p.symbol AND m.minute + interval '5 minutes' <= $4
          AND bucket_start >= m.minute AND bucket_start < m.minute + interval '5 minutes'
        ORDER BY bucket_start DESC LIMIT 1
    ) m5 ON true
    LEFT JOIN LATERAL (
        SELECT close FROM ohlcv_1m
        WHERE symbol = p.symbol AND m.minute + interval '30 minutes' <= $4
          AND bucket_start >= m.minute AND bucket_start < m.minute + interval '30 minutes'
        ORDER BY bucket_start DESC LIMIT 1
    ) m30 ON true
"""


def pct_change(ref: float | None, after: float | None) -> float | None:
    if ref is None or after is None or ref <= 0:
        return None
    return round((after - ref) / ref * 100, 2)


async def reactions(pool: asyncpg.Pool, pairs: list[tuple[int, str, datetime]],
                    now: datetime) -> dict[tuple[int, str], dict[str, float | None]]:
    """(news_id, symbol, created_at) -> {"m5": %, "m30": %} (values may be None)."""
    if not pairs:
        return {}
    ids, symbols, times = (list(col) for col in zip(*pairs, strict=True))
    rows = await pool.fetch(REACTION_SQL, ids, symbols, times, now)
    return {
        (r["news_id"], r["symbol"]): {k: pct_change(r["ref"], r[k]) for k in WINDOWS}
        for r in rows
    }
