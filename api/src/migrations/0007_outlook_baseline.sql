-- D11: the naive baseline each outlook is compared with ("the last 3 trading days'
-- direction continues"), so the accuracy report can say whether the AI beats it.
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS baseline_pct DOUBLE PRECISION;
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS baseline_hit BOOLEAN;
