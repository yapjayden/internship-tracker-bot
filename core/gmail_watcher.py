"""Gmail read-only watcher.

Contract: given a cursor representing "last checked" state, return every new
message since then as a list of `Email`, plus an updated cursor to persist
for next time. No classification or extraction happens here — this module's
only job is "what's new."

Auth: OAuth installed-app flow, read-only Gmail scope. The refresh token is
minted once via a local interactive script (see scripts/gmail_oauth_setup.py,
added at Stage 2) and stored as GMAIL_REFRESH_TOKEN.
"""

from __future__ import annotations

from core.config import Settings
from core.models import Email


async def fetch_new_emails(settings: Settings, cursor: str | None) -> tuple[list[Email], str]:
    """Return (new_emails, next_cursor).

    `cursor` is an opaque string (e.g. Gmail historyId) representing the last
    point this watcher successfully read up to. Pass None on first run.
    """
    raise NotImplementedError("Stage 2: implement Gmail API polling here")
