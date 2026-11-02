-- Same table as storage/timescale/init/001_schema.sql, so a database that never
-- ran the init scripts (e.g. Supabase) is complete too. No-op where it exists.
CREATE TABLE IF NOT EXISTS ohlcv_1m (
    symbol       TEXT             NOT NULL,
    bucket_start TIMESTAMPTZ      NOT NULL,
    open         DOUBLE PRECISION NOT NULL,
    high         DOUBLE PRECISION NOT NULL,
    low          DOUBLE PRECISION NOT NULL,
    close        DOUBLE PRECISION NOT NULL,
    volume       DOUBLE PRECISION NOT NULL DEFAULT 0,
    trade_count  BIGINT           NOT NULL DEFAULT 0,
    updated_at   TIMESTAMPTZ      NOT NULL DEFAULT now(),
    PRIMARY KEY (symbol, bucket_start)
);
