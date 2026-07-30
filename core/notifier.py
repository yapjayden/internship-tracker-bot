"""Telegram push notifications, sent via a plain HTTP POST to the Bot API's
sendMessage endpoint. Deliberately NOT python-telegram-bot's polling client —
this only ever pushes, it never listens, so it has no business holding a
long-running connection open from inside a GitHub Actions job.
"""

from __future__ import annotations

from core.config import Settings
from core.models import Category, Email, ResearchBrief


async def notify_new_item(
    settings: Settings,
    category: Category,
    extracted,
    email: Email,
    research_brief: ResearchBrief | None = None,
) -> None:
    raise NotImplementedError("Stage 6: implement Telegram sendMessage push here")
