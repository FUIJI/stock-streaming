// Outlook card logic (Phase 3d, D6). No "@/" imports: `npm test` runs this with Node.
import { DISPLAY_TZ } from "./market-time.ts";

export type Outlook = "positive" | "negative" | "mixed";
export type Confidence = "low" | "medium" | "high";
export type Counts = { positive: number; negative: number; neutral: number };

export interface Bullet {
  side: "bull" | "bear";
  text: string; // Thai, or English where the translation failed the checks
  text_en: string | null;
  news_ids: number[];
}

export interface Card {
  id: number;
  created_at: string;
  title: string;
  summary: string | null;
  title_en: string | null;
  summary_en: string | null;
  outlook: Outlook;
  confidence: Confidence | null;
  bullets: Bullet[];
  counts: Counts | null;
  price_change_pct: number | null;
  news_ids: number[];
  model: string;
  prompt_version: string;
}

export interface Source {
  id: number;
  headline: string;
  headline_th: string | null;
  source: string | null;
  url: string | null;
  created_at: string;
  importance: number | null;
  sentiment: number | null;
}

export interface Digest {
  symbol: string;
  window_hours: number;
  card: Card | null;
  card_is_current: boolean;
  live: { counts: Counts; price_change_pct: number | null; news_ids: number[] };
  sources: Source[];
}

export const OUTLOOK_LABELS: Record<Outlook, { arrow: string; label: string }> = {
  positive: { arrow: "▲", label: "Positive" },
  negative: { arrow: "▼", label: "Negative" },
  mixed: { arrow: "◆", label: "Mixed" },
};

export const CONFIDENCE_LABELS: Record<Confidence, string> = {
  low: "low confidence",
  medium: "medium confidence",
  high: "high confidence",
};

/** Sources numbered [1], [2]… in the order the card first cites them, then the rest. */
export function numberSources(digest: Digest): { sources: Source[]; index: Map<number, number> } {
  const byId = new Map(digest.sources.map((s) => [s.id, s]));
  const order: number[] = [];
  const add = (id: number) => {
    if (byId.has(id) && !order.includes(id)) order.push(id);
  };
  for (const b of digest.card?.bullets ?? []) b.news_ids.forEach(add);
  digest.sources.forEach((s) => add(s.id));
  return {
    sources: order.map((id) => byId.get(id)!),
    index: new Map(order.map((id, i) => [id, i + 1])),
  };
}

/** Widths in % for the good / bad / neutral bar; empty counts give an empty bar. */
export function countShares(c: Counts): Counts {
  const total = c.positive + c.negative + c.neutral;
  if (!total) return { positive: 0, negative: 0, neutral: 0 };
  return {
    positive: (c.positive / total) * 100,
    negative: (c.negative / total) * 100,
    neutral: (c.neutral / total) * 100,
  };
}

export function sourceTitle(s: Source): string {
  return s.headline_th || s.headline;
}

/** "4 Nov, 10:25" in Thai time. */
export function formatCardTime(iso: string): string {
  return new Intl.DateTimeFormat("en-GB", {
    timeZone: DISPLAY_TZ,
    day: "numeric",
    month: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).format(new Date(iso));
}
