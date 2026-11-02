"""Per-IP REST rate limiting and WebSocket connection caps (in memory, one process)."""

import time

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send


class RateLimiter:
    """Token bucket per key: `per_minute` requests, refilled continuously."""

    def __init__(self, per_minute: int):
        self.capacity = float(per_minute)
        self.rate = per_minute / 60.0
        self._buckets: dict[str, tuple[float, float]] = {}  # key -> (tokens, last time)

    def allow(self, key: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        tokens, last = self._buckets.get(key, (self.capacity, now))
        tokens = min(self.capacity, tokens + (now - last) * self.rate)
        allowed = tokens >= 1
        self._buckets[key] = (tokens - 1 if allowed else tokens, now)
        if len(self._buckets) > 10_000:  # forget clients idle long enough to be full again
            idle = self.capacity / self.rate
            self._buckets = {k: v for k, v in self._buckets.items() if now - v[1] < idle}
        return allowed


class ConnectionLimiter:
    def __init__(self, max_total: int, max_per_ip: int):
        self.max_total = max_total
        self.max_per_ip = max_per_ip
        self.total = 0
        self._per_ip: dict[str, int] = {}

    def acquire(self, ip: str) -> bool:
        if self.total >= self.max_total or self._per_ip.get(ip, 0) >= self.max_per_ip:
            return False
        self.total += 1
        self._per_ip[ip] = self._per_ip.get(ip, 0) + 1
        return True

    def release(self, ip: str) -> None:
        self.total -= 1
        remaining = self._per_ip.get(ip, 1) - 1
        if remaining:
            self._per_ip[ip] = remaining
        else:
            self._per_ip.pop(ip, None)


class RateLimitMiddleware:
    """429 once an IP exceeds app.state.rate_limiter; /health* is exempt (monitors)."""

    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        limiter = getattr(scope["app"].state, "rate_limiter", None) if "app" in scope else None
        if (scope["type"] == "http" and limiter is not None
                and not scope["path"].startswith("/health")):
            ip = scope["client"][0] if scope.get("client") else "unknown"
            if not limiter.allow(ip):
                response = JSONResponse({"detail": "Too many requests"}, status_code=429,
                                        headers={"Retry-After": "10"})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)
