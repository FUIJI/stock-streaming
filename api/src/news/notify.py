"""Where important news goes besides the web page (Phase 3d, D7).

Only the in-page bell/toast exists today (the browser polls GET /news). This seam is for
a push channel later (Telegram, LINE): implement `Notifier.notify` and return it from
`notifier_from_env`. The worker calls it once per article, right after the AI scored it.
"""

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger("api.news.notify")

ALERT_IMPORTANCE = 4  # the same bar as the bell (frontend/src/lib/news.ts IMPORTANT)


@dataclass(frozen=True)
class Alert:
    news_id: int
    symbols: list[str]  # tracked symbols the article is about
    importance: int
    headline: str
    headline_th: str | None
    url: str | None


class Notifier(Protocol):
    async def notify(self, alert: Alert) -> None: ...


class NullNotifier:
    """No push channel configured."""

    async def notify(self, alert: Alert) -> None:
        log.debug("Important news %s (no notifier): %s", alert.news_id, alert.headline)


def notifier_from_env() -> Notifier:
    return NullNotifier()
