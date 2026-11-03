// News panel logic (spec N7). No "@/" imports: `npm test` runs this with Node directly.
import { DISPLAY_TZ } from "./market-time.ts";

export const IMPORTANT = 4; // the "Important" filter and (later) alerts

export interface NewsAI {
  summary_th: string;
  why_it_matters: string;
  category: string;
  importance: number;
  is_market_wide: boolean;
  model: string;
  prompt_version: string;
  processed_at: string;
}

export interface NewsItem {
  id: number;
  headline: string;
  summary: string;
  author: string | null;
  source: string | null;
  url: string | null;
  symbols: string[];
  created_at: string;
  updated_at: string;
  backfilled: boolean;
  ai_status: "pending" | "done" | "failed" | "skipped";
  skip_reason: string | null;
  ai: NewsAI | null;
  per_symbol: { symbol: string; relevance: number; sentiment: number }[];
  reaction: Record<string, { m5: number | null; m30: number | null }>;
  hidden: boolean;
}

export interface NewsFilter {
  importantOnly: boolean;
  showHidden: boolean;
}

/** What the list shows, plus how many less-relevant articles sit behind the button. */
export function filterNews(items: NewsItem[], filter: NewsFilter) {
  const matching = items.filter(
    (n) => !filter.importantOnly || (n.ai !== null && n.ai.importance >= IMPORTANT),
  );
  const hiddenCount = matching.filter((n) => n.hidden).length;
  return {
    shown: filter.showHidden ? matching : matching.filter((n) => !n.hidden),
    hiddenCount,
  };
}

export type Tone = "positive" | "negative" | "neutral";

/** Sentiment −1..1 -> colour; the small band around 0 reads as neutral. */
export function sentimentTone(sentiment: number | null | undefined): Tone {
  if (sentiment == null || Math.abs(sentiment) < 0.2) return "neutral";
  return sentiment > 0 ? "positive" : "negative";
}

export function sentimentFor(item: NewsItem, symbol: string): number | null {
  return item.per_symbol.find((s) => s.symbol === symbol)?.sentiment ?? null;
}

/** "+0.42%", "−1.30%", "0.00%" */
export function formatPct(pct: number): string {
  if (pct === 0) return "0.00%";
  return `${pct > 0 ? "+" : "−"}${Math.abs(pct).toFixed(2)}%`;
}

/** "3 Nov, 10:25" in Thai time (the app's display zone). */
export function formatNewsTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TZ,
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}

export const CATEGORY_LABELS: Record<string, string> = {
  earnings: "Earnings",
  guidance: "Guidance",
  analyst: "Analyst",
  product: "Product",
  legal_regulatory: "Legal",
  m_and_a: "M&A",
  management: "Management",
  macro: "Macro",
  market_roundup: "Roundup",
  other: "Other",
};
