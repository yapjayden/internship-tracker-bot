"""Gmail read-only watcher.

Contract: given a cursor representing "last checked" state, return every new
message since then as a list of `Email`, plus an updated cursor to persist
for next time. No classification or extraction happens here — this module's
only job is "what's new."

The cursor is the internalDate (epoch milliseconds) of the newest message
already seen. Gmail's historyId would be the more idiomatic cursor, but it
expires after roughly a week of inactivity and needs a full re-sync when it
does; a timestamp degrades more gracefully for a cron job that might be
paused, and costs one extra search query per run.

Auth: OAuth installed-app flow, read-only Gmail scope. The refresh token is
minted once via scripts/gmail_oauth_setup.py and stored as
GMAIL_REFRESH_TOKEN.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import os
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr

from google.auth.exceptions import RefreshError
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

from core.config import Settings, get_env
from core.models import Email, MailSource

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# How far back to look on the very first run, when there's no cursor yet.
# One day suits steady-state operation, but it is far too narrow to tell
# whether the router works on real mail — most inboxes contain no application
# email on a given day. Raise GMAIL_LOOKBACK_DAYS when validating.
DEFAULT_LOOKBACK_DAYS = 1

# Cap per run so a first run against a busy mailbox can't fan out into
# hundreds of downstream Gemini calls. It defers work rather than dropping it:
# the oldest messages are processed first and the cursor advances only past
# those, so a backlog drains over successive runs.
DEFAULT_MAX_MESSAGES = 25

# Safety stop when listing message ids. Ids are cheap, but an unbounded loop
# over a mailbox with a very old cursor would still stall a cron run.
MAX_LIST_PAGES = 10


def _lookback() -> timedelta:
    raw = get_env("GMAIL_LOOKBACK_DAYS")
    days = int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_LOOKBACK_DAYS
    return timedelta(days=days)


def _max_messages() -> int:
    raw = get_env("GMAIL_MAX_MESSAGES")
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_MAX_MESSAGES


# Gmail's own tab classification, used as a free pre-filter before any Gemini
# call. A real inbox is overwhelmingly promotions — newsletters, receipts,
# retail offers — and every one of them otherwise costs a router request
# against a small daily quota, and crowds out application mail under the
# per-run cap.
#
# This is a recall/cost trade-off, not a free win: recruiting mail sent
# through marketing infrastructure can land in Promotions, and excluding the
# tab would miss it. Set GMAIL_QUERY_FILTER to "none" to search everything.
DEFAULT_QUERY_FILTER = "-category:promotions -category:social"


def _query_filter() -> str:
    # get_env strips whitespace out of values, which would mangle a query, so
    # read the raw variable and only trim the ends.
    #
    # Empty means "use the default", not "search everything". A GitHub Actions
    # `env:` entry bound to an unset repository variable arrives as an empty
    # string rather than being absent, so treating empty as "no filter" would
    # quietly disable the promotions exclusion in CI and flood the router with
    # newsletters. Disabling is deliberate and explicit: set it to "none".
    raw = (os.environ.get("GMAIL_QUERY_FILTER") or "").strip()
    if not raw:
        return DEFAULT_QUERY_FILTER
    return "" if raw.lower() == "none" else raw

EXPIRED_TOKEN_HELP = """\
GMAIL_REFRESH_TOKEN is expired or revoked.

The usual cause is not the token itself: while the OAuth consent screen is in
"Testing" publishing status, Google expires every refresh token after 7 days.
A cron job that runs for a week and then stops is the symptom.

Fix it once, in the Google Cloud console for this project:

  APIs & Services -> OAuth consent screen -> Publish app

Publishing without Google's verification review is fine for personal use. You
will see an "unverified app" interstitial during consent — click Advanced,
then "Go to <app> (unsafe)". Refresh tokens stop expiring once published.

Then mint a replacement, since the current one is already dead:

  python -m scripts.gmail_oauth_setup

and paste the new value into .env as GMAIL_REFRESH_TOKEN.

