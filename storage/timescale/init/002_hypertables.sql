-- Our bind mount replaces the image's /docker-entrypoint-initdb.d (which is what
-- normally creates the extension), so create it here explicitly.
CREATE EXTENSION IF NOT EXISTS timescaledb;

SELECT create_hypertable(
    'ohlcv_1m',
    by_range('bucket_start', INTERVAL '7 days'),
    if_not_exists => TRUE
);

-- Continuous aggregates for coarser intervals, both built from ohlcv_1m.
-- materialized_only = false so queries also see the not-yet-materialized tail.
CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_5m
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    symbol,
    time_bucket('5 minutes', bucket_start) AS bucket_start,
    first(open, bucket_start)              AS open,
    max(high)                              AS high,
    min(low)                               AS low,
    last(close, bucket_start)              AS close,
    sum(volume)                            AS volume,
    sum(trade_count)                       AS trade_count
FROM ohlcv_1m
GROUP BY symbol, time_bucket('5 minutes', bucket_start)
WITH NO DATA;

CREATE MATERIALIZED VIEW IF NOT EXISTS ohlcv_1h
WITH (timescaledb.continuous, timescaledb.materialized_only = false) AS
SELECT
    symbol,
    time_bucket('1 hour', bucket_start) AS bucket_start,
    first(open, bucket_start)           AS open,
    max(high)                           AS high,
    min(low)                            AS low,
    last(close, bucket_start)           AS close,
    sum(volume)                         AS volume,
    sum(trade_count)                    AS trade_count
FROM ohlcv_1m
GROUP BY symbol, time_bucket('1 hour', bucket_start)
WITH NO DATA;

SELECT add_continuous_aggregate_policy('ohlcv_5m',
    start_offset      => INTERVAL '1 hour',
    end_offset        => INTERVAL '5 minutes',
    schedule_interval => INTERVAL '5 minutes',
    if_not_exists     => TRUE
);

SELECT add_continuous_aggregate_policy('ohlcv_1h',
    start_offset      => INTERVAL '1 day',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '30 minutes',
    if_not_exists     => TRUE
);
