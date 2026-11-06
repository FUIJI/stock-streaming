"use client";

import {
  CandlestickData,
  CandlestickSeries,
  HistogramData,
  HistogramSeries,
  ISeriesApi,
  ISeriesMarkersPluginApi,
  SeriesMarker,
  Time,
  UTCTimestamp,
  createChart,
  createSeriesMarkers,
} from "lightweight-charts";
import { type ReactNode, useCallback, useEffect, useRef, useState } from "react";

import { usePolling } from "@/hooks/usePolling";
import { type StreamStatus, type Trade, useTradeStream } from "@/hooks/useTradeStream";
import { type Candle, type Interval, type Session, fetchCandles, fetchMarkerNews } from "@/lib/api";
import { type NewsMarker, buildMarkers } from "@/lib/markers";
import { formatNewsTime } from "@/lib/news";
import {
  type Ohlc,
  barSession,
  formatDisplayTime,
  formatTick,
  mergeTrade,
} from "@/lib/market-time";

interface Palette {
  up: string;
  down: string;
  upFaded: string;
  downFaded: string;
  upVolume: string;
  downVolume: string;
  text: string;
  grid: string;
  border: string;
  neutral: string;
}

function withAlpha(hex: string, alpha: number) {
  const n = parseInt(hex.trim().slice(1), 16);
  return `rgba(${(n >> 16) & 255}, ${(n >> 8) & 255}, ${n & 255}, ${alpha})`;
}

/** Chart colors come from the CSS theme tokens in globals.css. */
function readPalette(): Palette {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  const up = v("--up");
  const down = v("--down");
  return {
    up,
    down,
    // Pre/post-market candles are drawn faded (U2 in docs/specs/phase-3a-frontend.md).
    upFaded: withAlpha(up, 0.4),
    downFaded: withAlpha(down, 0.4),
    upVolume: withAlpha(up, 0.35),
    downVolume: withAlpha(down, 0.35),
    text: v("--muted"),
    grid: v("--grid"),
    border: v("--border"),
    neutral: v("--extended"),
  };
}

type LoadState = "loading" | "ready" | "empty" | "error";

interface Tip {
  x: number;
  y: number;
  marker: NewsMarker;
}

function toSeriesMarkers(markers: NewsMarker[], p: Palette): SeriesMarker<Time>[] {
  const color = { positive: p.up, negative: p.down, neutral: p.neutral };
  return markers.map((m) => ({
    time: m.time as UTCTimestamp,
    position: "aboveBar",
    shape: "circle",
    color: color[m.tone],
    size: m.big ? 2 : 1,
  }));
}

function NewsTip({ tip }: { tip: Tip }) {
  const shown = tip.marker.items.slice(0, 3);
  const more = tip.marker.items.length - shown.length;
  return (
    <div className="chart-tip" style={{ left: tip.x, top: tip.y }} role="tooltip">
      {shown.map((n) => (
        <p key={n.id}>
          <span className="chart-tip-meta">
            {n.ai?.importance}/5 · {formatNewsTime(n.created_at)}
          </span>
          <span lang="th">{n.ai?.headline_th || n.headline}</span>
        </p>
      ))}
      {more > 0 && <p className="chart-tip-meta">+{more} more</p>}
    </div>
  );
}

function toOhlc(c: Candle): Ohlc {
  return {
    time: Date.parse(c.bucket_start) / 1000,
    open: c.open,
    high: c.high,
    low: c.low,
    close: c.close,
    volume: c.volume,
  };
}

function styled(bar: Ohlc, interval: Interval, p: Palette) {
  const up = bar.close >= bar.open;
  const extended = interval !== "1d" && barSession(bar.time) !== "regular";
  const color = extended ? (up ? p.upFaded : p.downFaded) : up ? p.up : p.down;
  const time = bar.time as UTCTimestamp;
  const candle: CandlestickData<Time> = {
    time, open: bar.open, high: bar.high, low: bar.low, close: bar.close,
    color, wickColor: color, borderColor: color,
  };
  const volume: HistogramData<Time> = {
    time, value: bar.volume, color: up ? p.upVolume : p.downVolume,
  };
  return { candle, volume };
}

function liveLabel(stream: StreamStatus, session: Session | null) {
  if (stream === "connecting") return { tone: "pending", text: "Connecting…" };
  if (stream === "closed") return { tone: "down", text: "Reconnecting…" };
  if (session === "closed") return { tone: "idle", text: "Connected · market closed" };
  return { tone: "live", text: "Live" };
}

