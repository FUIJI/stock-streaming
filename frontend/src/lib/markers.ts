// News markers on the chart (Phase 3d, D8 / Q16). No "@/" imports: `npm test` runs this.
import type { ChartInterval } from "./market-time.ts";
import { type NewsItem, sentimentFor, sentimentTone, type Tone } from "./news.ts";

export const MARKER_MIN_IMPORTANCE = 3;

const WIDTH_SEC: Record<ChartInterval, number> = { "1m": 60, "5m": 300, "1h": 3600, "1d": 86400 };

export interface NewsMarker {
  time: number; // the candle's start (epoch seconds)
  tone: Tone; // average sentiment of its news for this symbol
  big: boolean; // any of them importance 5
  items: NewsItem[]; // newest first, for the tooltip
}

/**
 * The candle an article belongs to: the one it falls inside, else the next candle after
 * it (news while the market is closed shows where trading resumes and the price can
 * react). On "1d" every article lands in its day's candle. Null when it's beyond the data.
 */
export function candleFor(tsSec: number, candleTimes: number[], interval: ChartInterval): number | null {
  const width = WIDTH_SEC[interval];
  let lo = 0;
  let hi = candleTimes.length - 1;
  let before = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (candleTimes[mid] <= tsSec) {
      before = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  if (before >= 0 && tsSec < candleTimes[before] + width) return candleTimes[before];
  const after = before + 1;
  return after < candleTimes.length ? candleTimes[after] : null;
}

export function buildMarkers(news: NewsItem[], candleTimes: number[], interval: ChartInterval,
                             symbol: string): NewsMarker[] {
  const sorted = [...candleTimes].sort((a, b) => a - b);
  const groups = new Map<number, NewsItem[]>();
  for (const n of news) {
    if (!n.ai || n.ai.importance < MARKER_MIN_IMPORTANCE || n.hidden) continue;
    const time = candleFor(Date.parse(n.created_at) / 1000, sorted, interval);
    if (time === null) continue;
    groups.set(time, [...(groups.get(time) ?? []), n]);
  }
  return [...groups.entries()]
    .sort(([a], [b]) => a - b)
    .map(([time, items]) => {
      const scores = items.map((n) => sentimentFor(n, symbol)).filter((s): s is number => s !== null);
      const avg = scores.length ? scores.reduce((a, b) => a + b, 0) / scores.length : null;
      return {
        time,
        tone: sentimentTone(avg),
        big: items.some((n) => (n.ai?.importance ?? 0) >= 5),
        items: items.sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at)),
      };
    });
}
