// News alerts: toast + bell (Phase 3d, D7). No "@/" imports: `npm test` runs this directly.
//
// Alpaca news ids are not in time order across sources (fiscal.ai ids are far above
// Benzinga's), so "seen" is tracked by the article's time, not its id.
import { IMPORTANT, type NewsItem } from "./news.ts";

export const TOAST_MAX_AGE_MS = 2 * 3600_000; // a story re-scored later is old news by then
export const BELL_LIMIT = 10;

/** Worth an alert: important, scored by the AI, and not part of the first-start backfill. */
export function alertable(items: NewsItem[]): NewsItem[] {
  return items
    .filter((n) => !n.backfilled && n.ai !== null && n.ai.importance >= IMPORTANT)
    .sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}

/** Bell count: important articles newer than what the reader last opened the bell for. */
export function unread(items: NewsItem[], lastSeenAt: string | null): NewsItem[] {
  const since = lastSeenAt ? Date.parse(lastSeenAt) : Infinity;
  return alertable(items).filter((n) => Date.parse(n.created_at) > since);
}

/** On a first visit ever, start from the newest article so the bell doesn't open at 30. */
export function initialSeen(items: NewsItem[], stored: string | null): string {
  if (stored) return stored;
  return alertable(items)[0]?.created_at ?? new Date(0).toISOString();
}

/** Articles to toast now: new since this page loaded, not toasted yet, and fresh. */
export function newToasts(items: NewsItem[], known: Set<number>, nowMs: number): NewsItem[] {
  return alertable(items).filter(
    (n) => !known.has(n.id) && nowMs - Date.parse(n.created_at) <= TOAST_MAX_AGE_MS,
  );
}

export function alertText(n: NewsItem): string {
  return n.ai?.headline_th || n.headline;
}
