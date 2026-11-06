import assert from "node:assert/strict";
import { test } from "node:test";

import { alertable, alertText, initialSeen, newToasts, unread } from "./alerts.ts";
import type { NewsItem } from "./news.ts";

const NOW = Date.parse("2026-11-06T15:00:00Z");

function item(id: number, minutesAgo: number, importance: number | null, extra: Partial<NewsItem> = {}): NewsItem {
  const t = new Date(NOW - minutesAgo * 60_000).toISOString();
  return {
    id, headline: `News ${id}`, summary: "", author: null, source: "benzinga", url: null,
    symbols: ["AAPL"], created_at: t, updated_at: t, backfilled: false, skip_reason: null,
    ai_status: importance === null ? "pending" : "done",
    ai: importance === null ? null : {
      headline_th: `หัวข่าว ${id}`, summary_th: null, why_it_matters: null, category: "analyst",
      importance, is_market_wide: false, model: "gemma", prompt_version: "t3", processed_at: t,
    },
    per_symbol: [], reaction: {}, hidden: false, ...extra,
  };
}

test("only important, scored, non-backfilled articles alert, newest first", () => {
  const items = [item(1, 50, 4), item(2, 10, 5), item(3, 5, 3), item(4, 1, null),
                 item(5, 2, 5, { backfilled: true })];
  assert.deepEqual(alertable(items).map((n) => n.id), [2, 1]);
});

test("bell counts by time, not id (ids aren't time-ordered across sources)", () => {
  const items = [item(108413260, 120, 4), item(62825264, 30, 5)];  // bigger id is older
  const seen = new Date(NOW - 60 * 60_000).toISOString();
  assert.deepEqual(unread(items, seen).map((n) => n.id), [62825264]);
});

test("a first visit starts with an empty bell; later visits count what came in meanwhile", () => {
  const items = [item(1, 300, 4), item(2, 100, 5)];
  const seen = initialSeen(items, null);
  assert.equal(seen, items[1].created_at);
  assert.equal(unread(items, seen).length, 0);
  assert.equal(initialSeen([], null), new Date(0).toISOString());
  assert.equal(initialSeen(items, "2026-01-01T00:00:00Z"), "2026-01-01T00:00:00Z");
});

test("toasts: only new to this page, once, and only if fresh", () => {
  const known = new Set([1]);
  const items = [item(1, 5, 5), item(2, 3, 4), item(3, 200, 5)];  // #3 re-scored long after
  assert.deepEqual(newToasts(items, known, NOW).map((n) => n.id), [2]);
});

test("Thai headline first", () => {
  assert.equal(alertText(item(1, 1, 4)), "หัวข่าว 1");
  assert.equal(alertText({ ...item(1, 1, 4), ai: { ...item(1, 1, 4).ai!, headline_th: null } }), "News 1");
});
