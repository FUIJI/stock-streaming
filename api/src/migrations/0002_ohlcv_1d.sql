-- Daily candles from Alpaca (1Day bars). bucket_start = midnight America/New_York
-- of the trading day, as Alpaca stamps them. Today's candle is not stored here:
-- /candles?interval=1d rolls it up from ohlcv_1m until the nightly reconcile.
CREATE TABLE IF NOT EXISTS ohlcv_1d (
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
