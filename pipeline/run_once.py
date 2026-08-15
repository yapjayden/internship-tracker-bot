"""Entrypoint invoked by the GitHub Actions cron job. Does one full pass:
watch -> route -> extract -> research (parallel, interviews only) -> track
-> notify, then exits. No loop, no polling — the schedule is external.
"""

from __future__ import annotations

import asyncio
import logging

from core import (
    extractor_agent,
    gmail_watcher,
    notifier,
    research_agent,
    router_agent,
    state,
    tracker,
)
from core.config import Settings, load_settings
from core.models import Category, Email, ExtractedDetails, ResearchBrief

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CURSOR_KEY = "gmail"


async def research_interviews(
    settings: Settings,
    extracted: list[tuple[Email, Category, ExtractedDetails]],
) -> dict[frozenset[str], ResearchBrief]:
    """Research every company with a new interview, all at once.

    Returns briefs keyed by company rather than by email, because the mapping
    is genuinely many-to-one: a company can appear in several emails in one
    batch, and the brief is about the employer, not the message.

    Three filters, each cutting real cost:

    - Interviews only. An assessment invitation needs a deadline, not a
      briefing on company culture.
    - One brief per company, by tracker.company_key, so "Grab" and "Grab
      Holdings" in the same batch research once.
    - Nothing already briefed in the sheet. An interview thread runs to
      several emails — invitation, reschedule, confirmation — and each would
      otherwise redo three searches and a Gemini call.
    """
    wanted: dict[frozenset[str], tuple[str, str]] = {}
    for _, category, details in extracted:
        if category != Category.INTERVIEW:
            continue
        wanted.setdefault(
            tracker.company_key(details.company), (details.company, details.role)
        )

    if not wanted:
        return {}

    try:
        already = await tracker.companies_with_briefs(settings)
    except Exception as exc:
        # Not fatal: researching again costs quota, skipping the whole step
        # costs the feature. Prefer the brief.
        logger.warning("Could not read existing briefs (%s); researching all", exc)
        already = set()

    todo = {key: value for key, value in wanted.items() if key not in already}
    skipped = len(wanted) - len(todo)
    if skipped:
        logger.info("Skipping %d company/companies already briefed", skipped)
    if not todo:
        return {}

    logger.info("Researching %d compan(y/ies) in parallel", len(todo))

    # Independent by construction — no shared state between instances — so a
    # plain gather is safe. Pacing is handled inside: gemini has its own rate
    # limiter, and Tavily's free pool is generous relative to a single batch.
    results = await asyncio.gather(
        *(
            research_agent.research_company(settings, company, role)
            for company, role in todo.values()
        ),
        return_exceptions=True,
    )

    briefs: dict[frozenset[str], ResearchBrief] = {}
    for key, (company, _), result in zip(todo.keys(), todo.values(), results):
        if isinstance(result, BaseException):
            # A missing brief degrades the notification; it must not cost the
            # interview row or the alert that an interview exists at all.
            logger.error("Research failed for %s: %s", company, result)
            continue
        briefs[key] = result

    return briefs


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
        # Log every verdict, not only the keepers. A run that reports "0 of 25
        # relevant" is indistinguishable from a broken router unless you can
        # see what it decided and why.
        logger.info(
            "  %-18s %.2f  %s",
            route.category.value, route.confidence, email.subject[:60],
        )
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

    extracted: list[tuple[Email, Category, ExtractedDetails]] = []
    for (email, category), details in zip(relevant, extractions):
        if isinstance(details, BaseException):
            # One unreadable email must not sink the run — the rest still have
            # somewhere to go.
            logger.error("Extraction failed for %r: %s", email.subject, details)
            continue
        extracted.append((email, category, details))

    briefs = await research_interviews(settings, extracted)

    for email, category, details in extracted:
        brief = briefs.get(tracker.company_key(details.company))
        try:
            # Serialised inside the tracker: concurrent upserts for the same
            # application would otherwise each append their own row. The brief
            # rides along so the row is written once, not written then patched.
            is_new = await tracker.upsert_row(
                settings, category, details, email, research_brief=brief
            )
        except Exception as exc:
            logger.error("Tracker write failed for %r: %s", email.subject, exc)
            continue

        logger.info(
            "%s %s / %s / %s / key_date=%s",
            "Added" if is_new else "Updated",
            details.company, details.role, details.status.value, details.key_date,
        )

        # Notify only after the row is safely written. The sheet is the
        # durable record; a ping about something that failed to persist would
        # point at a tracker that does not contain it.
        try:
            await notifier.notify_new_item(
                settings, category, details, email,
                research_brief=brief, is_new=is_new,
            )
        except Exception as exc:
            # notify_new_item already swallows delivery failures, so reaching
            # here means a config problem — still not worth losing the run.
            logger.error("Notification failed for %r: %s", email.subject, exc)

    # Only advance the cursor after a clean pass, so a mid-run crash re-reads
    # the same messages next time rather than silently skipping them.
    await state.write_cursor(settings, CURSOR_KEY, next_cursor)


if __name__ == "__main__":
    asyncio.run(run())
