"""What the LLM must return per article, and the rules applied on top of it.

The model output is untrusted input: it is validated here, symbols outside the
article's own tags are dropped, and importance is capped by code rules
(docs/specs/phase-3c-news.md, Q6 and Q10).
"""

import re
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field, field_validator

PROMPT_VERSION = "v3"

Category = Literal[
    "earnings", "guidance", "analyst", "product", "legal_regulatory", "m_and_a",
    "management", "macro", "market_roundup", "other",
]
CATEGORIES: tuple[str, ...] = Category.__args__  # type: ignore[attr-defined]

# Below this relevance a tag is treated as incidental (article hidden by default,
# importance capped).
RELEVANCE_THRESHOLD = 0.5
CAPPED_IMPORTANCE = 2


@dataclass(frozen=True)
class NewsItem:
    """One article as the LLM sees it."""

    id: int
    headline: str
    summary: str
    tags: list[str]  # every symbol Alpaca tagged
    tracked: list[str]  # the tags we follow: the only symbols the model may assess


# The same short chunk 4+ times in a row ("โอกาส-อุตดารณ์โอกาส-อุตดารณ์...", ",,, ,,, ,,,")
# is a model stuck in a loop. Chunks with digits are exempt: "1,000,000,000" is fine.
_REPEATED_CHUNK = re.compile(r"(.{2,30}?)\1{3,}", re.DOTALL)
# Thai summaries have no business containing CJK (seen: "进进进").
_CJK = re.compile(r"[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af]")


def degeneration(text: str) -> str | None:
    """Why `text` looks like broken generation, or None if it looks fine."""
    if _CJK.search(text):
        return "contains CJK characters"
    for match in _REPEATED_CHUNK.finditer(text):
        if not any(c.isdigit() for c in match.group(1)):
            return f"repeats {match.group(1)!r}"
    return None


class SymbolAssessment(BaseModel):
    symbol: str
    relevance: float = Field(ge=0, le=1)
    sentiment: float = Field(ge=-1, le=1)


class NewsAnalysis(BaseModel):
    id: int
    # Above what the prompt asks for (~200 / ~120 characters), to catch runaway text.
    summary_th: str = Field(min_length=1, max_length=400)
    why_it_matters: str = Field(min_length=1, max_length=250)
    category: Category
    importance: int = Field(ge=1, le=5)
    is_market_wide: bool
    per_symbol: list[SymbolAssessment]

    @field_validator("summary_th", "why_it_matters")
    @classmethod
    def not_degenerate(cls, value: str) -> str:
        if problem := degeneration(value):
            raise ValueError(f"degenerate text: {problem}")
        return value


class BatchAnalysis(BaseModel):
    items: list[NewsAnalysis]


@dataclass(frozen=True)
class FinalAnalysis:
    """What gets stored: validated model output after code rules."""

    analysis: NewsAnalysis
    importance: int  # after caps; analysis.importance keeps the model's raw value
    per_symbol: list[SymbolAssessment]  # exactly one entry per tracked tag
    model: str = ""  # the model that actually answered (the fallback, sometimes)


def finalize(analysis: NewsAnalysis, item: NewsItem) -> FinalAnalysis:
    by_symbol = {
        a.symbol.upper(): a for a in analysis.per_symbol if a.symbol.upper() in item.tracked
    }
    # A tracked tag the model skipped is recorded as not relevant rather than guessed.
    per_symbol = [
        by_symbol.get(s) or SymbolAssessment(symbol=s, relevance=0, sentiment=0)
        for s in item.tracked
    ]
    per_symbol = [a.model_copy(update={"symbol": a.symbol.upper()}) for a in per_symbol]

    importance = analysis.importance
    top_relevance = max((a.relevance for a in per_symbol), default=0)
    if top_relevance < RELEVANCE_THRESHOLD or analysis.category == "market_roundup":
        importance = min(importance, CAPPED_IMPORTANCE)
    return FinalAnalysis(analysis=analysis, importance=importance, per_symbol=per_symbol)
