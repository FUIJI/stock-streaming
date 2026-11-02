import assert from "node:assert/strict";
import { test } from "node:test";

import {
  barSession,
  bucketStart,
  formatCountdown,
  formatDisplayTime,
  formatTick,
  marketDayStart,
  mergeTrade,
} from "./market-time.ts";

const at = (iso: string) => Date.parse(iso) / 1000;

test("sessions follow New York clock time across the DST change", () => {
  // Friday 30 Oct 2026 is EDT (UTC-4); Monday 2 Nov is EST (UTC-5).
  assert.equal(barSession(at("2026-10-30T08:00:00Z")), "pre"); // 04:00 EDT
  assert.equal(barSession(at("2026-10-30T13:29:00Z")), "pre");
  assert.equal(barSession(at("2026-10-30T13:30:00Z")), "regular"); // 09:30 EDT
  assert.equal(barSession(at("2026-10-30T20:00:00Z")), "post"); // 16:00 EDT
  assert.equal(barSession(at("2026-11-03T00:59:00Z")), "post"); // 19:59 EST Monday
  assert.equal(barSession(at("2026-11-02T14:29:00Z")), "pre"); // 09:29 EST
  assert.equal(barSession(at("2026-11-02T14:30:00Z")), "regular");
  assert.equal(barSession(at("2026-11-03T01:00:00Z")), "closed"); // 20:00 EST
});

test("daily candles start at midnight New York, like Alpaca's daily bars", () => {
  assert.equal(marketDayStart(at("2026-10-30T19:00:00Z")), at("2026-10-30T04:00:00Z"));
  assert.equal(marketDayStart(at("2026-11-02T23:30:00Z")), at("2026-11-02T05:00:00Z"));
  // 21:00 EST Monday is still Monday in New York although it is Tuesday in UTC.
  assert.equal(marketDayStart(at("2026-11-03T02:00:00Z")), at("2026-11-02T05:00:00Z"));
});

test("trades land in the candle of the selected interval", () => {
  const ts = Date.parse("2026-11-02T15:47:31Z");
  assert.equal(bucketStart(ts, "1m"), at("2026-11-02T15:47:00Z"));
  assert.equal(bucketStart(ts, "5m"), at("2026-11-02T15:45:00Z"));
  assert.equal(bucketStart(ts, "1h"), at("2026-11-02T15:00:00Z"));
  assert.equal(bucketStart(ts, "1d"), at("2026-11-02T05:00:00Z"));
});

test("mergeTrade extends the live candle, opens the next one, ignores stale trades", () => {
  const t0 = Date.parse("2026-11-02T15:45:10Z");
  const first = mergeTrade(null, { price: 10, volume: 5, ts: t0 }, "5m");
  assert.deepEqual(first, { time: at("2026-11-02T15:45:00Z"), open: 10, high: 10, low: 10, close: 10, volume: 5 });

  const same = mergeTrade(first, { price: 12, volume: 1, ts: t0 + 60_000 }, "5m");
  assert.deepEqual(same, { ...first, high: 12, close: 12, volume: 6 });

  const next = mergeTrade(same, { price: 11, volume: 2, ts: t0 + 300_000 }, "5m");
  assert.equal(next?.time, at("2026-11-02T15:50:00Z"));
  assert.equal(next?.open, 11);

  assert.equal(mergeTrade(next, { price: 9, volume: 1, ts: t0 }, "5m"), null);
});

test("labels are in Thai time", () => {
  assert.equal(formatDisplayTime(at("2026-11-02T14:30:00Z"), "1m"), "2 Nov, 21:30");
  assert.equal(formatDisplayTime(at("2026-11-02T05:00:00Z"), "1d"), "2 Nov 2026");
});

test("countdown formatting", () => {
  assert.equal(formatCountdown(30_000), "<1m");
  assert.equal(formatCountdown(12 * 60_000), "12m");
  assert.equal(formatCountdown((2 * 60 + 5) * 60_000), "2h 05m");
  assert.equal(formatCountdown((50 * 60) * 60_000), "2d 2h");
});

test("axis ticks are in Thai time", () => {
  assert.equal(formatTick(at("2026-11-02T17:00:00Z"), 3), "00:00"); // midnight in Bangkok
  assert.equal(formatTick(at("2026-11-02T17:00:00Z"), 2), "3 Nov");
});
