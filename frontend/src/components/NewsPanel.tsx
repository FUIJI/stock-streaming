"use client";

import { useCallback, useState } from "react";

import { usePolling } from "@/hooks/usePolling";
import { fetchNews } from "@/lib/api";
import {
  CATEGORY_LABELS,
  type NewsItem,
  filterNews,
  formatNewsTime,
  formatPct,
  sentimentFor,
  sentimentTone,
} from "@/lib/news";

function Reaction({ item, symbol }: { item: NewsItem; symbol: string }) {
  const r = item.reaction[symbol];
  if (!r || (r.m5 === null && r.m30 === null)) {
    return <span className="news-reaction" data-state="waiting">Price: waiting for market</span>;
  }
  const part = (label: string, pct: number | null) =>
    pct === null ? (
      <span>{label} …</span>
    ) : (
      <span className={pct > 0 ? "price-up" : pct < 0 ? "price-down" : undefined}>
        {label} {formatPct(pct)}
      </span>
    );
  return (
    <span className="news-reaction" title="Price change after the article">
      {part("5m", r.m5)} {part("30m", r.m30)}
    </span>
  );
}

function NewsEntry({ item, symbol }: { item: NewsItem; symbol: string }) {
  const ai = item.ai;
  const tone = sentimentTone(sentimentFor(item, symbol));
  return (
    <li className="news-item" data-hidden={item.hidden || undefined}>
      <div className="news-meta">
        <time dateTime={item.created_at}>{formatNewsTime(item.created_at)}</time>
        {ai && (
          <span className="news-importance" data-level={ai.importance}
                title={`Importance ${ai.importance}/5`}>
            {ai.importance}/5
          </span>
        )}
        {ai && <span className="news-tone" data-tone={tone} title={`Sentiment: ${tone}`} />}
        {ai && <span className="news-category">{CATEGORY_LABELS[ai.category] ?? ai.category}</span>}
      </div>

      {ai?.summary_th || ai?.headline_th ? (
        <>
          {/* Older articles have a full summary (Phase 3c); newer ones a one-line headline. */}
          <p className="news-summary" lang="th">{ai.summary_th ?? ai.headline_th}</p>
          {ai.summary_th && ai.why_it_matters && (
            <p className="news-why" lang="th">{ai.why_it_matters}</p>
          )}
        </>
      ) : (
        item.summary && <p className="news-summary">{item.summary}</p>
      )}

      {item.url ? (
        <a className="news-headline" href={item.url} target="_blank" rel="noopener noreferrer">
          {item.headline}
        </a>
      ) : (
        <span className="news-headline">{item.headline}</span>
      )}

      <div className="news-footer">
        <Reaction item={item} symbol={symbol} />
        <span className="news-source">
          {ai
            ? `AI summary · ${ai.model}${item.ai_status === "pending" ? " · updating" : ""}`
            : item.ai_status === "failed"
              ? "AI summary unavailable"
              : "AI summary pending"}
        </span>
      </div>
    </li>
  );
}

export default function NewsPanel({ symbol }: { symbol: string }) {
  const load = useCallback(() => fetchNews(symbol), [symbol]);
  const news = usePolling(load, 60_000);
  const [importantOnly, setImportantOnly] = useState(false);
  const [showHidden, setShowHidden] = useState(false);

  // usePolling keeps the previous symbol's list until the new one loads; don't show it.
  const items = (news.data ?? []).filter((n) => n.symbols.includes(symbol));
  const { shown, hiddenCount } = filterNews(items, { importantOnly, showHidden });

  return (
    <section className="news-panel" aria-label={`News for ${symbol}`}>
      <header className="news-header">
        <h2>News · {symbol}</h2>
        <div className="interval-selector" role="group" aria-label="News filter">
          <button type="button" data-active={!importantOnly || undefined}
                  onClick={() => setImportantOnly(false)}>All</button>
          <button type="button" data-active={importantOnly || undefined}
                  onClick={() => setImportantOnly(true)}>Important</button>
        </div>
      </header>

      {news.data === null ? (
        <p className="news-empty">{news.failed ? "News is unavailable right now." : "Loading news…"}</p>
      ) : shown.length === 0 ? (
        <p className="news-empty">
          {importantOnly ? "No important news lately." : "No news for this symbol yet."}
        </p>
      ) : (
        <ul className="news-list">
          {shown.map((item) => <NewsEntry key={item.id} item={item} symbol={symbol} />)}
        </ul>
      )}

      {hiddenCount > 0 && (
        <button type="button" className="news-more" onClick={() => setShowHidden((v) => !v)}>
          {showHidden
            ? "Hide less-relevant news"
            : `Show ${hiddenCount} less-relevant article${hiddenCount > 1 ? "s" : ""}`}
        </button>
      )}
    </section>
  );
}
