"use client";

import { useEffect, useState } from "react";

import type { MarketStatus, Session } from "@/lib/api";
import { formatClock, formatCountdown } from "@/lib/market-time";

const LABEL: Record<Session, string> = {
  pre: "Pre-market",
  regular: "Market open",
  post: "After-hours",
  closed: "Market closed",
};

// What the next change means, seen from the current session.
const NEXT: Record<Session, string> = {
  pre: "Opens",
  regular: "Closes",
  post: "After-hours ends",
  closed: "Pre-market starts",
};

export default function MarketStatusBadge({
  status,
  failed,
}: {
  status: MarketStatus | null;
  failed: boolean;
}) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(timer);
  }, []);

  if (!status) {
    return (
      <div className="market-badge" data-session="unknown">
        <span className="session-dot" />
        <span>{failed ? "Market status unavailable" : "Checking market…"}</span>
      </div>
    );
  }

  const next = status.next_change_at;
  return (
    <div className="market-badge" data-session={status.session}>
      <span className="session-dot" />
      <strong>{LABEL[status.session]}</strong>
      {next && (
        <span className="market-next">
          {NEXT[status.session]} {formatClock(next)} ·{" "}
          in {formatCountdown(Date.parse(next) - now)}
        </span>
      )}
    </div>
  );
}
