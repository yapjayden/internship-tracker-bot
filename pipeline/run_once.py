"""Entrypoint invoked by the GitHub Actions cron job. Does one full pass:
watch -> route -> extract -> research (parallel, interviews only) -> track
-> notify, then exits. No loop, no polling — the schedule is external.
"""

from __future__ import annotations

import asyncio
import logging

from core import extractor_agent, gmail_watcher, router_agent, state, tracker
from core.config import load_settings
from core.models import Category, Email

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CURSOR_KEY = "gmail"


async def run() -> None:
    settings = load_settings()

    # Creates the tabs and headers on a fresh spreadsheet, so the first run
    # needs no manual setup beyond sharing the sheet.
    await tracker.ensure_ready(settings)

    cursor = await state.read_cursor(settings, CURSOR_KEY)
    emails, next_cursor = await gmail_watcher.fetch_new_emails(settings, cursor)
    logger.info("Fetched %d new email(s)", len(emails))

    # Classification is independent per email, so fan it out rather than
    # walking the inbox serially. return_exceptions keeps one bad email from
    # sinking the whole run.
    routes = await asyncio.gather(
        *(router_agent.classify(settings, email) for email in emails),
        return_exceptions=True,
    )

    relevant: list[tuple[Email, Category]] = []
    for email, route in zip(emails, routes):
        if isinstance(route, BaseException):
            logger.error("Routing failed for %r: %s", email.subject, route)
            continue
        if route.category == Category.NOT_RELEVANT:
            continue
        relevant.append((email, route.category))

    logger.info("%d of %d email(s) relevant", len(relevant), len(emails))

    # Extraction is per-email and independent, same as routing, and the shared
    # limiter keeps the fan-out from becoming a quota burst.
    extractions = await asyncio.gather(
        *(extractor_agent.extract(settings, email, category) for email, category in relevant),
        return_exceptions=True,
    )

    for (email, category), details in zip(relevant, extractions):
        if isinstance(details, BaseException):
            # One unreadable email must not sink the run — the rest still have
            # somewhere to go.
            logger.error("Extraction failed for %r: %s", email.subject, details)
            continue

        # TODO Stage 7/8: fan out research_agent per company, interviews only.
        # TODO Stage 6: notifier.notify_new_item.
        try:
            # Serialised inside the tracker: concurrent upserts for the same
            # application would otherwise each append their own row.
            is_new = await tracker.upsert_row(settings, category, details, email)
        except Exception as exc:
            logger.error("Tracker write failed for %r: %s", email.subject, exc)
            continue

        logger.info(
            "%s %s / %s / %s / key_date=%s",
            "Added" if is_new else "Updated",
            details.company, details.role, details.status.value, details.key_date,
        )

    # Only advance the cursor after a clean pass, so a mid-run crash re-reads
    # the same messages next time rather than silently skipping them.
    await state.write_cursor(settings, CURSOR_KEY, next_cursor)


if __name__ == "__main__":
    asyncio.run(run())
