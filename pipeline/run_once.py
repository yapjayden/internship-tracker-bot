"""Entrypoint invoked by the GitHub Actions cron job. Does one full pass:
watch -> route -> extract -> research (parallel, interviews only) -> track
-> notify, then exits. No loop, no polling — the schedule is external.
"""

from __future__ import annotations

import asyncio
import logging

from core import mail_watcher, notifier, router_agent, tracker
from core.config import load_settings
from core.mail_watcher import MailCursors
from core.models import Category

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def run() -> None:
    settings = load_settings()

    # TODO Stage 2: load persisted cursors instead of starting fresh each run.
    cursors = MailCursors()
    emails, cursors = await mail_watcher.fetch_new_emails(settings, cursors)
    logger.info("Fetched %d new email(s)", len(emails))

    for email in emails:
        result = await router_agent.classify(settings, email)
        if result.category == Category.NOT_RELEVANT:
            continue
        # TODO Stage 4+: extract, Stage 7/8: fan out research agents for
        # interviews only, Stage 5: tracker.append_row, Stage 6: notifier.notify_new_item.

    # TODO Stage 2: persist `cursors` for next run.


if __name__ == "__main__":
    asyncio.run(run())
