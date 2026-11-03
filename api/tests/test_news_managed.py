from datetime import UTC, datetime

import pytest

from src.news.analysis import NewsItem
from src.news.analyzer import analyze
from src.news.llm import LLMError
from src.news.managed import ManagedProvider, Quota, QuotaExhausted

ITEM = NewsItem(id=1, headline="h", summary="s", tags=["AAPL"], tracked=["AAPL"])
ANSWER = ('{"items": [{"id": 1, "headline_th": "หัวข่าว",'
          ' "category": "product", "importance": 4, "is_market_wide": false,'
          ' "per_symbol": [{"symbol": "AAPL", "relevance": 0.9, "sentiment": 0.3}]}]}')


class Scripted:
    """A provider that replays a script of answers / errors."""

    def __init__(self, model, *script):
        self.model, self.script, self.calls = model, list(script), 0

    async def generate_json(self, system, user, schema, max_tokens=None):
        self.calls += 1
        step = self.script.pop(0)
        if isinstance(step, Exception):
            raise step
        return step

    async def aclose(self):
        pass


def roomy_quota(**kw):
    return Quota(daily_limit=kw.get("daily", 100), per_minute=kw.get("rpm", 100))


async def test_unavailable_primary_falls_back_and_the_answering_model_is_recorded():
    primary = Scripted("gemma", LLMError("503 overloaded", retryable=True))
    fallback = Scripted("gemini-flash", ANSWER)
    managed = ManagedProvider([primary, fallback], roomy_quota())
    result = await analyze(managed, [ITEM], {})
    assert result.done[1].model == "gemini-flash"
    assert managed.quota.calls_today == 2  # both attempts count against our quota


async def test_bad_output_is_not_sent_to_the_fallback():
    bad = LLMError("invalid", retryable=True, bad_output=True)
    primary, fallback = Scripted("gemma", bad), Scripted("gemini-flash", ANSWER)
    managed = ManagedProvider([primary, fallback], roomy_quota())
    with pytest.raises(LLMError):
        await managed.generate_json("s", "u", {})
    assert fallback.calls == 0


async def test_auth_errors_do_not_fall_back_either():
    primary = Scripted("gemma", LLMError("400 key invalid", retryable=False))
    fallback = Scripted("gemini-flash", ANSWER)
    with pytest.raises(LLMError):
        await ManagedProvider([primary, fallback], roomy_quota()).generate_json("s", "u", {})
    assert fallback.calls == 0


async def test_daily_limit_stops_calls_until_utc_midnight():
    quota = Quota(daily_limit=2, per_minute=100,
                  clock=lambda: datetime(2026, 11, 3, 15, 0, tzinfo=UTC))
    primary = Scripted("gemma", ANSWER, ANSWER, ANSWER)
    managed = ManagedProvider([primary], quota)
    await managed.generate_json("s", "u", {})
    await managed.generate_json("s", "u", {})
    with pytest.raises(QuotaExhausted) as exc:
        await managed.generate_json("s", "u", {})
    assert exc.value.resets_at == datetime(2026, 11, 4, tzinfo=UTC)
    assert primary.calls == 2


async def test_the_daily_count_resets_on_a_new_utc_day():
    day = {"now": datetime(2026, 11, 3, 23, 59, tzinfo=UTC)}
    quota = Quota(daily_limit=1, per_minute=100, clock=lambda: day["now"])
    await quota.acquire()
    day["now"] = datetime(2026, 11, 4, 0, 1, tzinfo=UTC)
    await quota.acquire()
    assert quota.calls_today == 1


async def test_per_minute_limit_waits_instead_of_failing():
    clock = {"t": 0.0}
    waits = []

    async def fake_sleep(seconds):
        waits.append(seconds)
        clock["t"] += seconds

    quota = Quota(daily_limit=100, per_minute=2, monotonic=lambda: clock["t"], sleep=fake_sleep)
    await quota.acquire()
    clock["t"] = 10
    await quota.acquire()
    await quota.acquire()  # third call within the minute: waits for the first to age out
    assert waits == [50] and quota.calls_today == 3
