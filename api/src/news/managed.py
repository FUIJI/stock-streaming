"""The provider the worker actually uses: our own quota in front of a primary model,
with a fallback model for when the primary is unavailable (docs/specs/phase-3c-news.md Q12).
"""

import asyncio
import os
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .llm import GEMINI_URL, GeminiProvider, LLMError, LLMProvider


class QuotaExhausted(LLMError):
    """Our daily cap is spent; nothing is sent until `resets_at`."""

    def __init__(self, resets_at: datetime):
        super().__init__(f"daily LLM call limit reached; resets at {resets_at.isoformat()}",
                         retryable=True)
        self.resets_at = resets_at


def next_utc_midnight(now: datetime) -> datetime:
    return datetime.combine(now.date() + timedelta(days=1), datetime.min.time(), tzinfo=UTC)


@dataclass
class Quota:
    """Calls per UTC day (hard stop) and per minute (waits). Kept in memory: a restart
    only forgets today's count, and the cap sits well below the provider's own."""

    daily_limit: int
    per_minute: int
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    monotonic: Callable[[], float] = time.monotonic
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep
    calls_today: int = 0
    _day: str = ""
    _recent: deque = field(default_factory=deque)

    def _roll(self, now: datetime) -> None:
        if now.date().isoformat() != self._day:
            self._day, self.calls_today = now.date().isoformat(), 0

    def remaining(self) -> int:
        """Calls left today (the count resets at UTC midnight even with no call made)."""
        self._roll(self.clock())
        return self.daily_limit - self.calls_today

    async def acquire(self) -> None:
        now = self.clock()
        self._roll(now)
        if self.calls_today >= self.daily_limit:
            raise QuotaExhausted(next_utc_midnight(now))
        while True:
            t = self.monotonic()
            while self._recent and t - self._recent[0] >= 60:
                self._recent.popleft()
            if len(self._recent) < self.per_minute:
                break
            await self.sleep(60 - (t - self._recent[0]))
        self._recent.append(self.monotonic())
        self.calls_today += 1


class ManagedProvider:
    """Tries each model in order; moves on only when one is *unavailable* (overload,
    timeout). A 429 is not passed on: it means our key is over a limit, which waiting
    fixes, and the fallback's tiny daily quota (Flash: 20) would just be burned with it.
    Bad output is returned to the analyzer, which retries smaller."""

    name = "managed"

    def __init__(self, providers: list[LLMProvider], quota: Quota):
        self.providers = providers
        self.quota = quota
        self.model = providers[0].model
        self.last_model: str | None = None
        self.last_error: str | None = None
        self.last_usage: dict = {}

    async def generate_json(self, system: str, user: str, schema: dict,
                            max_tokens: int | None = None) -> str:
        error: LLMError | None = None
        for provider in self.providers:
            await self.quota.acquire()  # QuotaExhausted propagates: no point trying the next
            try:
                text = await provider.generate_json(system, user, schema, max_tokens)
            except LLMError as exc:
                self.last_error = f"{provider.model}: {exc}"[:300]
                if exc.bad_output or not exc.retryable or exc.rate_limited:
                    raise
                error = exc
                continue
            self.last_model = provider.model
            self.last_usage = getattr(provider, "last_usage", {})
            return text
        assert error is not None
        raise error

    async def aclose(self) -> None:
        for provider in self.providers:
            await provider.aclose()


def _gemini(key: str, model: str) -> GeminiProvider:
    temperature = os.environ.get("LLM_TEMPERATURE", "")
    return GeminiProvider(
        key, model,
        temperature=float(temperature) if temperature else None,
        thinking_level=os.environ.get("LLM_THINKING_LEVEL", "low") or None,
        base_url=os.environ.get("GEMINI_BASE_URL", GEMINI_URL),
        timeout=float(os.environ.get("LLM_TIMEOUT_SEC", "300")),
    )


def managed_provider_from_env() -> ManagedProvider | None:
    """None when no LLM is configured: articles are then shown untranslated."""
    name = os.environ.get("LLM_PROVIDER", "gemini").lower()
    if name in ("", "off", "none"):
        return None
    if name != "gemini":
        raise RuntimeError(f"Unknown LLM_PROVIDER: {name!r} (expected gemini|off)")
    key = os.environ.get("GEMINI_API_KEY", "")
    if not key:
        return None
    models = [os.environ.get("LLM_MODEL", "gemma-4-26b-a4b-it")]
    fallback = os.environ.get("LLM_FALLBACK_MODEL", "gemini-3.8-flash")
    if fallback and fallback not in models:
        models.append(fallback)
    quota = Quota(daily_limit=int(os.environ.get("LLM_DAILY_CALL_LIMIT", "200")),
                  per_minute=int(os.environ.get("LLM_RPM_LIMIT", "10")))
    return ManagedProvider([_gemini(key, m) for m in models], quota)
