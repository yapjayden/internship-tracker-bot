"""Entrypoint invoked by the GitHub Actions cron job. Does one full pass:
watch -> route -> extract -> research (parallel, interviews only) -> track
-> notify, then exits. No loop, no polling — the schedule is external.
"""

from __future__ import annotations

import asyncio
import logging

from core import gmail_watcher, router_agent, state
from core.config import load_settings
from core.models import Category

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CURSOR_KEY = "gmail"


async def run() -> None:
    settings = load_settings()

    cursor = state.read_cursor(CURSOR_KEY)
    emails, next_cursor = await gmail_watcher.fetch_new_emails(settings, cursor)
    logger.info("Fetched %d new email(s)", len(emails))

    # Classification is independent per email, so fan it out rather than
    # walking the inbox serially. return_exceptions keeps one bad email from
    # sinking the whole run.
    routes = await asyncio.gather(
        *(router_agent.classify(settings, email) for email in emails),
        return_exceptions=True,
    )

    for email, route in zip(emails, routes):
        if isinstance(route, Exception):
            logger.error("Routing failed for %r: %s", email.subject, route)
            continue
        if route.category == Category.NOT_RELEVANT:
            continue

        # TODO Stage 4: extractor_agent.extract for relevant categories.
        # TODO Stage 7/8: fan out research_agent per company, interviews only.
        # TODO Stage 5: tracker.append_row.  Stage 6: notifier.notify_new_item.
        logger.info("Relevant (%s): %s", route.category.value, email.subject)

    # Only advance the cursor after a clean pass, so a mid-run crash re-reads
    # the same messages next time rather than silently skipping them.
    state.write_cursor(CURSOR_KEY, next_cursor)


if __name__ == "__main__":
    asyncio.run(run())
