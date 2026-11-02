"use client";

import { useEffect, useState } from "react";

/** Calls `load` now and every `intervalMs`; keeps the last good value on failure. */
export function usePolling<T>(load: () => Promise<T>, intervalMs: number) {
  const [data, setData] = useState<T | null>(null);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const tick = () =>
      load()
        .then((value) => {
          if (cancelled) return;
          setData(value);
          setFailed(false);
        })
        .catch(() => {
          if (!cancelled) setFailed(true);
        });
    tick();
    const timer = setInterval(tick, intervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [load, intervalMs]);

  return { data, failed };
}
