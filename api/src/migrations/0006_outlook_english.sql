-- Outlook cards are written in English, then translated line by line (prompt d2).
-- title_th / summary_th / bullets[].text hold the Thai, or the English where the
-- translation failed the checks; the English original is kept here and in bullets[].text_en.
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS title_en TEXT;
ALTER TABLE news_digest ADD COLUMN IF NOT EXISTS summary_en TEXT;
