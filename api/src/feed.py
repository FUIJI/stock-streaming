"""What the live trade feed is doing, for /health and /health/feed."""

import time
from dataclasses import dataclass, field


@dataclass
class FeedStats:
    connections: int = 0  # ingestion sockets (lite) or running Kafka consumers (full stack)
    trades_total: int = 0
    last_trade_at: float | None = None  # wall clock (time.time()) when last received
    started_at: float = field(default_factory=time.time)
    last_trades: dict[str, tuple[float, int]] = field(default_factory=dict)  # (price, ts ms)

    def record_trade(self, symbol: str, price: float, ts: int) -> None:
        self.trades_total += 1
        self.last_trade_at = time.time()
        previous = self.last_trades.get(symbol)
        if previous is None or ts >= previous[1]:
            self.last_trades[symbol] = (price, ts)

    def seconds_since_trade(self, now: float | None = None) -> float:
        """Since the last trade, or since startup if none arrived yet."""
        now = time.time() if now is None else now
        return now - (self.last_trade_at or self.started_at)

    def problem(self, session: str | None, stale_after: float, now: float | None = None) -> str | None:
        """Why the feed looks unhealthy, or None.

        Silence only counts during the regular session: IEX can be quiet for long
        stretches in pre/post-market, and nothing trades while the market is closed.
        """
        if self.connections == 0:
            return "no trade feed connected"
        idle = self.seconds_since_trade(now)
        if session == "regular" and idle > stale_after:
            return f"no trades for {idle:.0f}s during the regular session"
        return None
