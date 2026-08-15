"""Telegram push notifications, sent via a plain HTTP POST to the Bot API's
sendMessage endpoint. Deliberately NOT python-telegram-bot's polling client —
this only ever pushes, it never listens, so it has no business holding a
long-running connection open from inside a GitHub Actions job.

A failed notification must never sink a pipeline run: the tracker row is the
durable record, and a message that did not send is worth a log line, not a
lost run. notify_new_item returns a bool rather than raising.
"""

from __future__ import annotations

import asyncio
import html
import logging
import os
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from core.config import Settings, require_setting
from core.models import (
    ApplicationStatus,
    Category,
    Email,
    ExtractedDetails,
    ResearchBrief,
)

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT_S = 15.0
MAX_ATTEMPTS = 3

# Telegram hard-rejects messages over 4096 characters. A research brief is the
# only field that can realistically approach that.
MAX_MESSAGE_CHARS = 4096
MAX_SOURCES_SHOWN = 4
# Headroom against the HTML escaping applied after the budget is computed:
# one "&" becomes five characters.
SAFETY_MARGIN = 200
# Source titles can be a whole press-release headline. The URL is the part
# that has to survive intact for the link to work.
MAX_SOURCE_TITLE = 60

# Which statuses are worth interrupting someone for. Acknowledgements are the
# bulk of the volume and carry no news — they still land in the tracker, they
# just do not buzz a phone. Override with NOTIFY_STATUSES as a comma-separated
# list, or "all".
DEFAULT_NOTIFY_STATUSES = frozenset(
    {
        ApplicationStatus.ASSESSMENT,
        ApplicationStatus.INTERVIEW,
        ApplicationStatus.OFFER,
        ApplicationStatus.REJECTED,
        ApplicationStatus.ACTION_NEEDED,
    }
)

# Times reach here in UTC — Gmail's internalDate is UTC, CI runs in UTC, and
# the extractor normalises to it. Showing that raw makes the reader convert an
# interview time in their head. Render in the timezone they actually live in.
DEFAULT_DISPLAY_TZ = "Asia/Singapore"

STATUS_PREFIX = {
    ApplicationStatus.APPLIED: "📮 Applied",
    ApplicationStatus.ASSESSMENT: "📝 Assessment",
    ApplicationStatus.INTERVIEW: "🎯 Interview",
    ApplicationStatus.OFFER: "🎉 Offer",
    ApplicationStatus.REJECTED: "💀 Rejected",
    ApplicationStatus.ACTION_NEEDED: "⚠️ Action needed",
    ApplicationStatus.UNKNOWN: "❓ Update",
}


def notify_statuses() -> frozenset[ApplicationStatus]:
    raw = os.environ.get("NOTIFY_STATUSES", "").strip()
    if not raw:
        return DEFAULT_NOTIFY_STATUSES
    if raw.lower() == "all":
        return frozenset(ApplicationStatus)

    chosen = set()
    for name in raw.split(","):
        name = name.strip().lower()
        if not name:
            continue
        try:
            chosen.add(ApplicationStatus(name))
        except ValueError:
            logger.warning("Ignoring unknown status %r in NOTIFY_STATUSES", name)
    # An entirely unparseable value should not silence the bot outright.
    return frozenset(chosen) or DEFAULT_NOTIFY_STATUSES


def should_notify(status: ApplicationStatus) -> bool:
    return status in notify_statuses()


def _display_tz() -> ZoneInfo:
    name = os.environ.get("DISPLAY_TIMEZONE", "").strip() or DEFAULT_DISPLAY_TZ
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError:
        logger.warning("Unknown DISPLAY_TIMEZONE %r, falling back to UTC", name)
        return ZoneInfo("UTC")


