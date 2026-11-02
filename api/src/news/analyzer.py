"""Run a batch of articles through the LLM, falling back to one article per call."""

import logging
import re
from dataclasses import dataclass, field

from pydantic import ValidationError

from .analysis import BatchAnalysis, FinalAnalysis, NewsItem, finalize
from .llm import LLMError, LLMProvider
from .prompt import RESPONSE_SCHEMA, SYSTEM_PROMPT, build_user_message

log = logging.getLogger("api.news.llm")


_OPEN_FENCE = re.compile(r"^```[a-zA-Z]*\s*")
_CLOSE_FENCE = re.compile(r"\s*```$")


def extract_json(text: str) -> str:
    """The JSON document in a model reply. Gemma on the VM added markdown fences
    despite application/json, sometimes only the closing one."""
    return _CLOSE_FENCE.sub("", _OPEN_FENCE.sub("", text.strip())).strip()


@dataclass
class AnalyzeResult:
    done: dict[int, FinalAnalysis] = field(default_factory=dict)
    failed: dict[int, LLMError] = field(default_factory=dict)
    calls: int = 0


async def _call(provider: LLMProvider, items: list[NewsItem], names: dict[str, str],
                result: AnalyzeResult) -> BatchAnalysis:
    result.calls += 1
    raw = await provider.generate_json(SYSTEM_PROMPT, build_user_message(items, names),
                                       RESPONSE_SCHEMA)
    try:
        return BatchAnalysis.model_validate_json(extract_json(raw))
    except ValidationError as exc:
        raise LLMError(f"invalid model output: {str(exc)[:300]}", retryable=True,
                       bad_output=True) from exc


async def analyze(provider: LLMProvider, items: list[NewsItem],
                  names: dict[str, str]) -> AnalyzeResult:
    """One call for the batch; anything it got wrong or left out is retried alone."""
    result = AnalyzeResult()
    by_id = {item.id: item for item in items}
    leftover = list(items)

    if len(items) > 1:
        try:
            batch = await _call(provider, items, names, result)
            for analysis in batch.items:
                if analysis.id in by_id and analysis.id not in result.done:
                    result.done[analysis.id] = finalize(analysis, by_id[analysis.id])
            leftover = [item for item in items if item.id not in result.done]
        except LLMError as exc:
            if not exc.bad_output:
                # Rate limits, outages, bad keys would fail per article too: don't multiply calls.
                result.failed = {item.id: exc for item in items}
                return result
            log.warning("Batch of %d failed (%s); retrying one by one", len(items), exc)

    for i, item in enumerate(leftover):
        try:
            batch = await _call(provider, [item], names, result)
            match = next((a for a in batch.items if a.id == item.id), None)
            if match is None:
                raise LLMError("model output did not include this article", retryable=True,
                               bad_output=True)
            result.done[item.id] = finalize(match, item)
        except LLMError as exc:
            result.failed[item.id] = exc
            if not exc.bad_output:
                result.failed.update({rest.id: exc for rest in leftover[i + 1:]})
                break
    return result
