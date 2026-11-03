"""Run the configured LLM on the labelled cases and report how often it agrees (D3).

    dc run --rm -T api python -m src.news.eval --runs 2 [--batch 10] < api/tests/news_eval/cases.json
    dc run --rm -T -e LLM_TEMPERATURE=0 api python -m src.news.eval ...   # try a setting

Costs ~2 calls per run for 30 cases (batches of LLM_TRIAGE_BATCH). Writes nothing.
Exit code 1 when below the bar in evaluation.PASS.
"""

import argparse
import asyncio
import json
import logging
import os
import sys
import time

from .analysis import PROMPT_VERSION
from .analyzer import AnalyzeResult, analyze
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


async def analyze_in_batches(provider, items, names, batch: int) -> AnalyzeResult:
    """Same batch size as the worker sends (LLM_TRIAGE_BATCH), not all cases at once."""
    total = AnalyzeResult()
    for n, start in enumerate(range(0, len(items), batch), 1):
        print(f"  batch {n}/{-(-len(items) // batch)} ...", file=sys.stderr, flush=True)
        part = await analyze(provider, items[start:start + batch], names)
        total.done.update(part.done)
        total.failed.update(part.failed)
        total.calls += part.calls
        total.splits += part.splits
        total.prompt_tokens += part.prompt_tokens
        total.output_tokens += part.output_tokens
    return total


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


async def run(cases: list[dict], runs: int, batch: int) -> bool:
    provider = managed_provider_from_env()
    if provider is None:
        raise SystemExit("No LLM configured: set GEMINI_API_KEY (and LLM_MODEL if needed)")
    items = [to_item(c) for c in cases]
    names = await names_from_db()
    results, reports = [], []
    try:
        for i in range(runs):
            started = time.monotonic()
            result = await analyze_in_batches(provider, items, names, batch)
            results.append(result.done)
            reports.append(score(cases, result.done))
            print_report(reports[-1], i + 1)
            for news_id, error in result.failed.items():
                print(f"  ! {news_id}: {error}")
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
    parser.add_argument("--batch", type=int,
                        default=int(os.environ.get("LLM_TRIAGE_BATCH", "25")),
                        help="articles per call (default: LLM_TRIAGE_BATCH)")
    args = parser.parse_args()
    cases = [c for c in json.load(sys.stdin)["cases"] if labelled(c)]
    if not cases:
        raise SystemExit("No labelled cases on stdin (see tests/news_eval/README.md)")
    print(f"{len(cases)} labelled cases · {args.batch} per call · "
          f"temperature {os.environ.get('LLM_TEMPERATURE', 'default')}")
    sys.exit(0 if asyncio.run(run(cases, args.runs, args.batch)) else 1)


if __name__ == "__main__":
    main()
