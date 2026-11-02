"""Lite-mode replacement for the Spark OHLCV job: in-memory 1-minute candles.

Same semantics as processing/spark/jobs/ohlcv_aggregation.py: event-time
buckets, a watermark that drops trades arriving too late, and the
currently-forming candle upserted on every flush ("update" output mode).
"""

import asyncio
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import asyncpg

log = logging.getLogger("api.aggregator")

BUCKET_MS = 60_000

UPSERT_SQL = """
    INSERT INTO ohlcv_1m
        (symbol, bucket_start, open, high, low, close, volume, trade_count, updated_at)
    VALUES ($1, $2, $3, $4, $5, $6, $7, $8, now())
    ON CONFLICT (symbol, bucket_start) DO UPDATE SET
        open = EXCLUDED.open,
        high = EXCLUDED.high,
        low = EXCLUDED.low,
        close = EXCLUDED.close,
        volume = EXCLUDED.volume,
        trade_count = EXCLUDED.trade_count,
        updated_at = now()
"""

CandleRow = tuple[str, datetime, float, float, float, float, float, int]


@dataclass
class _Bar:
    open: float
    high: float
    low: float
    close: float
    volume: float
    trade_count: int
    first_ts: int
    last_ts: int


class CandleAggregator:
    def __init__(self, watermark_ms: int = 30_000):
        self._watermark_ms = watermark_ms
        self._bars: dict[tuple[str, int], _Bar] = {}
        self._dirty: set[tuple[str, int]] = set()
        self._max_ts = 0

    def add(self, symbol: str, price: float, volume: float, ts: int) -> bool:
        """Fold one trade into its candle. Returns False if it was dropped as late."""
        if ts < self._max_ts - self._watermark_ms:
            return False
        self._max_ts = max(self._max_ts, ts)
        key = (symbol, ts - ts % BUCKET_MS)
        bar = self._bars.get(key)
        if bar is None:
            self._bars[key] = _Bar(price, price, price, price, volume, 1, ts, ts)
        else:
            bar.high = max(bar.high, price)
            bar.low = min(bar.low, price)
            bar.volume += volume
            bar.trade_count += 1
            # Trades can arrive out of order: open/close follow event time, not arrival.
            if ts < bar.first_ts:
                bar.open, bar.first_ts = price, ts
            if ts >= bar.last_ts:
                bar.close, bar.last_ts = price, ts
        self._dirty.add(key)
        return True

    def seed(self, rows: list[CandleRow]) -> None:
        """Resume candles already in the DB (e.g. after a restart mid-minute).

        Without this, the first flush after a restart would overwrite the
        current minute with only the trades seen since startup.
        """
        for symbol, bucket_start, open_, high, low, close, volume, trade_count in rows:
            bucket_ms = int(bucket_start.timestamp() * 1000)
            # Event times within the bucket are unknown; any later trade becomes the close.
            self._bars[(symbol, bucket_ms)] = _Bar(open_, high, low, close, volume, trade_count,
                                                   bucket_ms, bucket_ms)
            self._max_ts = max(self._max_ts, bucket_ms)

    def pop_dirty(self) -> list[CandleRow]:
        """Candles changed since the last call, as rows for UPSERT_SQL."""
        rows = [self._row(key) for key in sorted(self._dirty, key=lambda k: k[1])]
        self._dirty.clear()
        return rows

    def mark_dirty(self, rows: list[CandleRow]) -> None:
        """Re-queue rows whose upsert failed so the next flush retries them."""
        for symbol, bucket_start, *_ in rows:
            key = (symbol, int(bucket_start.timestamp() * 1000))
            if key in self._bars:
                self._dirty.add(key)

    def evict_closed(self) -> None:
        """Forget candles the watermark has closed; call only after they were flushed."""
        cutoff = self._max_ts - self._watermark_ms
        for key in [k for k in self._bars if k[1] + BUCKET_MS <= cutoff and k not in self._dirty]:
            del self._bars[key]

    def _row(self, key: tuple[str, int]) -> CandleRow:
        bar = self._bars[key]
        bucket_start = datetime.fromtimestamp(key[1] / 1000, tz=UTC)
        return (key[0], bucket_start, bar.open, bar.high, bar.low, bar.close,
                bar.volume, bar.trade_count)


async def load_recent_candles(
    pool: asyncpg.Pool, window: timedelta = timedelta(minutes=2)
) -> list[CandleRow]:
    rows = await pool.fetch(
        """
        SELECT symbol, bucket_start, open, high, low, close, volume, trade_count
        FROM ohlcv_1m
        WHERE bucket_start >= now() - $1::interval
        """,
        window,
    )
    return [tuple(row) for row in rows]


async def flush_candles(aggregator: CandleAggregator, pool: asyncpg.Pool) -> None:
    rows = aggregator.pop_dirty()
    if not rows:
        return
    try:
        await pool.executemany(UPSERT_SQL, rows)
    except Exception:
        aggregator.mark_dirty(rows)
        raise
    aggregator.evict_closed()


async def run_flusher(aggregator: CandleAggregator, pool: asyncpg.Pool, interval: float) -> None:
    """Background task: upsert changed candles every `interval` seconds."""
    while True:
        await asyncio.sleep(interval)
        try:
            await flush_candles(aggregator, pool)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("Candle flush failed; will retry")
