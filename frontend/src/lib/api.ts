import type { BarSession, ChartInterval } from "./market-time";
import type { NewsItem } from "./news";

export const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export type Interval = ChartInterval;
export const INTERVALS: Interval[] = ["1m", "5m", "1h", "1d"];

export interface Candle {
  symbol: string;
  bucket_start: string;
  open: number;
  high: number;
  low: number;
  close: number;
  volume: number;
  trade_count: number;
}

export interface Quote {
  symbol: string;
  price: number | null;
  price_at: string | null;
  prev_close: number | null;
  change: number | null;
  change_pct: number | null;
  day_open: number | null;
  day_high: number | null;
  day_low: number | null;
  day_volume: number | null;
}

export type Session = BarSession;

export interface MarketStatus {
  session: Session;
  next_session: Session | null;
  next_change_at: string | null;
}

export interface SymbolInfo {
  symbol: string;
  name: string | null;
  exchange: string | null;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${API_URL}${path}`);
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`);
  return res.json();
}

export function fetchCandles(symbol: string, interval: Interval, limit = 300): Promise<Candle[]> {
  const params = new URLSearchParams({ symbol, interval, limit: String(limit) });
  return getJson(`/candles?${params}`);
}

export const fetchQuotes = () => getJson<Quote[]>("/quotes");
export const fetchMarketStatus = () => getJson<MarketStatus>("/market/status");
export const fetchSymbolDetails = () => getJson<SymbolInfo[]>("/symbols/details");

/** Every article for `symbol`, less-relevant ones included (the panel filters locally). */
export function fetchNews(symbol: string, limit = 50): Promise<NewsItem[]> {
  const params = new URLSearchParams({ symbol, include_hidden: "true", limit: String(limit) });
  return getJson(`/news?${params}`);
}
