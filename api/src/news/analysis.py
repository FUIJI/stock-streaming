"""What the LLM must return per article (triage), and the rules applied on top of it.

The model output is untrusted input: it is validated here, symbols outside the
article's own tags are dropped, and importance is capped by code rules
(docs/specs/phase-3c-news.md Q6/Q10, docs/specs/phase-3d-news-digest.md D-Q1/D-Q2).
"""

import re
from dataclasses import dataclass
from typing import Literal

from typing import Any

from pydantic import BaseModel, Field, field_validator

PROMPT_VERSION = "t1"

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
# Gemma's reasoning leaking into the answer (seen: "ปัจthought: [thought] {").
_THOUGHT = re.compile(r"(?<![A-Za-z])thought(?![A-Za-z])|</?think", re.IGNORECASE)
# A Thai vowel/tone mark can only follow a Thai consonant (seen: "ความเสan์").
_BROKEN_THAI = re.compile(r"[A-Za-z][\u0e31\u0e34-\u0e3a\u0e47-\u0e4e]")
# Lowercase Latin glued inside a Thai word. Names are spaced or capitalised ("หุ้นAAPL").
_GLUED_LATIN = re.compile(r"[\u0e01-\u0e4e][a-z]+[\u0e01-\u0e4e]")


def degeneration(text: str) -> str | None:
    """Why `text` looks like broken generation, or None if it looks fine."""
    if _CJK.search(text):
        return "contains CJK characters"
    for pattern, problem in ((_THOUGHT, "leaked reasoning"), (_BROKEN_THAI, "broken Thai"),
                             (_GLUED_LATIN, "Latin inside a Thai word")):
        if match := pattern.search(text):
            return f"{problem}: {match.group(0)!r}"
    for match in _REPEATED_CHUNK.finditer(text):
        if not any(c.isdigit() for c in match.group(1)):
            return f"repeats {match.group(1)!r}"
    return None


class SymbolAssessment(BaseModel):
    symbol: str
    relevance: float = Field(ge=0, le=1)
    sentiment: float = Field(ge=-1, le=1)


HEADLINE_TH_MAX = 100


class NewsAnalysis(BaseModel):
    """Triage of one article: scores plus a one-line Thai headline."""

    id: int
    # Optional on purpose (D-Q2): a garbled translation is dropped, the scores are kept.
    headline_th: str | None = None
    category: Category
    importance: int = Field(ge=1, le=5)
    is_market_wide: bool
    per_symbol: list[SymbolAssessment]

    @field_validator("headline_th", mode="before")
    @classmethod
    def usable_or_none(cls, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        value = value.strip()
        if not value or len(value) > HEADLINE_TH_MAX or degeneration(value):
            return None
        return value


class BatchAnalysis(BaseModel):
    """The reply's envelope; items are validated one by one so one bad item can't sink 25."""

    items: list[Any]


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
