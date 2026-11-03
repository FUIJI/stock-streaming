"""Triage a batch of articles in one call; a bad answer is split in halves and retried."""

import logging
import os
import re
from dataclasses import dataclass, field, replace

from pydantic import ValidationError

from .analysis import BatchAnalysis, FinalAnalysis, NewsAnalysis, NewsItem, finalize
from .llm import LLMError, LLMProvider
from .prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_user_message

log = logging.getLogger("api.news.llm")


_OPEN_FENCE = re.compile(r"^```[a-zA-Z]*\s*")
_CLOSE_FENCE = re.compile(r"\s*```$")


def extract_json(text: str) -> str:
    """The JSON document in a model reply. Gemma on the VM added markdown fences
    despite application/json, sometimes only the closing one."""
    return _CLOSE_FENCE.sub("", _OPEN_FENCE.sub("", text.strip())).strip()


def output_budget(articles: int) -> int:
    """Output-token ceiling for a call. A triage item is ~80 tokens; the base leaves room
    for a thinking model's reasoning, which counts against the same limit."""
    per_article = int(os.environ.get("LLM_MAX_TOKENS_PER_ARTICLE", "200"))
    return int(os.environ.get("LLM_MAX_TOKENS_BASE", "1024")) + per_article * articles


@dataclass
class AnalyzeResult:
    done: dict[int, FinalAnalysis] = field(default_factory=dict)
    failed: dict[int, LLMError] = field(default_factory=dict)
    calls: int = 0
    splits: int = 0  # times a batch was halved after a bad answer
    prompt_tokens: int = 0  # as reported by the API, for answers that came back
    output_tokens: int = 0


class _Stop(Exception):
    """An availability/config error: calling again for the rest would fail the same way."""

    def __init__(self, error: LLMError):
        self.error = error


async def _call(provider: LLMProvider, items: list[NewsItem], names: dict[str, str],
                result: AnalyzeResult) -> tuple[list[NewsAnalysis], str]:
    """The valid items of one answer (invalid ones are just absent) and the model used."""
    result.calls += 1
    raw = await provider.generate_json(SYSTEM_PROMPT, build_user_message(items, names),
                                       RESPONSE_SCHEMA, max_tokens=output_budget(len(items)))
    model = getattr(provider, "last_model", None) or provider.model
    usage = getattr(provider, "last_usage", None) or {}
    result.prompt_tokens += usage.get("promptTokenCount", 0)
    result.output_tokens += usage.get("candidatesTokenCount", 0) + usage.get(
        "thoughtsTokenCount", 0)
    try:
        envelope = BatchAnalysis.model_validate_json(extract_json(raw))
    except ValidationError as exc:
        raise LLMError(f"invalid model output: {str(exc)[:300]}", retryable=True,
                       bad_output=True) from exc
    valid = []
    for entry in envelope.items:
        try:
            valid.append(NewsAnalysis.model_validate(entry))
        except ValidationError as exc:
            log.info("Dropped an invalid item: %s", str(exc)[:200])
    return valid, model


async def _triage(provider: LLMProvider, items: list[NewsItem], names: dict[str, str],
                  result: AnalyzeResult) -> None:
    by_id = {item.id: item for item in items}
    try:
        answers, model = await _call(provider, items, names, result)
    except LLMError as exc:
        if not exc.bad_output:
            raise _Stop(exc) from exc
        error = exc
        answers, model = [], ""
    else:
        error = LLMError("model output did not include this article (or it was invalid)",
                         retryable=True, bad_output=True)
    for analysis in answers:
        item = by_id.get(analysis.id)  # ids we did not send are ignored
        if item is not None and analysis.id not in result.done:
            result.done[analysis.id] = replace(finalize(analysis, item), model=model)

    leftover = [item for item in items if item.id not in result.done]
    if not leftover:
        return
    if len(leftover) == 1 and len(items) == 1:
        result.failed[leftover[0].id] = error
        return
    if len(leftover) < len(items):  # partial answer: the rest go again as one smaller batch
        await _triage(provider, leftover, names, result)
        return
    result.splits += 1
    log.warning("Batch of %d failed (%s); splitting in halves", len(items), error)
    half = len(leftover) // 2
    await _triage(provider, leftover[:half], names, result)
    await _triage(provider, leftover[half:], names, result)


async def analyze(provider: LLMProvider, items: list[NewsItem],
                  names: dict[str, str]) -> AnalyzeResult:
    """Triage `items`; every article ends up in `done` or `failed`, never raises LLMError."""
    result = AnalyzeResult()
    try:
        await _triage(provider, items, names, result)
    except _Stop as stop:
        # Rate limits, outages, bad keys: fail what's left instead of multiplying calls.
        result.failed.update({i.id: stop.error for i in items
                              if i.id not in result.done and i.id not in result.failed})
    return result
