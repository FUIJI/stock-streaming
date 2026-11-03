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
    return {"id": news_id, "headline_th": "หัวข่าวไทย",
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
    assert result.done[1].analysis.headline_th == "หัวข่าวไทย"


async def test_broken_batch_output_is_split_in_halves():
    fake = FakeGemini(gemini_reply("{not json"),
                      gemini_reply({"items": [item_json(1, "AAPL")]}),
                      gemini_reply({"items": [item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert sorted(result.done) == [1, 2] and result.calls == 3 and result.splits == 1


async def test_halving_goes_down_to_single_articles_only_where_needed():
    items = [NewsItem(id=i, headline=f"h{i}", summary="", tags=["AAPL"], tracked=["AAPL"])
             for i in range(1, 5)]
    fake = FakeGemini(gemini_reply("{not json"),  # [1,2,3,4] -> halves
                      gemini_reply({"items": [item_json(1, "AAPL"), item_json(2, "AAPL")]}),
                      gemini_reply("{not json"),  # [3,4] -> halves
                      gemini_reply({"items": [item_json(3, "AAPL")]}),
                      gemini_reply("{not json"))  # [4] alone fails: reported, not raised
    result = await analyze(fake.provider(), items, NAMES)
    assert sorted(result.done) == [1, 2, 3] and result.failed[4].bad_output
    assert (result.calls, result.splits) == (5, 2)


async def test_only_articles_missing_from_the_batch_are_retried():
    fake = FakeGemini(gemini_reply({"items": [item_json(1, "AAPL")]}),
                      gemini_reply({"items": [item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert sorted(result.done) == [1, 2] and result.calls == 2 and result.splits == 0


async def test_one_invalid_item_does_not_sink_the_batch():
    fake = FakeGemini(gemini_reply({"items": [item_json(1, "AAPL", importance=9),  # invalid
                                              item_json(2, "MSFT"), item_json(99, "AAPL")]}),
                      gemini_reply({"items": [item_json(1, "AAPL")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert sorted(result.done) == [1, 2] and result.calls == 2  # id 99 was never sent: ignored


async def test_an_article_that_keeps_failing_is_reported_not_raised():
    fake = FakeGemini(gemini_reply("{}"),
                      gemini_reply({"items": [item_json(1, "AAPL", importance=9)]}),  # invalid
                      gemini_reply({"items": [item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert list(result.done) == [2] and result.failed[1].retryable


async def test_garbled_thai_headline_is_dropped_but_the_scores_kept():
    bad = {**item_json(1, "AAPL"), "headline_th": "ความเสan์เสียชื่อ"}
    result = await analyze(FakeGemini(gemini_reply({"items": [bad]})).provider(), ITEMS[:1], NAMES)
    final = result.done[1]
    assert final.analysis.headline_th is None and final.importance == 4


async def test_rate_limits_are_retryable_and_do_not_multiply_calls():
    rate_limited = FakeGemini(httpx.Response(429, text="quota"))
    result = await analyze(rate_limited.provider(), ITEMS, NAMES)
    assert result.calls == 1 and all(e.retryable for e in result.failed.values())


async def test_http_errors_stop_the_halving_retries_too():
    fake = FakeGemini(gemini_reply("{not json"), httpx.Response(503, text="overloaded"))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert result.calls == 2 and sorted(result.failed) == [1, 2]

    bad_key = FakeGemini(httpx.Response(400, text="API key not valid"))
    result = await analyze(bad_key.provider(), ITEMS, NAMES)
    assert result.calls == 1 and not result.failed[2].retryable  # no per-article retries


async def test_thinking_level_is_only_sent_to_gemini_models():
    fake = FakeGemini(gemini_reply({"items": [item_json(1, "AAPL")]}))
    gemma = GeminiProvider("k", "gemma-4-26b-a4b-it", thinking_level="low",
                           transport=httpx.MockTransport(fake.handler))
    await analyze(gemma, ITEMS[:1], NAMES)
    assert "thinkingConfig" not in json.loads(fake.requests[0].content)["generationConfig"]


async def test_network_errors_name_their_type():
    def timeout(request):
        raise httpx.ReadTimeout("", request=request)

    result = await analyze(FakeGemini(timeout).provider(), ITEMS[:1], NAMES)
    message = str(result.failed[1])
    assert "ReadTimeout" in message and "after" in message and result.failed[1].retryable


def test_extract_json_unwraps_markdown_fences():
    from src.news.analyzer import extract_json

    doc = '{"items": []}'
    assert extract_json(doc) == doc
    assert extract_json(f"```json\n{doc}\n```") == doc
    assert extract_json(f"```\n{doc}\n```\n") == doc
    assert extract_json(f"  {doc}  \n") == doc
    assert extract_json(f"{doc}\n```") == doc  # closing fence only, as Gemma replied
    assert extract_json(f"```json\n{doc}") == doc


async def test_fenced_reply_like_gemmas_is_accepted():
    # The shape seen on the VM: valid JSON, then a closing fence on its own line.
    reply = "```json\n" + json.dumps({"items": [item_json(1, "AAPL")]}, indent=2) + "\n```"
    result = await analyze(FakeGemini(gemini_reply(reply)).provider(), ITEMS[:1], NAMES)
    assert list(result.done) == [1] and result.calls == 1


async def test_output_is_capped_per_article_in_the_batch():
    fake = FakeGemini(gemini_reply({"items": [item_json(1, "AAPL"), item_json(2, "MSFT")]}))
    await analyze(fake.provider(), ITEMS, NAMES)
    assert json.loads(fake.requests[0].content)["generationConfig"]["maxOutputTokens"] == \
        1024 + 200 * 2


async def test_hitting_the_token_limit_retries_in_halves():
    cut_off = httpx.Response(200, json={"candidates": [
        {"finishReason": "MAX_TOKENS", "content": {"parts": [{"text": '{"items": [{"id": 1,'}]}}]})
    fake = FakeGemini(cut_off,
                      gemini_reply({"items": [item_json(1, "AAPL")]}),
                      gemini_reply({"items": [item_json(2, "MSFT")]}))
    result = await analyze(fake.provider(), ITEMS, NAMES)
    assert sorted(result.done) == [1, 2] and result.calls == 3
    single = json.loads(fake.requests[1].content)["generationConfig"]["maxOutputTokens"]
    assert single == 1024 + 200


def test_prompt_keeps_the_format_rules():
    from src.news.prompt import SYSTEM_PROMPT

    assert "No markdown, no code fences" in SYSTEM_PROMPT
    assert "own words" in SYSTEM_PROMPT


def test_article_summaries_are_cut_before_sending():
    from src.news.prompt import build_user_message

    long = NewsItem(id=1, headline="h", summary="x" * 1000, tags=["AAPL"], tracked=["AAPL"])
    assert len(json.loads(build_user_message([long], NAMES))["articles"][0]["summary"]) == 300


async def test_token_usage_is_added_up():
    reply = httpx.Response(200, json={
        "candidates": [{"content": {"parts": [{"text": json.dumps(
            {"items": [item_json(1, "AAPL"), item_json(2, "MSFT")]})}]}}],
        "usageMetadata": {"promptTokenCount": 900, "candidatesTokenCount": 150}})
    result = await analyze(FakeGemini(reply).provider(), ITEMS, NAMES)
    assert (result.prompt_tokens, result.output_tokens) == (900, 150)


async def test_filtered_answers_are_bad_output_not_an_outage():
    from src.news.managed import ManagedProvider, Quota

    withheld = httpx.Response(200, json={"candidates": [
        {"finishReason": "RECITATION", "content": {}, "finishMessage": "may contain material"}]})
    gemma, flash = FakeGemini(withheld), FakeGemini(gemini_reply({"items": []}))
    managed = ManagedProvider(
        [GeminiProvider("k", "gemma-4-26b-a4b-it", transport=httpx.MockTransport(gemma.handler)),
         GeminiProvider("k", "gemini-3.8-flash", transport=httpx.MockTransport(flash.handler))],
        Quota(daily_limit=100, per_minute=100))
    result = await analyze(managed, ITEMS[:1], NAMES)
    error = result.failed[1]
    assert "RECITATION" in str(error) and error.bad_output and error.retryable
    assert flash.requests == []  # the 20-a-day fallback is not spent on it


def test_prompt_version_is_triage():
    from src.news.analysis import PROMPT_VERSION

    assert PROMPT_VERSION == "t2"
