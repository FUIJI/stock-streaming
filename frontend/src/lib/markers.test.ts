import assert from "node:assert/strict";
import { test } from "node:test";

import { buildMarkers, candleFor } from "./markers.ts";
import type { NewsItem } from "./news.ts";

const T0 = Date.parse("2026-11-04T15:00:00Z") / 1000; // 10:00 New York
const MINUTES = [0, 1, 2, 5].map((m) => T0 + m * 60); // a gap at 3-4 (no trades)

function item(id: number, at: number, importance: number, sentiment: number, hidden = false): NewsItem {
  const t = new Date(at * 1000).toISOString();
  return {
    id, headline: `News ${id}`, summary: "", author: null, source: null, url: null,
    symbols: ["AAPL"], created_at: t, updated_at: t, backfilled: false, ai_status: "done",
    skip_reason: null,
    ai: { headline_th: null, summary_th: null, why_it_matters: null, category: "analyst",
          importance, is_market_wide: false, model: "g", prompt_version: "t3", processed_at: t },
    per_symbol: [{ symbol: "AAPL", relevance: 0.9, sentiment }], reaction: {}, hidden,
  };
}

test("an article lands in its candle, or the next one when there is no trading", () => {
  assert.equal(candleFor(T0 + 30, MINUTES, "1m"), T0);
  assert.equal(candleFor(T0 + 200, MINUTES, "1m"), T0 + 300); // in the gap -> next candle
  assert.equal(candleFor(T0 - 3600, MINUTES, "1m"), T0); // before the data -> first candle
  assert.equal(candleFor(T0 + 400, MINUTES, "1m"), null); // after the last candle
  const days = [T0, T0 + 86400];
  assert.equal(candleFor(T0 + 30_000, days, "1d"), T0); // same day, after the close
});

test("one marker per candle: tone from the average, big when any is a 5", () => {
  const news = [item(1, T0 + 10, 4, 0.8), item(2, T0 + 20, 5, -0.2), item(3, T0 + 70, 3, -0.6),
                item(4, T0 + 80, 2, 0.9), item(5, T0 + 90, 4, 0.9, true)];
  const markers = buildMarkers(news, MINUTES, "1m", "AAPL");
  assert.deepEqual(markers.map((m) => [m.time, m.tone, m.big, m.items.map((n) => n.id)]), [
    [T0, "positive", true, [2, 1]], // avg +0.3, newest first
    [T0 + 60, "negative", false, [3]], // importance 2 and hidden ones are left out
  ]);
});

test("daily charts get one marker per day", () => {
  const days = [T0, T0 + 86400];
  const news = [item(1, T0 + 100, 3, 0.5), item(2, T0 + 20_000, 4, 0.5), item(3, T0 + 90_000, 3, -0.5)];
  assert.deepEqual(buildMarkers(news, days, "1d", "AAPL").map((m) => m.items.length), [2, 1]);
});
