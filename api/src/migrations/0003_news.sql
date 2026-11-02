-- News for tracked symbols (Alpaca / Benzinga) and their LLM enrichment.
-- docs/specs/phase-3c-news.md
CREATE TABLE IF NOT EXISTS news (
    id                 BIGINT      PRIMARY KEY,  -- Alpaca news id
    headline           TEXT        NOT NULL,
    summary            TEXT        NOT NULL DEFAULT '',
    author             TEXT,
    source             TEXT,
    url                TEXT,
    symbols            TEXT[]      NOT NULL,      -- every tag Alpaca gave, not only tracked ones
    created_at         TIMESTAMPTZ NOT NULL,
    updated_at         TIMESTAMPTZ NOT NULL,
    fetched_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    backfilled         BOOLEAN     NOT NULL DEFAULT false,  -- loaded at first start: never alerted
    ai_status          TEXT        NOT NULL DEFAULT 'pending'
                       CHECK (ai_status IN ('pending', 'done', 'failed')),
    ai_attempts        INT         NOT NULL DEFAULT 0,
    ai_next_attempt_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    ai_error           TEXT
);
CREATE INDEX IF NOT EXISTS news_created_at_idx ON news (created_at DESC);
CREATE INDEX IF NOT EXISTS news_ai_queue_idx ON news (ai_status, ai_next_attempt_at);

CREATE TABLE IF NOT EXISTS news_ai (
    news_id        BIGINT      PRIMARY KEY REFERENCES news (id) ON DELETE CASCADE,
    summary_th     TEXT        NOT NULL,
    why_it_matters TEXT        NOT NULL,
    category       TEXT        NOT NULL,
    importance     SMALLINT    NOT NULL CHECK (importance BETWEEN 1 AND 5),  -- after code rules
    importance_raw SMALLINT    NOT NULL CHECK (importance_raw BETWEEN 1 AND 5),  -- as the LLM said
    is_market_wide BOOLEAN     NOT NULL,
    model          TEXT        NOT NULL,
    prompt_version TEXT        NOT NULL,
    processed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Only tracked symbols that appear in the article's tags.
CREATE TABLE IF NOT EXISTS news_symbols (
    news_id   BIGINT           NOT NULL REFERENCES news (id) ON DELETE CASCADE,
    symbol    TEXT             NOT NULL,
    relevance DOUBLE PRECISION NOT NULL CHECK (relevance BETWEEN 0 AND 1),
    sentiment DOUBLE PRECISION NOT NULL CHECK (sentiment BETWEEN -1 AND 1),
    PRIMARY KEY (news_id, symbol)
);
CREATE INDEX IF NOT EXISTS news_symbols_symbol_idx ON news_symbols (symbol);
