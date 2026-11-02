-- Tracked symbols. `active` = currently in SYMBOLS (what ingestion subscribes to);
-- name/exchange are filled in from Alpaca's assets API by the reconcile job.
CREATE TABLE IF NOT EXISTS symbols (
    symbol     TEXT        PRIMARY KEY,
    name       TEXT,
    exchange   TEXT,
    active     BOOLEAN     NOT NULL DEFAULT true,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
