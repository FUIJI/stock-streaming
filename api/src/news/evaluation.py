"""Score triage answers against hand labels (D3). Pure functions: eval.py does the I/O.

A case file is JSON: {"cases": [{"id", "headline", "summary", "tags", "tracked",
"label": {"importance": 1-5, "per_symbol": {"AAPL": {"sentiment": "+"|"-"|"0",
"relevant": true|false}}}}]}. Unlabelled fields (null) are left out of the scores.
"""

from dataclasses import dataclass, field

from .analysis import RELEVANCE_THRESHOLD, FinalAnalysis, NewsItem

SENTIMENT_BAND = 0.2  # |sentiment| below this reads as neutral, as in the UI
PASS = {"importance_within_1": 0.85, "sentiment": 0.80}  # spec D3


def direction(sentiment: float) -> str:
    if abs(sentiment) < SENTIMENT_BAND:
        return "0"
    return "+" if sentiment > 0 else "-"


def to_item(case: dict) -> NewsItem:
    return NewsItem(id=int(case["id"]), headline=case["headline"], summary=case.get("summary", ""),
                    tags=list(case["tags"]), tracked=list(case["tracked"]))


def labelled(case: dict) -> bool:
    label = case.get("label") or {}
    return label.get("importance") is not None or any(
        v.get("sentiment") is not None or v.get("relevant") is not None
        for v in (label.get("per_symbol") or {}).values())


@dataclass
class Tally:
    hits: int = 0
    total: int = 0

    def add(self, ok: bool) -> None:
        self.hits += ok
        self.total += 1

    @property
    def rate(self) -> float | None:
        return self.hits / self.total if self.total else None


@dataclass
class Report:
    importance_exact: Tally = field(default_factory=Tally)
    importance_within_1: Tally = field(default_factory=Tally)
    sentiment: Tally = field(default_factory=Tally)
    relevance: Tally = field(default_factory=Tally)
    headline_th: Tally = field(default_factory=Tally)  # a usable Thai headline came back
    failed: list[int] = field(default_factory=list)  # no valid answer at all
    mistakes: list[str] = field(default_factory=list)

    def passed(self) -> bool:
        return all((getattr(self, k).rate or 0) >= v for k, v in PASS.items())


def score(cases: list[dict], answers: dict[int, FinalAnalysis]) -> Report:
    report = Report()
    for case in cases:
        news_id, label = int(case["id"]), case.get("label") or {}
        answer = answers.get(news_id)
        if answer is None:
            report.failed.append(news_id)
            continue
        report.headline_th.add(answer.analysis.headline_th is not None)
        want = label.get("importance")
        if want is not None:
            report.importance_exact.add(answer.importance == want)
            report.importance_within_1.add(abs(answer.importance - want) <= 1)
            if abs(answer.importance - want) > 1:
                report.mistakes.append(f"{news_id} importance {answer.importance}, label {want}"
                                       f" · {case['headline'][:70]}")
        got = {a.symbol: a for a in answer.per_symbol}
        for symbol, expected in (label.get("per_symbol") or {}).items():
            a = got.get(symbol)
            if a is None:
                continue
            if expected.get("sentiment") is not None:
                ok = direction(a.sentiment) == expected["sentiment"]
                report.sentiment.add(ok)
                if not ok:
                    report.mistakes.append(f"{news_id} {symbol} sentiment {a.sentiment:+.1f}, "
                                           f"label {expected['sentiment']} · {case['headline'][:70]}")
            if expected.get("relevant") is not None:
                ok = (a.relevance >= RELEVANCE_THRESHOLD) == expected["relevant"]
                report.relevance.add(ok)
                if not ok:
                    report.mistakes.append(f"{news_id} {symbol} relevance {a.relevance:.1f}, "
                                           f"label {'relevant' if expected['relevant'] else 'not'}"
                                           f" · {case['headline'][:70]}")
    return report


def agreement(runs: list[dict[int, FinalAnalysis]]) -> dict[str, float | None]:
    """How often repeated runs give the same answer (consistency, not correctness)."""
    importance, sentiment = Tally(), Tally()
    ids = set.intersection(*(set(r) for r in runs)) if runs else set()
    for news_id in ids:
        answers = [r[news_id] for r in runs]
        importance.add(len({a.importance for a in answers}) == 1)
        for i in range(len(answers[0].per_symbol)):
            sentiment.add(len({direction(a.per_symbol[i].sentiment) for a in answers}) == 1)
    return {"importance": importance.rate, "sentiment": sentiment.rate}
