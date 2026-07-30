"""Outlook read-only watcher, via Microsoft Graph API.

Mirrors gmail_watcher.py's contract exactly: given a cursor, return new
`Email`s since then plus an updated cursor. Router/extractor/tracker never
need to know a message came from Outlook rather than Gmail.

Auth: Azure AD (Microsoft Entra) app registration, delegated `Mail.Read`
scope (read-only). The refresh token is minted once via a local interactive
script (see scripts/outlook_oauth_setup.py, added at Stage 2) and stored as
OUTLOOK_REFRESH_TOKEN.
"""

from __future__ import annotations

from core.config import Settings
from core.models import Email


async def fetch_new_emails(settings: Settings, cursor: str | None) -> tuple[list[Email], str]:
    """Return (new_emails, next_cursor).

    `cursor` is an opaque string (e.g. a Graph delta link) representing the
    last point this watcher successfully read up to. Pass None on first run.
    """
    raise NotImplementedError("Stage 2: implement Microsoft Graph API polling here")
