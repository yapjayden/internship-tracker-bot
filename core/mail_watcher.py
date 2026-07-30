"""Fan-in over every mail source. Runs each source's watcher concurrently
and merges results into one list of `Email`, so the rest of the pipeline
never deals with per-source logic or cursors directly.
"""

from __future__ import annotations

import asyncio

from core import gmail_watcher, outlook_watcher
from core.config import Settings
from core.models import Email


class MailCursors:
    """Per-source cursor state. Persisted wherever run_once.py decides
    (Stage 2 will pick: a tracker-adjacent Sheet tab, or a small state file
    committed by the GitHub Actions job)."""

    def __init__(self, gmail: str | None = None, outlook: str | None = None):
        self.gmail = gmail
        self.outlook = outlook


async def fetch_new_emails(settings: Settings, cursors: MailCursors) -> tuple[list[Email], MailCursors]:
    (gmail_emails, gmail_cursor), (outlook_emails, outlook_cursor) = await asyncio.gather(
        gmail_watcher.fetch_new_emails(settings, cursors.gmail),
        outlook_watcher.fetch_new_emails(settings, cursors.outlook),
    )
    merged = [*gmail_emails, *outlook_emails]
    return merged, MailCursors(gmail=gmail_cursor, outlook=outlook_cursor)
