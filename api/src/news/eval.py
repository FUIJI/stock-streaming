"""Run the configured LLM on the labelled cases and report how often it agrees (D3).

    dc run --rm -T api python -m src.news.eval --runs 2 [--batch 10] < api/tests/news_eval/cases.json
    dc run --rm -T -e LLM_TEMPERATURE=0 api python -m src.news.eval ...   # try a setting

Only the primary model is measured (the fallback's 20 calls a day are not spent here)
and calls are paced at --rpm 1, since the running API shares the key's per-minute limit.

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


RETRY_WAITS = (60.0, 180.0)  # seconds; Gemma's free tier often answers 503/500 for a while


async def analyze_in_batches(provider, items, names, batch: int,
                             waits=RETRY_WAITS, sleep=asyncio.sleep) -> AnalyzeResult:
    """Same batch size as the worker sends (LLM_TRIAGE_BATCH), not all cases at once.

    Articles that failed because the service was unavailable (5xx, 429, timeout) are
    tried again after a pause, like the worker would; bad answers are not (they count).
    """
    total = AnalyzeResult()

    async def one_pass(todo):
        for n, start in enumerate(range(0, len(todo), batch), 1):
            print(f"  batch {n}/{-(-len(todo) // batch)} ...", file=sys.stderr, flush=True)
            part = await analyze(provider, todo[start:start + batch], names)
            total.done.update(part.done)
            for news_id in part.done:
                total.failed.pop(news_id, None)
            total.failed.update(part.failed)
            total.calls += part.calls
            total.splits += part.splits
            total.prompt_tokens += part.prompt_tokens
            total.output_tokens += part.output_tokens

    await one_pass(items)
    for wait in waits:
        unavailable = [i for i in items if i.id in total.failed
                       and not total.failed[i.id].bad_output and total.failed[i.id].retryable]
        if not unavailable:
            break
        print(f"  {len(unavailable)} articles hit an unavailable service "
              f"({total.failed[unavailable[0].id]}); retrying in {wait:.0f}s",
              file=sys.stderr, flush=True)
        await sleep(wait)
        await one_pass(unavailable)
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


async def run(cases: list[dict], runs: int, batch: int, rpm: int,
              with_fallback: bool) -> bool:
    provider = managed_provider_from_env()
    if provider is None:
        raise SystemExit("No LLM configured: set GEMINI_API_KEY (and LLM_MODEL if needed)")
    if not with_fallback:
        for extra in provider.providers[1:]:
            await extra.aclose()
        provider.providers = provider.providers[:1]
    provider.quota.per_minute = rpm
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
            errors: dict[str, list[int]] = {}
            for news_id, error in result.failed.items():
                errors.setdefault(str(error)[:200], []).append(news_id)
            for message, ids in errors.items():
                print(f"  ! {len(ids)} articles: {message}")
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
    parser.add_argument("--rpm", type=int, default=1, help="calls per minute (default 1)")
    parser.add_argument("--with-fallback", action="store_true",
                        help="also use LLM_FALLBACK_MODEL (spends its small daily quota)")
    args = parser.parse_args()
    cases = [c for c in json.load(sys.stdin)["cases"] if labelled(c)]
    if not cases:
        raise SystemExit("No labelled cases on stdin (see tests/news_eval/README.md)")
    print(f"{len(cases)} labelled cases · {args.batch} per call · "
          f"temperature {os.environ.get('LLM_TEMPERATURE', 'default')}")
    sys.exit(0 if asyncio.run(run(cases, args.runs, args.batch, args.rpm,
                                  args.with_fallback)) else 1)


if __name__ == "__main__":
    main()
