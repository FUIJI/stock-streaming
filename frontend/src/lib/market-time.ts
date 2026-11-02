// Pure time helpers shared by the chart and badges. No imports with the "@/" alias:
// `npm test` runs these files directly with Node's type stripping.

export type ChartInterval = "1m" | "5m" | "1h" | "1d";
export type BarSession = "pre" | "regular" | "post" | "closed";

export const DISPLAY_TZ = "Asia/Bangkok";
const MARKET_TZ = "America/New_York";

const INTERVAL_SECONDS: Record<Exclude<ChartInterval, "1d">, number> = {
  "1m": 60,
  "5m": 300,
  "1h": 3600,
};

function zonedParts(epochSec: number, timeZone: string) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(new Date(epochSec * 1000));
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  return { hour: get("hour"), minute: get("minute"), second: get("second") };
}

/** Which US session a bar starting at `epochSec` belongs to (by New York clock time). */
export function barSession(epochSec: number): BarSession {
  const { hour, minute } = zonedParts(epochSec, MARKET_TZ);
  const minutes = hour * 60 + minute;
  if (minutes >= 4 * 60 && minutes < 9 * 60 + 30) return "pre";
  if (minutes >= 9 * 60 + 30 && minutes < 16 * 60) return "regular";
  if (minutes >= 16 * 60 && minutes < 20 * 60) return "post";
  return "closed";
}

/** Midnight New York time for the day containing `epochSec` (how daily bars are stamped). */
export function marketDayStart(epochSec: number): number {
  // Exact on trading days: US DST switches happen at 2am on Sundays.
  const { hour, minute, second } = zonedParts(epochSec, MARKET_TZ);
  return epochSec - (hour * 3600 + minute * 60 + second);
}

/** Start time (epoch seconds) of the candle a trade at `tsMs` belongs to. */
export function bucketStart(tsMs: number, interval: ChartInterval): number {
  const sec = Math.floor(tsMs / 1000);
  if (interval === "1d") return marketDayStart(sec);
  const width = INTERVAL_SECONDS[interval];
  return sec - (sec % width);
}

export interface Ohlc {
  time: number;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
}

/** Fold a live trade into the latest candle. Returns null for trades older than it. */
export function mergeTrade(
  last: Ohlc | null,
  trade: { price: number; volume: number; ts: number },
  interval: ChartInterval,
): Ohlc | null {
  const time = bucketStart(trade.ts, interval);
  if (!last || time > last.time) {
    const p = trade.price;
    return { time, open: p, high: p, low: p, close: p, volume: trade.volume };
  }
  if (time < last.time) return null;
  return {
    ...last,
    high: Math.max(last.high, trade.price),
    low: Math.min(last.low, trade.price),
    close: trade.price,
    volume: last.volume + trade.volume,
  };
}

/** Axis/crosshair label in Thai time: "14:35", or "4 Nov" for daily bars. */
export function formatDisplayTime(epochSec: number, interval: ChartInterval): string {
  const opts: Intl.DateTimeFormatOptions =
    interval === "1d"
      ? { timeZone: DISPLAY_TZ, day: "numeric", month: "short", year: "numeric" }
      : { timeZone: DISPLAY_TZ, day: "numeric", month: "short", hour: "2-digit", minute: "2-digit", hourCycle: "h23" };
  return new Intl.DateTimeFormat("en-GB", opts).format(new Date(epochSec * 1000));
}

export function formatClock(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TZ,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}

/** "2h 05m", "12m", "<1m" */
export function formatCountdown(ms: number): string {
  const minutes = Math.floor(ms / 60_000);
  if (minutes < 1) return "<1m";
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const mins = minutes % 60;
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${String(mins).padStart(2, "0")}m`;
  return `${mins}m`;
}

/** Axis tick label in Thai time. `kind` mirrors lightweight-charts' TickMarkType
 * (0 year, 1 month, 2 day of month, 3 time, 4 time with seconds). */
export function formatTick(epochSec: number, kind: number): string {
  const d = new Date(epochSec * 1000);
  const fmt = (opts: Intl.DateTimeFormatOptions) =>
    new Intl.DateTimeFormat("en-GB", { timeZone: DISPLAY_TZ, ...opts }).format(d);
  if (kind === 0) return fmt({ year: "numeric" });
  if (kind === 1) return fmt({ month: "short" });
  if (kind === 2) return fmt({ day: "numeric", month: "short" });
  return fmt({ hour: "2-digit", minute: "2-digit", hourCycle: "h23" });
}
