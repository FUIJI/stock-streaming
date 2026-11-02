"use client";

import { useEffect, useRef, useState } from "react";

import { API_URL } from "@/lib/api";

export interface Trade {
  symbol: string;
  price: number;
  volume: number;
  ts: number;
}

export type StreamStatus = "connecting" | "open" | "closed";

const WS_BASE = (process.env.NEXT_PUBLIC_WS_URL ?? API_URL).replace(/^http/, "ws");

/** Subscribes to live trades for a symbol; reconnects automatically. */
export function useTradeStream(symbol: string, onTrade: (trade: Trade) => void): StreamStatus {
  const handlerRef = useRef(onTrade);
  const [status, setStatus] = useState<StreamStatus>("connecting");
  useEffect(() => {
    handlerRef.current = onTrade;
  });

  useEffect(() => {
    let ws: WebSocket | null = null;
    let retryTimer: ReturnType<typeof setTimeout> | undefined;
    let disposed = false;

    const connect = () => {
      setStatus("connecting");
      ws = new WebSocket(`${WS_BASE}/ws/${encodeURIComponent(symbol)}`);
      ws.onopen = () => setStatus("open");
      ws.onmessage = (event) => handlerRef.current(JSON.parse(event.data));
      ws.onclose = () => {
        if (disposed) return;
        setStatus("closed");
        retryTimer = setTimeout(connect, 3000);
      };
    };
    connect();

    return () => {
      disposed = true;
      clearTimeout(retryTimer);
      ws?.close();
    };
  }, [symbol]);

  return status;
}
