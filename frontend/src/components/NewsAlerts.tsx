"use client";

import { useEffect, useRef, useState } from "react";

import { usePolling } from "@/hooks/usePolling";
import { fetchImportantNews } from "@/lib/api";
import { BELL_LIMIT, alertText, initialSeen, newToasts, unread } from "@/lib/alerts";
import { type NewsItem, formatNewsTime } from "@/lib/news";

const SEEN_KEY = "stock-streaming.newsSeenAt";
const TOAST_MS = 10_000;

function readSeen(): string | null {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch {
    return null;
  }
}

function writeSeen(value: string) {
  try {
    localStorage.setItem(SEEN_KEY, value);
  } catch {
    // private mode etc.: the bell just forgets between visits
  }
}

function AlertLine({ n, onSelect }: { n: NewsItem; onSelect: (symbol: string) => void }) {
  const symbol = n.symbols[0];
  return (
    <>
      <button type="button" className="alert-symbol" onClick={() => onSelect(symbol)}>
        {symbol}
      </button>
      <span className="alert-importance">{n.ai?.importance}/5</span>
      <time dateTime={n.created_at}>{formatNewsTime(n.created_at)}</time>
      {n.url ? (
        <a className="alert-text" lang="th" href={n.url} target="_blank" rel="noopener noreferrer">
          {alertText(n)}
        </a>
      ) : (
        <span className="alert-text" lang="th">{alertText(n)}</span>
      )}
    </>
  );
}

/** Bell with unread important news + toasts for ones arriving while the page is open. */
export default function NewsAlerts({ onSelect }: { onSelect: (symbol: string) => void }) {
  const news = usePolling(fetchImportantNews, 60_000);
  const [seenAt, setSeenAt] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [openedSince, setOpenedSince] = useState<string | null>(null); // highlight relative to this
  const [toasts, setToasts] = useState<NewsItem[]>([]);
  const known = useRef<Set<number> | null>(null);

  useEffect(() => {
    const items = news.data;
    if (!items) return;
    if (known.current === null) {
      // First load of this page: remember what's there, toast nothing (D-Q15).
      known.current = new Set(items.map((n) => n.id));
      const seen = initialSeen(items, readSeen());
      writeSeen(seen);
      setSeenAt(seen);
      return;
    }
    const fresh = newToasts(items, known.current, Date.now());
    fresh.forEach((n) => known.current!.add(n.id));
    items.forEach((n) => known.current!.add(n.id));
    if (fresh.length) setToasts((t) => [...fresh, ...t].slice(0, 3));
  }, [news.data]);

  useEffect(() => {
    if (!toasts.length) return;
    const timer = setTimeout(() => setToasts((t) => t.slice(0, -1)), TOAST_MS);
    return () => clearTimeout(timer);
  }, [toasts]);

  const pending = unread(news.data ?? [], seenAt);
  const recent = (news.data ?? []).filter((n) => !n.backfilled).slice(0, BELL_LIMIT);

  const toggle = () => {
    if (!open) setOpenedSince(seenAt);
    if (!open && news.data?.length) {
      const newest = news.data.map((n) => n.created_at).sort().at(-1)!;
      if (!seenAt || newest > seenAt) {
        writeSeen(newest);
        setSeenAt(newest);
      }
    }
    setOpen(!open);
  };
  const pick = (symbol: string) => {
    onSelect(symbol);
    setOpen(false);
  };

  return (
    <>
      <div className="bell">
        <button type="button" className="bell-button" onClick={toggle} aria-expanded={open}
                aria-label={`Important news${pending.length ? `: ${pending.length} new` : ""}`}
                title="Important news">
          🔔
          {pending.length > 0 && <span className="bell-count">{pending.length}</span>}
        </button>
        {open && (
          <div className="bell-menu" role="dialog" aria-label="Important news">
            <p className="bell-title">Important news (4–5) · all symbols</p>
            {recent.length ? (
              <ul>
                {recent.map((n) => (
                  <li key={n.id}
                      data-new={unread([n], openedSince).length > 0 || undefined}>
                    <AlertLine n={n} onSelect={pick} />
                  </li>
                ))}
              </ul>
            ) : (
              <p className="bell-empty">Nothing important lately.</p>
            )}
          </div>
        )}
      </div>
      {toasts.length > 0 && (
        <div className="toasts" role="status" aria-live="polite">
          {toasts.map((n) => (
            <div key={n.id} className="toast">
              <AlertLine n={n} onSelect={pick} />
              <button type="button" className="toast-close" aria-label="Dismiss"
                      onClick={() => setToasts((t) => t.filter((x) => x.id !== n.id))}>×</button>
            </div>
          ))}
        </div>
      )}
    </>
  );
}
