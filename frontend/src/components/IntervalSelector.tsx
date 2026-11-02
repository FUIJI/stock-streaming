"use client";

import { INTERVALS, type Interval } from "@/lib/api";

export default function IntervalSelector({
  value,
  onChange,
}: {
  value: Interval;
  onChange: (interval: Interval) => void;
}) {
  return (
    <div className="interval-selector" role="group" aria-label="Candle interval">
      {INTERVALS.map((interval) => (
        <button
          key={interval}
          type="button"
          data-active={interval === value || undefined}
          onClick={() => onChange(interval)}
        >
          {interval}
        </button>
      ))}
    </div>
  );
}
