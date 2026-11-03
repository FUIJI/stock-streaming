from datetime import UTC, datetime

from src.news.analysis import NewsAnalysis, finalize
from src.news.evaluation import agreement, direction, labelled, score, to_item
from src.news.export_eval import pick, to_case


def case(news_id, importance=None, aapl=None):
    return {"id": news_id, "headline": f"News {news_id}", "summary": "", "tags": ["AAPL", "SPY"],
            "tracked": ["AAPL"],
            "label": {"importance": importance, "per_symbol": {"AAPL": aapl or {}}}}


def answer(c, importance, relevance=0.9, sentiment=0.5, headline_th="หัวข่าว"):
    analysis = NewsAnalysis.model_validate({
        "id": c["id"], "headline_th": headline_th, "category": "product",
        "importance": importance, "is_market_wide": False,
        "per_symbol": [{"symbol": "AAPL", "relevance": relevance, "sentiment": sentiment}]})
    return finalize(analysis, to_item(c))


def test_direction_has_a_neutral_band():
    assert [direction(x) for x in (0.5, -0.3, 0.1, -0.19)] == ["+", "-", "0", "0"]


def test_scores_count_only_labelled_fields():
    cases = [case(1, 4, {"sentiment": "+", "relevant": True}),
             case(2, 5, {"sentiment": "-", "relevant": None}),
             case(3, None, {"sentiment": None, "relevant": False}),
             case(4, 2)]
    answers = {1: answer(cases[0], 4), 2: answer(cases[1], 3, sentiment=-0.6),
               3: answer(cases[2], 3, relevance=0.2, headline_th="ความเสan์")}
    r = score(cases, answers)
    assert (r.importance_within_1.hits, r.importance_within_1.total) == (1, 2)  # 5 vs 3
    assert r.importance_exact.rate == 0.5
    assert (r.sentiment.hits, r.sentiment.total) == (2, 2)
    assert (r.relevance.hits, r.relevance.total) == (2, 2)
    assert (r.headline_th.hits, r.headline_th.total) == (2, 3)  # garbled one dropped
    assert r.failed == [4]
    assert len(r.mistakes) == 1 and "importance 3, label 5" in r.mistakes[0]
    assert not r.passed()  # importance ±1 at 50%


def test_importance_is_scored_after_the_code_caps():
    c = case(1, 2, {"relevant": False})
    assert answer(c, 5, relevance=0.1).importance == 2  # capped: counts as right
    assert score([c], {1: answer(c, 5, relevance=0.1)}).importance_exact.rate == 1.0


def test_agreement_between_runs():
    c = case(1, 3)
    same = agreement([{1: answer(c, 3)}, {1: answer(c, 3, sentiment=0.4)}])
    assert same == {"importance": 1.0, "sentiment": 1.0}
    differ = agreement([{1: answer(c, 3)}, {1: answer(c, 4, sentiment=-0.4)}])
    assert differ == {"importance": 0.0, "sentiment": 0.0}


def test_unlabelled_cases_are_ignored():
    assert not labelled(case(1))
    assert labelled(case(1, 3)) and labelled(case(1, None, {"relevant": True}))


def test_export_spreads_over_model_scores_and_hides_them():
    rows = [{"id": i, "headline": "h", "summary": "", "symbols": ["AAPL"],
             "created_at": datetime(2026, 11, 3, tzinfo=UTC), "importance": imp}
            for i, imp in enumerate([2] * 20 + [5, 4, 3, None])]
    chosen = pick(rows, 6, seed=1)
    assert sorted({r["importance"] for r in chosen}, key=str) == sorted({2, 3, 4, 5, None}, key=str)
    assert pick(rows, 6, seed=1) == chosen  # reproducible
    exported = to_case({**rows[0], "symbols": ["AAPL", "SPY"]}, {"AAPL", "MSFT"})
    assert exported["tracked"] == ["AAPL"] and "importance" not in {k for k in exported
                                                                    if k != "label"}
    assert exported["label"]["per_symbol"] == {"AAPL": {"sentiment": None, "relevant": None}}


async def test_eval_sends_batches_like_the_worker():
    import json as _json

    from src.news.eval import analyze_in_batches

    class Echo:
        model, calls = "gemma", []

        async def generate_json(self, system, user, schema, max_tokens=None):
            ids = [a["id"] for a in _json.loads(user)["articles"]]
            self.calls.append(ids)
            return _json.dumps({"items": [
                {"id": i, "headline_th": "ห", "category": "other", "importance": 3,
                 "is_market_wide": False,
                 "per_symbol": [{"symbol": "AAPL", "relevance": 1, "sentiment": 0}]}
                for i in ids]})

    provider = Echo()
    items = [to_item(case(i)) for i in range(1, 8)]
    result = await analyze_in_batches(provider, items, {}, batch=3)
    assert provider.calls == [[1, 2, 3], [4, 5, 6], [7]] and len(result.done) == 7
    assert result.calls == 3
