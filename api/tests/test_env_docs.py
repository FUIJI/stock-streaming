"""Every news/AI setting the API reads is documented in .env.example (CLAUDE.md: keep it current)."""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PREFIXES = ("LLM_", "NEWS_", "DIGEST_", "OUTLOOK_", "GEMINI_API_KEY")
INTERNAL = {"GEMINI_BASE_URL"}  # test hook, not a setting


def read_by_code() -> set[str]:
    names = set()
    for path in (ROOT / "api" / "src").rglob("*.py"):
        names |= set(re.findall(r'environ\.get\("([A-Z0-9_]+)"', path.read_text()))
    return {n for n in names if n.startswith(PREFIXES)} - INTERNAL


def test_news_settings_are_documented_in_both_env_examples():
    names = read_by_code()
    assert "LLM_MODEL" in names and "DIGEST_TIMES_ET" in names  # the scan works
    for example in (ROOT / ".env.example", ROOT / "deploy" / "env.vm.example"):
        text = example.read_text()
        missing = sorted(n for n in names if not re.search(rf"\b{n}=", text))
        assert not missing, f"{example.name} lacks {missing}"


def test_the_lite_stack_passes_every_news_setting_to_the_api():
    """Otherwise a value set in .env on the VM silently never reaches the container."""
    compose = (ROOT / "docker-compose.prod.yml").read_text()
    missing = sorted(n for n in read_by_code() if f"{n}: ${{{n}" not in compose)
    assert not missing, f"docker-compose.prod.yml does not pass {missing}"
