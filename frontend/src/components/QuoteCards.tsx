"use client";

import type { Quote, SymbolInfo } from "@/lib/api";
import { shortCompanyName } from "@/lib/format";

function signed(value: number, digits: number) {
  return `${value > 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export default function QuoteCards({
  symbols,
  details,
  quotes,
  selected,
  onSelect,
}: {
  symbols: string[];
  details: Record<string, SymbolInfo>;
  quotes: Record<string, Quote>;
  selected: string;
  onSelect: (symbol: string) => void;
}) {
  return (
    <nav className="quote-cards" aria-label="Symbols">
      {symbols.map((symbol) => {
        const q = quotes[symbol];
        const change = q?.change ?? null;
        const trend = change === null || change === 0 ? "flat" : change > 0 ? "up" : "down";
        return (
          <button
            key={symbol}
            type="button"
            className="quote-card"
            data-active={symbol === selected || undefined}
            data-trend={trend}
            onClick={() => onSelect(symbol)}
          >
            <span className="quote-symbol">{symbol}</span>
            <span className="quote-name">{shortCompanyName(details[symbol]?.name) ?? "\u00a0"}</span>
            <span className="quote-price">{q?.price != null ? q.price.toFixed(2) : "—"}</span>
            <span className="quote-change">
              {change !== null && q?.change_pct != null
                ? `${signed(change, 2)} (${signed(q.change_pct, 2)}%)`
                : "—"}
            </span>
          </button>
        );
      })}
    </nav>
  );
}