def format_key_date(value: datetime) -> str:
    # A naive datetime means the email gave a wall-clock time with no zone. The
    # extractor is told to read those as Singapore time, so label it rather
    # than silently treating it as UTC and shifting it by eight hours.
    tz = _display_tz()
    if value.tzinfo is None:
        return value.strftime("%a %d %b %Y, %H:%M")
    return value.astimezone(tz).strftime("%a %d %b %Y, %H:%M %Z")


def _esc(text: str) -> str:
    """Escape for Telegram's HTML parse mode.

    HTML rather than MarkdownV2 because company and role names are free text
    lifted out of an email. MarkdownV2 requires escaping eighteen different
    characters and rejects the whole message on a single miss.
    """
    return html.escape(text or "", quote=False)


def _truncate_escaped(text: str, limit: int) -> str:
    """Shorten already-escaped text without splitting an HTML entity.

    Cutting inside "&amp;" leaves "&am", which Telegram rejects as malformed
    HTML — losing the entire message rather than the tail of one field.
    """
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text

    cut = text[:limit]
    # Prefer a word boundary, then step back off any dangling entity.
    if " " in cut:
        cut = cut.rsplit(" ", 1)[0]
    ampersand = cut.rfind("&")
    if ampersand != -1 and ";" not in cut[ampersand:]:
        cut = cut[:ampersand]
    return cut.rstrip() + "…"


def _short_source(source: str) -> str:
    """Trim a long headline off a "Title — URL" pair, keeping the URL whole."""
    title, separator, url = source.rpartition(" — ")
    if not separator:
        return source
    if len(title) > MAX_SOURCE_TITLE:
        title = title[:MAX_SOURCE_TITLE].rstrip() + "…"
    return f"{title} — {url}"


def build_message(
    category: Category,
    extracted: ExtractedDetails,
    email: Email,
    research_brief: ResearchBrief | None = None,
    is_new: bool = True,
) -> str:
    prefix = STATUS_PREFIX.get(extracted.status, "❓ Update")
    verb = "New" if is_new else "Updated"

    lines = [
        f"<b>{_esc(prefix)}</b> — {verb}",
        "",
        f"<b>{_esc(extracted.company)}</b>",
        _esc(extracted.role),
    ]

    if extracted.key_date:
        lines.append(f"🗓 {_esc(format_key_date(extracted.key_date))}")

    if extracted.next_steps:
        lines += ["", f"➡️ {_esc(extracted.next_steps)}"]

    footer = ["", f"<i>{_esc(email.subject[:120])}</i>"]

    if research_brief and research_brief.brief_text.strip():
        source_lines: list[str] = []
        if research_brief.sources:
            # A brief the reader might repeat to an interviewer has to be
            # checkable.
            source_lines = ["", "<b>Sources</b>"] + [
                _esc(_short_source(s)) for s in research_brief.sources[:MAX_SOURCES_SHOWN]
            ]
        else:
            # Say so rather than let unsourced recall look researched.
            source_lines = ["", "<i>No sources found — treat as unverified.</i>"]

        # Give the brief whatever space is actually left, rather than a fixed
        # budget. The brief ends with the suggested questions — the most
        # useful part — so a too-tight constant silently cuts exactly what the
        # reader wanted, even when the message is only half full.
        fixed = "\n".join(lines + ["", "<b>Prep brief</b>"] + source_lines + footer)
        available = MAX_MESSAGE_CHARS - len(fixed) - SAFETY_MARGIN

        # Escape first, then measure. Escaping expands text — one "&" becomes
        # five characters — so budgeting against the raw length can still
        # overflow, and the blind cut that follows could land inside an entity
        # and make Telegram reject the whole message as malformed HTML.
        brief = _truncate_escaped(_esc(research_brief.brief_text.strip()), available)

        lines += ["", "<b>Prep brief</b>", brief] + source_lines

    lines += footer

    message = "\n".join(lines)
    if len(message) > MAX_MESSAGE_CHARS:
        message = message[: MAX_MESSAGE_CHARS - 1] + "…"
    return message


