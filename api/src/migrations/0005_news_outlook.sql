-- Phase 3d D4: the per-symbol 3-day outlook card (docs/specs/phase-3d-news-digest.md).
-- `tone` holds the outlook (positive / negative / mixed); bullets hold
-- [{"side": "bull"|"bear", "text": ..., "news_ids": [...]}].
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS summary_th TEXT;
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS confidence TEXT
    CHECK (confidence IN ('low', 'medium', 'high'));
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS counts JSONB;  -- {"positive", "negative", "neutral"}
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS price_change_pct DOUBLE PRECISION;
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS price_at_create DOUBLE PRECISION;
-- Filled 3 trading days later (D11): did the outlook come true?
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS outcome_close DOUBLE PRECISION;
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS outcome_pct DOUBLE PRECISION;
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS outcome_at TIMESTAMPTZ;
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS hit BOOLEAN;
