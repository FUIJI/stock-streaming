import assert from "node:assert/strict";
import { test } from "node:test";

import {
  type Digest, accuracyLine, countShares, formatCardTime, numberSources, sourceTitle,
} from "./digest.ts";

function source(id: number, th: string | null = null) {
  return { id, headline: `News ${id}`, headline_th: th, source: "benzinga", url: null,
    created_at: "2026-11-04T03:25:00Z", importance: 4, sentiment: 0.5 };
}

const digest: Digest = {
  symbol: "AAPL", window_hours: 72, card_is_current: true,
  live: { counts: { positive: 1, negative: 1, neutral: 0 }, price_change_pct: -1, news_ids: [1, 2, 3] },
  sources: [source(1), source(2, "หัวข่าว"), source(3)],
  card: {
    id: 9, created_at: "2026-11-04T03:25:00Z", title: "t", summary: "s", title_en: "t",
    summary_en: "s", outlook: "mixed", confidence: "low", counts: null, price_change_pct: null,
    news_ids: [1, 3], model: "gemma", prompt_version: "d2",
    bullets: [{ side: "bull", text: "b", text_en: "b", news_ids: [3] },
              { side: "bear", text: "x", text_en: "x", news_ids: [1, 99] }],
  },
};

test("sources are numbered in the order the card cites them", () => {
  const { sources, index } = numberSources(digest);
  assert.deepEqual(sources.map((s) => s.id), [3, 1, 2]);
  assert.equal(index.get(3), 1);
  assert.equal(index.get(99), undefined); // cited but not returned: no number, no crash
});

test("without a card the live selection keeps its order", () => {
  const { sources } = numberSources({ ...digest, card: null });
  assert.deepEqual(sources.map((s) => s.id), [1, 2, 3]);
});

test("count bar shares", () => {
  assert.deepEqual(countShares({ positive: 1, negative: 1, neutral: 2 }),
    { positive: 25, negative: 25, neutral: 50 });
  assert.deepEqual(countShares({ positive: 0, negative: 0, neutral: 0 }),
    { positive: 0, negative: 0, neutral: 0 });
});

test("titles prefer Thai; times are Thai time", () => {
  assert.equal(sourceTitle(source(2, "หัวข่าว")), "หัวข่าว");
  assert.equal(sourceTitle(source(1)), "News 1");
  assert.equal(formatCardTime("2026-11-04T03:25:00Z"), "4 Nov, 10:25");
});

test("accuracy shows only with enough judged cards", () => {
  assert.equal(accuracyLine(null), null);
  assert.equal(accuracyLine({ evaluated: 19, hits: 10, rate: 0.53, baseline_rate: 0.5 }), null);
  assert.equal(accuracyLine({ evaluated: 34, hits: 20, rate: 0.588, baseline_rate: 0.51 }),
    "Past outlooks: 59% right (naive guess 51%) · 34 judged");
});