def _rejection_hint(body: str, token: str, chat_id: str) -> str:
    """Turn Telegram's 4xx wording into the thing to actually change.

    Its messages describe the API's view, not the mistake. "The bot can't send
    messages to the bot" is what you get for pasting the token's leading
    number as the chat id — a copy-paste slip that reads like a permissions
    problem.
    """
    lowered = body.lower()
    bot_id = token.split(":", 1)[0]

    if "can't send messages to the bot" in lowered or chat_id == bot_id:
        return (
            f"TELEGRAM_CHAT_ID is {chat_id}, which is the bot's own id — the "
            "number before the colon in TELEGRAM_BOT_TOKEN. You need your own "
            "id. Run: python -m scripts.telegram_chat_id"
        )
    if "chat not found" in lowered:
        return (
            f"No chat with id {chat_id}. If it is your user id, message the bot "
            "once first — Telegram forbids a bot from opening a conversation. "
            "Run: python -m scripts.telegram_chat_id"
        )
    if "bot was blocked" in lowered:
        return "You have blocked this bot. Unblock it in Telegram."
    if "unauthorized" in lowered:
        return (
            "TELEGRAM_BOT_TOKEN was rejected. Copy the whole value from "
            "@BotFather, including the part before the colon."
        )
    if "can't parse entities" in lowered:
        return (
            "Telegram could not parse the message HTML. This is a formatting "
            "bug, not a config problem — please report the message that failed."
        )
    return ""


async def send_message(settings: Settings, text: str) -> bool:
    """POST one message. Returns True on success, False once it gives up."""
    token = require_setting(settings.telegram_bot_token, "TELEGRAM_BOT_TOKEN")
    chat_id = require_setting(settings.telegram_chat_id, "TELEGRAM_CHAT_ID")
    url = f"{API_BASE}/bot{token}/sendMessage"

    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        # These are status pings; an unfurled careers page would dwarf them.
        "disable_web_page_preview": True,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(url, json=payload)
            except httpx.RequestError as exc:
                logger.warning(
                    "Telegram request failed (%d/%d): %s", attempt, MAX_ATTEMPTS, exc
                )
                if attempt == MAX_ATTEMPTS:
                    return False
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 200:
                return True

            if response.status_code == 429:
                # Telegram states how long to wait; guessing burns the budget.
                try:
                    retry_after = float(
                        response.json().get("parameters", {}).get("retry_after", 2**attempt)
                    )
                except ValueError:
                    retry_after = float(2**attempt)
                logger.warning("Telegram rate limited, waiting %.0fs", retry_after)
                await asyncio.sleep(min(retry_after, 60.0))
                continue

            body = response.text[:200]
            if 400 <= response.status_code < 500:
                # A bad token, a wrong chat_id, or malformed HTML fails
                # identically however many times it is retried.
                logger.error(
                    "Telegram rejected the message (%d): %s", response.status_code, body
                )
                hint = _rejection_hint(body, token, chat_id)
                if hint:
                    logger.error("%s", hint)
                return False

            logger.warning(
                "Telegram %d (%d/%d): %s", response.status_code, attempt, MAX_ATTEMPTS, body
            )
            if attempt == MAX_ATTEMPTS:
                return False
            await asyncio.sleep(2**attempt)

    return False


async def notify_new_item(
    settings: Settings,
    category: Category,
    extracted: ExtractedDetails,
    email: Email,
    research_brief: ResearchBrief | None = None,
    is_new: bool = True,
) -> bool:
    """Push one tracker update, if its status warrants interrupting anyone."""
    if not should_notify(extracted.status):
        logger.info(
            "Not notifying for %s / %s (%s is silent)",
            extracted.company, extracted.role, extracted.status.value,
        )
        return False

    text = build_message(category, extracted, email, research_brief, is_new)
    sent = await send_message(settings, text)
    if sent:
        logger.info(
            "Notified: %s / %s (%s)",
            extracted.company, extracted.role, extracted.status.value,
        )
    return sent
