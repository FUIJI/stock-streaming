"use client";

import { useMemo, useState } from "react";

import CandlestickChart from "@/components/CandlestickChart";
import IntervalSelector from "@/components/IntervalSelector";
import MarketStatusBadge from "@/components/MarketStatusBadge";
import NewsAlerts from "@/components/NewsAlerts";
import OutlookCard from "@/components/OutlookCard";
import QuoteCards from "@/components/QuoteCards";
import { usePolling } from "@/hooks/usePolling";
import { useTheme } from "@/hooks/useTheme";
import {
  type Interval,
  type Quote,
  type SymbolInfo,
  fetchMarketStatus,
  fetchQuotes,
  fetchSymbolDetails,
} from "@/lib/api";
import { shortCompanyName } from "@/lib/format";

const DEFAULT_SYMBOLS = ["AAPL", "MSFT", "NVDA", "TSLA"];

function bySymbol<T extends { symbol: string }>(rows: T[] | null): Record<string, T> {
  return Object.fromEntries((rows ?? []).map((row) => [row.symbol, row]));
}

export default function Home() {
  const market = usePolling(fetchMarketStatus, 60_000);
  const quotes = usePolling(fetchQuotes, 15_000);
  const symbolInfo = usePolling(fetchSymbolDetails, 300_000);

  const symbols = symbolInfo.data?.length
    ? symbolInfo.data.map((s) => s.symbol)
    : DEFAULT_SYMBOLS;
  const details = useMemo(() => bySymbol<SymbolInfo>(symbolInfo.data), [symbolInfo.data]);
  const quoteMap = useMemo(() => bySymbol<Quote>(quotes.data), [quotes.data]);

  const [chosen, setChosen] = useState(DEFAULT_SYMBOLS[0]);
  const symbol = symbols.includes(chosen) ? chosen : symbols[0];
  const [interval, setChartInterval] = useState<Interval>("1m");
  const [theme, toggleTheme] = useTheme();

  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <div>
          <h1>Stock Streaming</h1>
          <p>US equities incl. pre-market &amp; after-hours · live trades via Alpaca (IEX)</p>
        </div>
        <div className="header-actions">
          <MarketStatusBadge status={market.data} failed={market.failed} />
          <NewsAlerts onSelect={setChosen} />
          <button
            type="button"
            className="theme-toggle"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Light theme" : "Dark theme"}
          >
            {theme === "dark" ? "☀" : "☾"}
          </button>
        </div>
      </header>

      <QuoteCards
        symbols={symbols}
        details={details}
        quotes={quoteMap}
        selected={symbol}
        onSelect={setChosen}
      />
      {quotes.failed && !quotes.data && (
        <p className="notice">Prices are unavailable right now; the chart below still works.</p>
      )}

      <div className="main-grid">
        <CandlestickChart
          symbol={symbol}
          name={shortCompanyName(details[symbol]?.name)}
          interval={interval}
          session={market.data?.session ?? null}
          prevClose={quoteMap[symbol]?.prev_close}
          theme={theme}
          toolbar={<IntervalSelector value={interval} onChange={setChartInterval} />}
        />
        <OutlookCard symbol={symbol} />
      </div>
    </main>
  );
}
