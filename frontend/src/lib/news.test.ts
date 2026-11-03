import assert from "node:assert/strict";
import { test } from "node:test";

import { type NewsItem, filterNews, formatNewsTime, formatPct, sentimentFor, sentimentTone } from "./news.ts";

function item(id: number, importance: number | null, hidden = false): NewsItem {
  return {
    id, headline: `News ${id}`, summary: "", author: null, source: "benzinga", url: null,
    symbols: ["AAPL"], created_at: "2026-11-03T03:25:37Z", updated_at: "2026-11-03T03:25:37Z",
    backfilled: false, ai_status: importance === null ? "pending" : "done", skip_reason: null,
    ai: importance === null ? null : {
      headline_th: "หัวข่าว", summary_th: "สรุป", why_it_matters: "เพราะ", category: "product", importance,
      is_market_wide: false, model: "gemma", prompt_version: "v3", processed_at: "",
    },
    per_symbol: [{ symbol: "AAPL", relevance: hidden ? 0.2 : 0.9, sentiment: -0.6 }],
    reaction: { AAPL: { m5: null, m30: null } }, hidden,
  };
}

const items = [item(1, 5), item(2, 2), item(3, null), item(4, 4, true)];

test("all news hides less-relevant articles behind a count", () => {
  const { shown, hiddenCount } = filterNews(items, { importantOnly: false, showHidden: false });
  assert.deepEqual(shown.map((n) => n.id), [1, 2, 3]);
  assert.equal(hiddenCount, 1);
  assert.equal(filterNews(items, { importantOnly: false, showHidden: true }).shown.length, 4);
});

test("important = AI importance >= 4; unanalysed articles drop out", () => {
  const { shown, hiddenCount } = filterNews(items, { importantOnly: true, showHidden: false });
  assert.deepEqual(shown.map((n) => n.id), [1]);
  assert.equal(hiddenCount, 1);
});

test("sentiment tone has a neutral band", () => {
  assert.equal(sentimentTone(0.5), "positive");
  assert.equal(sentimentTone(-0.3), "negative");
  assert.equal(sentimentTone(0.1), "neutral");
  assert.equal(sentimentTone(null), "neutral");
  assert.equal(sentimentFor(items[0], "AAPL"), -0.6);
  assert.equal(sentimentFor(items[0], "MSFT"), null);
});

test("formatting", () => {
  assert.equal(formatPct(0.4231), "+0.42%");
  assert.equal(formatPct(-1.3), "−1.30%");
  assert.equal(formatPct(0), "0.00%");
  assert.equal(formatNewsTime("2026-11-03T03:25:37Z"), "3 Nov, 10:25");
});
