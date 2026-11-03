"""Run the configured LLM on the labelled cases and report how often it agrees (D3).

    dc run --rm -T api python -m src.news.eval --runs 2 < api/tests/news_eval/cases.json

Costs ~2 calls per run for 30 cases (batches of LLM_TRIAGE_BATCH). Writes nothing.
Exit code 1 when below the bar in evaluation.PASS.
"""

import argparse
import asyncio
import json
import logging
import sys
import time

from .analysis import PROMPT_VERSION
from .analyzer import analyze
from .evaluation import PASS, Report, agreement, labelled, score, to_item
from .managed import managed_provider_from_env
from .store import company_names


async def names_from_db() -> dict[str, str]:
    """Company names as the worker sends them; the eval still runs without a database."""
    from ..db import create_pool

    try:
        pool = await create_pool()
    except Exception:
        return {}
    try:
        return await company_names(pool)
    finally:
        await pool.close()


def pct(rate: float | None) -> str:
    return "  n/a" if rate is None else f"{rate * 100:5.1f}%"


def print_report(report: Report, run: int) -> None:
    print(f"\n== run {run}")
    for name in ("importance_within_1", "importance_exact", "sentiment", "relevance",
                 "headline_th"):
        tally = getattr(report, name)
        bar = f"  (pass ≥ {PASS[name] * 100:.0f}%)" if name in PASS else ""
        print(f"{name:20} {pct(tally.rate)}  {tally.hits}/{tally.total}{bar}")
    if report.failed:
        print(f"no answer for: {report.failed}")
    for line in report.mistakes:
        print("  ✗", line)


async def run(cases: list[dict], runs: int) -> bool:
    provider = managed_provider_from_env()
    if provider is None:
        raise SystemExit("No LLM configured: set GEMINI_API_KEY (and LLM_MODEL if needed)")
    items = [to_item(c) for c in cases]
    names = await names_from_db()
    results, reports = [], []
    try:
        for i in range(runs):
            started = time.monotonic()
            result = await analyze(provider, items, names)
            results.append(result.done)
            reports.append(score(cases, result.done))
            print_report(reports[-1], i + 1)
            print(f"calls {result.calls} · splits {result.splits} · tokens in/out "
                  f"{result.prompt_tokens}/{result.output_tokens} · "
                  f"{time.monotonic() - started:.0f}s")
    finally:
        await provider.aclose()
    if runs > 1:
        same = agreement(results)
        print(f"\nsame answer across runs: importance {pct(same['importance'])} · "
              f"sentiment direction {pct(same['sentiment'])}")
    passed = all(r.passed() for r in reports)
    print(f"\nprompt {PROMPT_VERSION} · models {' -> '.join(p.model for p in provider.providers)}"
          f" · {'PASS' if passed else 'BELOW THE BAR'}")
    return passed


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--runs", type=int, default=1, help="repeat to measure consistency")
    args = parser.parse_args()
    cases = [c for c in json.load(sys.stdin)["cases"] if labelled(c)]
    if not cases:
        raise SystemExit("No labelled cases on stdin (see tests/news_eval/README.md)")
    print(f"{len(cases)} labelled cases")
    sys.exit(0 if asyncio.run(run(cases, args.runs)) else 1)


if __name__ == "__main__":
    main()
