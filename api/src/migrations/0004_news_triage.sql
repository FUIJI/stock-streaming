-- Phase 3d: rule-based skipping, one-line Thai headlines, per-symbol digests.
-- docs/specs/phase-3d-news-digest.md
ALTER TABLE news DROP CONSTRAINT IF EXISTS news_ai_status_check;
ALTER TABLE news ADD CONSTRAINT news_ai_status_check
    CHECK (ai_status IN ('pending', 'done', 'failed', 'skipped'));
ALTER TABLE news ADD COLUMN IF NOT EXISTS skip_reason TEXT;  -- why the rules skipped it

ALTER TABLE news_ai ADD COLUMN IF NOT EXISTS headline_th TEXT;  -- null: failed the text checks
ALTER TABLE news_ai ALTER COLUMN summary_th DROP NOT NULL;      -- Phase 3c only
ALTER TABLE news_ai ALTER COLUMN why_it_matters DROP NOT NULL;  -- Phase 3c only

CREATE TABLE IF NOT EXISTS news_digest (
    id             BIGSERIAL   PRIMARY KEY,
    scope          TEXT        NOT NULL CHECK (scope IN ('symbol', 'watchlist')),
    symbol         TEXT,       -- null for the watchlist digest
    period_start   TIMESTAMPTZ NOT NULL,
    period_end     TIMESTAMPTZ NOT NULL,
    title_th       TEXT        NOT NULL,
    bullets        JSONB       NOT NULL,  -- [{"text": ..., "news_ids": [...]}]
    tone           TEXT        NOT NULL CHECK (tone IN ('positive', 'negative', 'mixed', 'neutral')),
    news_ids       BIGINT[]    NOT NULL,  -- every article the digest cites
    model          TEXT        NOT NULL,
    prompt_version TEXT        NOT NULL,
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    CHECK ((scope = 'symbol') = (symbol IS NOT NULL))
);
CREATE INDEX IF NOT EXISTS news_digest_latest_idx ON news_digest (scope, symbol, period_end DESC);

-- One row per scheduled digest round and symbol; retried like the news queue.
CREATE TABLE IF NOT EXISTS digest_jobs (
    id              BIGSERIAL   PRIMARY KEY,
    scope           TEXT        NOT NULL CHECK (scope IN ('symbol', 'watchlist')),
    symbol          TEXT,
    due_at          TIMESTAMPTZ NOT NULL,
    status          TEXT        NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'done', 'failed', 'skipped')),
    attempts        INT         NOT NULL DEFAULT 0,
    next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    error           TEXT,
    UNIQUE NULLS NOT DISTINCT (scope, symbol, due_at)
);
CREATE INDEX IF NOT EXISTS digest_jobs_queue_idx ON digest_jobs (status, next_attempt_at);
