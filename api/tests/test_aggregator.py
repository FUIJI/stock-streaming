from datetime import UTC, datetime

from src.aggregator import CandleAggregator, flush_candles

T0 = 1_700_000_040_000  # 2023-11-14T22:14:00Z, a minute boundary


def test_builds_ohlcv_by_event_time_even_when_out_of_order():
    agg = CandleAggregator()
    agg.add("AAPL", 101.0, 10, T0 + 5_000)
    agg.add("AAPL", 100.0, 5, T0 + 1_000)  # earlier trade arrives later -> becomes open
    agg.add("AAPL", 104.0, 1, T0 + 30_000)
    agg.add("AAPL", 99.0, 2, T0 + 20_000)

    [row] = agg.pop_dirty()
    assert row == ("AAPL", datetime.fromtimestamp(T0 / 1000, tz=UTC),
                   100.0, 104.0, 99.0, 104.0, 18.0, 4)


def test_separates_symbols_and_minutes():
    agg = CandleAggregator()
    agg.add("AAPL", 1.0, 1, T0)
    agg.add("MSFT", 2.0, 1, T0)
    agg.add("AAPL", 3.0, 1, T0 + 60_000)
    rows = agg.pop_dirty()
    assert sorted((r[0], r[1].timestamp()) for r in rows) == [
        ("AAPL", T0 / 1000), ("AAPL", T0 / 1000 + 60), ("MSFT", T0 / 1000),
    ]


def test_pop_dirty_only_returns_changed_candles():
    agg = CandleAggregator()
    agg.add("AAPL", 1.0, 1, T0)
    assert len(agg.pop_dirty()) == 1
    assert agg.pop_dirty() == []
    agg.add("AAPL", 2.0, 1, T0 + 1_000)
    [row] = agg.pop_dirty()
    assert row[5] == 2.0 and row[7] == 2  # close, trade_count accumulate across flushes


def test_drops_trades_behind_the_watermark():
    agg = CandleAggregator(watermark_ms=30_000)
    agg.add("AAPL", 1.0, 1, T0 + 120_000)
    assert agg.add("AAPL", 9.0, 1, T0 + 60_000) is False
    assert agg.add("AAPL", 2.0, 1, T0 + 95_000) is True  # within 30s: accepted


def test_evicts_only_closed_and_flushed_candles():
    agg = CandleAggregator(watermark_ms=30_000)
    agg.add("AAPL", 1.0, 1, T0)
    agg.add("AAPL", 2.0, 1, T0 + 95_000)  # closes the first bucket (end + 30s <= max)
    agg.evict_closed()  # both still dirty -> nothing evicted
    assert len(agg.pop_dirty()) == 2
    agg.evict_closed()
    agg.add("AAPL", 3.0, 1, T0 + 96_000)
    assert [r[1].timestamp() for r in agg.pop_dirty()] == [T0 / 1000 + 60]


class FakePool:
    def __init__(self, fail=False):
        self.fail = fail
        self.batches = []

    async def executemany(self, query, rows):
        if self.fail:
            raise ConnectionError("db down")
        self.batches.append(rows)


async def test_flush_requeues_rows_when_db_write_fails():
    agg = CandleAggregator()
    agg.add("AAPL", 1.0, 1, T0)
    failing = FakePool(fail=True)
    try:
        await flush_candles(agg, failing)
    except ConnectionError:
        pass
    ok = FakePool()
    await flush_candles(agg, ok)
    assert len(ok.batches) == 1 and ok.batches[0][0][0] == "AAPL"


def test_seeded_candle_continues_instead_of_being_overwritten():
    agg = CandleAggregator()
    bucket = datetime.fromtimestamp(T0 / 1000, tz=UTC)
    agg.seed([("AAPL", bucket, 100.0, 110.0, 95.0, 105.0, 50.0, 7)])
    assert agg.pop_dirty() == []  # seeding alone writes nothing

    agg.add("AAPL", 120.0, 5.0, T0 + 40_000)
    [row] = agg.pop_dirty()
    assert row == ("AAPL", bucket, 100.0, 120.0, 95.0, 120.0, 55.0, 8)
