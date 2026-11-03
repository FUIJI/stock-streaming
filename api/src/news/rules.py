"""Rules that keep articles away from the LLM (Phase 3d, D1). Free and instant.

Skipped articles are still stored (and visible with include_skipped); `reprocess
--rerun-rules` re-applies these rules after they change.
"""

import os
import re

# Benzinga's listicles and auto-generated items: they mention a stock, they aren't news about it.
_NOISE = [
    r"\bstocks? to (watch|buy|sell)\b",
    r"\bwhy these \d+ stocks\b",
    r"\b\d+ stocks? (to|that|on|for)\b",
    r"\bunusual options activity\b",
    r"\boptions (frenzy|market dynamics|activity)\b",
    r"\bwhat (the )?whales are doing\b",
    r"\bsmart money is betting\b",
    r"\btop (gainers|losers|movers)\b",
    r"\bprice over earnings overview\b",
    r"\bp/e ratio insights\b",
    r"\bhow much (you would|would you) have made\b",
    r"\bif you (had )?invested \$",
]
NOISE = re.compile("|".join(f"(?:{p})" for p in _NOISE), re.IGNORECASE)


def max_tags() -> int:
    return int(os.environ.get("NEWS_MAX_TAGS", "5"))


def skip_reason(headline: str, symbols: list[str], tag_limit: int | None = None) -> str | None:
    """Why this article should not go to the LLM, or None to analyse it."""
    tag_limit = max_tags() if tag_limit is None else tag_limit
    if len(symbols) > tag_limit:
        return f"tags {len(symbols)} symbols (> {tag_limit}): market roundup"
    if match := NOISE.search(headline):
        return f"listicle/auto headline: {match.group(0)!r}"
    return None