Other causes, if the app is already published: the Google account password
changed, access was withdrawn at myaccount.google.com/permissions, or the
OAuth client was deleted or had its secret reset.\
"""


def _build_service(settings: Settings):
    creds = Credentials(
        token=None,
        refresh_token=settings.gmail_refresh_token,
        client_id=settings.gmail_client_id,
        client_secret=settings.gmail_client_secret,
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )
    # cache_discovery=False avoids a noisy warning on ephemeral CI runners.
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decode_body(payload: dict) -> str:
    """Pull plain text out of a Gmail payload, walking multipart bodies.
    Prefers text/plain; falls back to text/html only if that's all there is."""

    def walk(part: dict) -> tuple[str | None, str | None]:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain":
                return decoded, None
            if mime == "text/html":
                return None, decoded

        plain = html = None
        for sub in part.get("parts", []):
            sub_plain, sub_html = walk(sub)
            plain = plain or sub_plain
            html = html or sub_html
        return plain, html

    plain, html = walk(payload)
    return plain or html or ""


def _to_email(message: dict) -> Email:
    headers = {h["name"].lower(): h["value"] for h in message["payload"].get("headers", [])}
    _, sender_addr = parseaddr(headers.get("from", ""))
    internal_ms = int(message["internalDate"])

    return Email(
        source=MailSource.GMAIL,
        message_id=message["id"],
        sender=sender_addr or headers.get("from", ""),
        subject=headers.get("subject", "(no subject)"),
        received_at=datetime.fromtimestamp(internal_ms / 1000, tz=timezone.utc),
        body_text=_decode_body(message["payload"]),
    )


def _fetch_sync(settings: Settings, cursor: str | None) -> tuple[list[Email], str]:
    service = _build_service(settings)

    max_messages = _max_messages()

    if cursor:
        after_epoch_s = int(cursor) // 1000
        cursor_ms = int(cursor)
    else:
        lookback = _lookback()
        after_epoch_s = int((datetime.now(timezone.utc) - lookback).timestamp())
        cursor_ms = after_epoch_s * 1000
        logger.info("No cursor; looking back %s", lookback)

    # Gmail's `after:` is second-granularity and inclusive, so the exact-match
    # boundary message comes back again — filtered out by internalDate below.
    query = f"after:{after_epoch_s}"
    query_filter = _query_filter()
    if query_filter:
        query = f"{query} {query_filter}"
    logger.info("Gmail query: %s", query)

    # List every id in the window before deciding which to process. Ids are
    # cheap — one request per 500, no message bodies — and this is what lets
    # the cap defer work rather than lose it.
    stubs: list[dict] = []
    page_token = None
    for _ in range(MAX_LIST_PAGES):
        listing = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=500, pageToken=page_token)
            .execute()
        )
        stubs.extend(listing.get("messages", []))
        page_token = listing.get("nextPageToken")
        if not page_token:
            break
    else:
        logger.warning(
            "Stopped listing after %d pages; the backlog is larger than this "
            "run can see. It will drain over subsequent runs.",
            MAX_LIST_PAGES,
        )

    # Gmail returns newest first. Take the OLDEST slice, not the newest: the
    # cursor advances only to the newest message actually processed, so
    # whatever is left is still ahead of it and arrives on the next run. Taking
    # the newest instead would jump the cursor over the remainder and lose it —
    # silently dropping an interview invite sitting behind a burst of mail.
    backlog = len(stubs)
    if backlog > max_messages:
        stubs = stubs[-max_messages:]
        logger.info(
            "%d message(s) waiting; processing the oldest %d this run, "
            "the rest follow next run.",
            backlog, max_messages,
        )

    emails: list[Email] = []
    newest_ms = cursor_ms

    for stub in stubs:
        message = (
            service.users()
            .messages()
            .get(userId="me", id=stub["id"], format="full")
            .execute()
        )
        internal_ms = int(message["internalDate"])
        if internal_ms <= cursor_ms:
            continue
        emails.append(_to_email(message))
        newest_ms = max(newest_ms, internal_ms)

    emails.sort(key=lambda e: e.received_at)
    return emails, str(newest_ms)


async def fetch_new_emails(settings: Settings, cursor: str | None) -> tuple[list[Email], str]:
    """Return (new_emails, next_cursor).

    `cursor` is the internalDate in epoch ms of the newest message already
    processed. Pass None on first run to look back GMAIL_LOOKBACK_DAYS.
    """
    # googleapiclient is synchronous; keep it off the event loop.
    try:
        return await asyncio.to_thread(_fetch_sync, settings, cursor)
    except RefreshError as exc:
        # The raw error is a 25-frame traceback ending in "invalid_grant",
        # which says nothing about the 7-day expiry that almost always causes
        # it. Replace it with the fix.
        raise RuntimeError(EXPIRED_TOKEN_HELP) from exc
