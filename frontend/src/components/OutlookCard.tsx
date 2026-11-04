"use client";

import { useCallback } from "react";

import { usePolling } from "@/hooks/usePolling";
import { fetchDigest } from "@/lib/api";
import {
  type Bullet,
  CONFIDENCE_LABELS,
  type Counts,
  type Digest,
  OUTLOOK_LABELS,
  countShares,
  formatCardTime,
  numberSources,
  sourceTitle,
} from "@/lib/digest";
import { formatPct, sentimentTone } from "@/lib/news";

function CountBar({ counts, pct }: { counts: Counts; pct: number | null }) {
  const share = countShares(counts);
  return (
    <div className="outlook-counts">
      <div className="count-bar" aria-hidden="true">
        <span data-tone="positive" style={{ width: `${share.positive}%` }} />
        <span data-tone="negative" style={{ width: `${share.negative}%` }} />
        <span data-tone="neutral" style={{ width: `${share.neutral}%` }} />
      </div>
      <p>
        <span className="price-up">{counts.positive} good</span> ·{" "}
        <span className="price-down">{counts.negative} bad</span> · {counts.neutral} neutral
        {pct !== null && (
          <>
            {" "}· price over the period{" "}
            <span className={pct > 0 ? "price-up" : pct < 0 ? "price-down" : undefined}>
              {formatPct(pct)}
            </span>
          </>
        )}
      </p>
    </div>
  );
}

function Factors({ title, side, bullets, index }: {
  title: string; side: "bull" | "bear"; bullets: Bullet[]; index: Map<number, number>;
}) {
  const mine = bullets.filter((b) => b.side === side);
  if (!mine.length) return null;
  return (
    <div className="outlook-factors" data-side={side}>
      <h3>{title}</h3>
      <ul>
        {mine.map((b, i) => (
          <li key={i}>
            <span lang={b.text === b.text_en ? "en" : "th"}>{b.text}</span>
            {b.news_ids.filter((id) => index.has(id)).map((id) => (
              <a key={id} className="cite" href={`#source-${id}`}>[{index.get(id)}]</a>
            ))}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CardBody({ digest }: { digest: Digest }) {
  const { card, live } = digest;
  const { sources, index } = numberSources(digest);
  return (
    <>
      {card ? (
        <>
          <div className="outlook-verdict" data-outlook={card.outlook}>
            <span className="verdict-arrow">{OUTLOOK_LABELS[card.outlook].arrow}</span>
            <div>
              <strong>{OUTLOOK_LABELS[card.outlook].label}</strong>
              <span> next 3 trading days</span>
              {card.confidence && <small>{CONFIDENCE_LABELS[card.confidence]}</small>}
            </div>
          </div>
          {!digest.card_is_current && (
            <p className="outlook-note">New articles since this card — it updates next round.</p>
          )}
        </>
      ) : (
        <p className="outlook-note">
          {live.news_ids.length
            ? "No AI outlook yet — the numbers and sources below are live."
            : `No news that matters for ${digest.symbol} in the last ${digest.window_hours} hours.`}
        </p>
      )}

      {live.news_ids.length > 0 && <CountBar counts={live.counts} pct={live.price_change_pct} />}

      {card && (
        <div className="outlook-text">
          <h3 lang="th">{card.title}</h3>
          {card.summary && <p lang="th">{card.summary}</p>}
          <Factors title="Supporting" side="bull" bullets={card.bullets} index={index} />
          <Factors title="Weighing" side="bear" bullets={card.bullets} index={index} />
        </div>
      )}

      {sources.length > 0 && (
        <ol className="outlook-sources">
          {sources.map((s) => (
            <li key={s.id} id={`source-${s.id}`}>
              <span className="source-num">{index.get(s.id)}</span>
              <span className="news-tone" data-tone={sentimentTone(s.sentiment)} />
              <div>
                {s.url ? (
                  <a href={s.url} target="_blank" rel="noopener noreferrer">{sourceTitle(s)} ↗</a>
                ) : (
                  <span>{sourceTitle(s)}</span>
                )}
                <small>{s.source ?? "news"} · {formatCardTime(s.created_at)}</small>
              </div>
            </li>
          ))}
        </ol>
      )}

      <p className="outlook-footer">
        {card ? `AI outlook · ${card.model} · updated ${formatCardTime(card.created_at)} · ` : ""}
        From news only — not investment advice.
      </p>
    </>
  );
}

export default function OutlookCard({ symbol }: { symbol: string }) {
  const load = useCallback(() => fetchDigest(symbol), [symbol]);
  const digest = usePolling(load, 60_000);
  // usePolling keeps the previous symbol's card until the new one loads; don't show it.
  const data = digest.data?.symbol === symbol ? digest.data : null;
  return (
    <section className="outlook-card" aria-label={`News outlook for ${symbol}`}>
      <header className="outlook-header">
        <h2>News outlook · {symbol}</h2>
        <span>last {data?.window_hours ?? 72}h</span>
      </header>
      <div className="outlook-scroll">
        {data ? (
          <CardBody digest={data} />
        ) : (
          <p className="outlook-note">{digest.failed ? "Outlook unavailable right now." : "Loading…"}</p>
        )}
      </div>
    </section>
  );
}