export default function CandlestickChart({
  symbol,
  name,
  interval,
  session,
  prevClose,
  theme,
  toolbar,
}: {
  symbol: string;
  name?: string | null;
  interval: Interval;
  session: Session | null;
  prevClose?: number | null; // colors the headline price like the quote cards do
  theme: string; // recreates the chart with the new palette when it changes
  toolbar?: ReactNode;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const candlesRef = useRef<ISeriesApi<"Candlestick"> | null>(null);
  const volumeRef = useRef<ISeriesApi<"Histogram"> | null>(null);
  const lastBarRef = useRef<Ohlc | null>(null);
  const paletteRef = useRef<Palette | null>(null);
  const [lastPrice, setLastPrice] = useState<number | null>(null);
  const [load, setLoad] = useState<LoadState>("loading");
  const markersApiRef = useRef<ISeriesMarkersPluginApi<Time> | null>(null);
  const markersRef = useRef<Map<number, NewsMarker>>(new Map());
  const [candleTimes, setCandleTimes] = useState<number[]>([]);
  const [tip, setTip] = useState<Tip | null>(null);
  const loadNews = useCallback(() => fetchMarkerNews(symbol), [symbol]);
  const news = usePolling(loadNews, 60_000);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;

    const palette = readPalette();
    paletteRef.current = palette;
    const chart = createChart(container, {
      autoSize: true,
      layout: {
        background: { color: "transparent" },
        textColor: palette.text,
        attributionLogo: false,
      },
      grid: {
        vertLines: { color: palette.grid },
        horzLines: { color: palette.grid },
      },
      localization: {
        timeFormatter: (time: Time) => formatDisplayTime(time as number, interval),
      },
      timeScale: {
        timeVisible: interval !== "1d",
        secondsVisible: false,
        borderColor: palette.border,
        tickMarkFormatter: (time: Time, kind: number) => formatTick(time as number, kind),
      },
      rightPriceScale: { borderColor: palette.border, scaleMargins: { top: 0.08, bottom: 0.25 } },
    });
    const candles = chart.addSeries(CandlestickSeries, {
      upColor: palette.up,
      downColor: palette.down,
      wickUpColor: palette.up,
      wickDownColor: palette.down,
      borderVisible: false,
    });
    const volume = chart.addSeries(HistogramSeries, {
      priceScaleId: "volume",
      priceFormat: { type: "volume" },
      lastValueVisible: false,
      priceLineVisible: false,
    });
    chart.priceScale("volume").applyOptions({ scaleMargins: { top: 0.8, bottom: 0 } });
    markersApiRef.current = createSeriesMarkers(candles, []);
    markersRef.current = new Map();
    setCandleTimes([]);
    setTip(null);
    // Pointing at a candle with news shows its headlines; clicking jumps to the article in
    // the outlook card when it's one of its sources, else opens the original.
    chart.subscribeCrosshairMove((param) => {
      const marker = param.time === undefined ? undefined : markersRef.current.get(param.time as number);
      setTip(marker && param.point ? { x: param.point.x, y: param.point.y, marker } : null);
    });
    chart.subscribeClick((param) => {
      const marker = param.time === undefined ? undefined : markersRef.current.get(param.time as number);
      const first = marker?.items[0];
      if (!first) return;
      const inCard = document.getElementById(`source-${first.id}`);
      if (inCard) inCard.scrollIntoView({ behavior: "smooth", block: "center" });
      else if (first.url) window.open(first.url, "_blank", "noopener,noreferrer");
    });
    candlesRef.current = candles;
    volumeRef.current = volume;
    lastBarRef.current = null;
    setLastPrice(null);
    setLoad("loading");

    let cancelled = false;
    fetchCandles(symbol, interval, 300)
      .then((rows) => {
        if (cancelled) return;
        const bars = rows.map(toOhlc);
        const drawn = bars.map((bar) => styled(bar, interval, palette));
        candles.setData(drawn.map((d) => d.candle));
        volume.setData(drawn.map((d) => d.volume));
        lastBarRef.current = bars[bars.length - 1] ?? null;
        setCandleTimes(bars.map((b) => b.time));
        setLastPrice(lastBarRef.current?.close ?? null);
        setLoad(bars.length ? "ready" : "empty");
        chart.timeScale().fitContent();
      })
      .catch(() => {
        if (!cancelled) setLoad("error");
      });

    return () => {
      cancelled = true;
      candlesRef.current = null;
      volumeRef.current = null;
      markersApiRef.current = null;
      chart.remove();
    };
  }, [symbol, interval, theme]);

  useEffect(() => {
    const api = markersApiRef.current;
    const palette = paletteRef.current;
    if (!api || !palette) return;
    const markers = buildMarkers(news.data ?? [], candleTimes, interval, symbol);
    markersRef.current = new Map(markers.map((m) => [m.time, m]));
    api.setMarkers(toSeriesMarkers(markers, palette));
  }, [news.data, candleTimes, interval, symbol]);

  const stream = useTradeStream(symbol, (trade: Trade) => {
    const candles = candlesRef.current;
    const volume = volumeRef.current;
    const palette = paletteRef.current;
    if (!candles || !volume || !palette || load === "loading") return;
    const bar = mergeTrade(lastBarRef.current, trade, interval);
    if (!bar) return; // older than the newest candle
    lastBarRef.current = bar;
    const drawn = styled(bar, interval, palette);
    candles.update(drawn.candle);
    volume.update(drawn.volume);
    setLastPrice(trade.price);
    setLoad("ready");
  });

  const live = liveLabel(stream, session);
  const direction =
    lastPrice === null || prevClose == null || lastPrice === prevClose
      ? "flat"
      : lastPrice > prevClose ? "up" : "down";

  return (
    <section className="chart-card">
      <header className="chart-header">
        <div className="chart-title">
          <h2>{symbol}</h2>
          {name && <span className="chart-name">{name}</span>}
        </div>
        {lastPrice !== null && (
          <span className={`price price-${direction}`}>{lastPrice.toFixed(2)}</span>
        )}
        <span className="live-status" data-tone={live.tone}>
          <span className="live-dot" />
          {live.text}
        </span>
        {toolbar}
      </header>
      <div className="chart-body">
        <div ref={containerRef} className="chart-container" />
        {tip && <NewsTip tip={tip} />}
        {load !== "ready" && (
          <p className="chart-overlay" data-state={load}>
            {load === "loading" && "Loading candles…"}
            {load === "empty" &&
              `No ${interval} candles for ${symbol} yet. They appear as soon as trades arrive.`}
            {load === "error" && "Couldn't load candles from the API. Try again in a moment."}
          </p>
        )}
      </div>
      <footer className="chart-legend">
        <span>Times in ICT (UTC+7)</span>
        <span>
          <span className="legend-dot" /> News (importance 3+): point to read, click to open
        </span>
        {interval !== "1d" && (
          <span>
            <span className="legend-swatch" /> Faded candles: pre-market / after-hours
          </span>
        )}
      </footer>
    </section>
  );
}
