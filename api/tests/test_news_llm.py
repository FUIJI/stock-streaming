import json

import httpx

from src.news.analysis import NewsItem
from src.news.analyzer import analyze
from src.news.llm import GeminiProvider

ITEMS = [
    NewsItem(id=1, headline="Apple beats estimates", summary="Record iPhone sales.",
             tags=["AAPL"], tracked=["AAPL"]),
    NewsItem(id=2, headline="Microsoft cut to Hold", summary="Analyst downgrade.",
             tags=["MSFT", "SPY"], tracked=["MSFT"]),
]
NAMES = {"AAPL": "Apple Inc.", "MSFT": "Microsoft Corporation"}


def item_json(news_id, symbol, importance=4):
    return {"id": news_id, "summary_th": "สรุปข่าว", "why_it_matters": "สำคัญเพราะ...",
            "category": "earnings", "importance": importance, "is_market_wide": False,
            "per_symbol": [{"symbol": symbol, "relevance": 0.9, "sentiment": 0.5}]}


def gemini_reply(payload, thought=False) -> httpx.Response:
    parts = [{"text": "thinking...", "thought": True}] if thought else []
    parts.append({"text": payload if isinstance(payload, str) else json.dumps(payload)})
    return httpx.Response(200, json={"candidates": [{"content": {"parts": parts}}]})


class FakeGemini:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests = []

    def handler(self, request):
        self.requests.append(request)
        reply = self.responses.pop(0)
        return reply(request) if callable(reply) else reply

    def provider(self, **kwargs):
        return GeminiProvider("test-key", "gemini-3.8-flash",
                              transport=httpx.MockTransport(self.handler), **kwargs)


async def test_request_uses_structured_output_and_no_tools():
    fake = FakeGemini(gemini_reply({"items": [item_json(1, "AAPL"), item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(temperature=0, thinking_level="low"), ITEMS, NAMES)
    assert sorted(result.done) == [1, 2] and result.calls == 1

    req = fake.requests[0]
    assert req.url.path == "/v1beta/models/gemini-3.8-flash:generateContent"
    assert req.headers["x-goog-api-key"] == "test-key"
    body = json.loads(req.content)
    assert "tools" not in body
    cfg = body["generationConfig"]
    assert cfg["responseMimeType"] == "application/json" and cfg["temperature"] == 0
    assert cfg["thinkingConfig"] == {"thinkingLevel": "low"}
    sent = json.loads(body["contents"][0]["parts"][0]["text"])["articles"]
    assert sent[1]["tracked"] == [{"symbol": "MSFT", "company": "Microsoft Corporation"}]


async def test_thought_parts_are_ignored():
    fake = FakeGemini(gemini_reply({"items": [item_json(1, "AAPL")]}, thought=True))
    result = await analyze(fake.provider(), ITEMS[:1], NAMES)
    assert result.done[1].analysis.summary_th == "สรุปข่าว"


async def test_broken_batch_output_is_retried_one_article_at_a_time():
    fake = FakeGemini(gemini_reply("{not json"),
                      gemini_reply({"items": [item_json(1, "AAPL")]}),
                      gemini_reply({"items": [item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert sorted(result.done) == [1, 2] and result.calls == 3


async def test_only_articles_missing_from_the_batch_are_retried():
    fake = FakeGemini(gemini_reply({"items": [item_json(1, "AAPL")]}),
                      gemini_reply({"items": [item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert sorted(result.done) == [1, 2] and result.calls == 2


async def test_an_article_that_keeps_failing_is_reported_not_raised():
    fake = FakeGemini(gemini_reply("{}"),
                      gemini_reply({"items": [item_json(1, "AAPL", importance=9)]}),  # invalid
                      gemini_reply({"items": [item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert list(result.done) == [2] and result.failed[1].retryable


async def test_rate_limits_are_retryable_and_do_not_multiply_calls():
    rate_limited = FakeGemini(httpx.Response(429, text="quota"))
    result = await analyze(rate_limited.provider(), ITEMS, NAMES)
    assert result.calls == 1 and all(e.retryable for e in result.failed.values())


async def test_http_errors_stop_the_one_by_one_retries_too():
    fake = FakeGemini(gemini_reply("{not json"), httpx.Response(503, text="overloaded"))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert result.calls == 2 and sorted(result.failed) == [1, 2]

    bad_key = FakeGemini(httpx.Response(400, text="API key not valid"))
    result = await analyze(bad_key.provider(), ITEMS, NAMES)
    assert result.calls == 1 and not result.failed[2].retryable  # no per-article retries
