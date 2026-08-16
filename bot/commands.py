"""Telegram command handling for the query side.

Pure functions from a list of Application to a list of message strings. No
network, no Telegram types, no Sheets — so every command can be rendered and
checked offline, and the transport in app.py stays a thin shell.

Returning a list rather than one string because Telegram caps a message at
4096 characters and a full application list will pass that once there are a
few dozen rows. Splitting on a group boundary keeps the output readable
instead of guillotining a row in half.
"""

from __future__ import annotations

import html
from datetime import datetime, timedelta, timezone

from core.models import Application, ApplicationStatus
from core.notifier import MAX_MESSAGE_CHARS, format_brief, format_key_date

# Left over for the HTML tags the renderers add after budgeting.
CHUNK_BUDGET = MAX_MESSAGE_CHARS - 200

UPCOMING_WINDOW = timedelta(days=7)


def _esc(text: str) -> str:
    return html.escape(text or "", quote=False)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_future(value: datetime | None) -> bool:
    if value is None:
        return False
    # A row edited by hand may hold a naive datetime; assume UTC rather than
    # raising in the middle of rendering a list.
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value >= _now()


# The user asked for groups that answer "what do I need to do", which is not
# the same as the raw status. A booked interview and one already sat are both
# status=interview, but only one needs preparing for — so key_date splits them.
def group_of(app: Application) -> str:
    if app.status == ApplicationStatus.OFFER:
        return "🎉 Offers"
    if app.status == ApplicationStatus.REJECTED:
        return "💀 Rejected"
    if app.status == ApplicationStatus.ACTION_NEEDED:
        return "⚠️ Action needed"
    if app.status == ApplicationStatus.INTERVIEW:
        return "🎯 Upcoming interviews" if _is_future(app.key_date) else "⏳ Awaiting outcome"
    if app.status == ApplicationStatus.ASSESSMENT:
        return "📝 Assessments to do" if _is_future(app.key_date) else "⏳ Awaiting outcome"
    if app.status == ApplicationStatus.APPLIED:
        return "📮 Awaiting response"
    return "❓ Unclear"


# Ordered by urgency: things with a deadline first, dead applications last.
GROUP_ORDER = [
    "⚠️ Action needed",
    "📝 Assessments to do",
    "🎯 Upcoming interviews",
    "⏳ Awaiting outcome",
    "🎉 Offers",
    "📮 Awaiting response",
    "❓ Unclear",
    "💀 Rejected",
]


def _sort_key(app: Application) -> tuple:
    """Soonest deadline first; undated rows after dated ones, alphabetical."""
    if app.key_date is None:
        return (1, datetime.max.replace(tzinfo=timezone.utc), app.company.lower())
    dated = app.key_date if app.key_date.tzinfo else app.key_date.replace(tzinfo=timezone.utc)
    return (0, dated, app.company.lower())


def _chunk(lines: list[str]) -> list[str]:
    """Pack lines into as few messages as fit."""
    messages: list[str] = []
    current: list[str] = []
    size = 0
    for line in lines:
        if size + len(line) + 1 > CHUNK_BUDGET and current:
            messages.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += len(line) + 1
    if current:
        messages.append("\n".join(current))
    return messages or ["Nothing to show."]


def _line(app: Application) -> str:
    line = f"• <b>{_esc(app.label)}</b>"
    if app.key_date:
        line += f"\n   🗓 {_esc(format_key_date(app.key_date))}"
    return line


def cmd_all(apps: list[Application]) -> list[str]:
    """Every application, grouped by what it needs from you."""
    if not apps:
        return [
            "No applications tracked yet.\n\n"
            "Rows appear here once the pipeline sees a reply from an employer."
        ]

    grouped: dict[str, list[Application]] = {}
    for app in apps:
        grouped.setdefault(group_of(app), []).append(app)

    lines = [f"<b>{len(apps)} application(s)</b>"]
    for group in GROUP_ORDER:
        members = grouped.get(group)
        if not members:
            continue
        lines.append("")
        lines.append(f"<b>{_esc(group)}</b>  ({len(members)})")
        lines += [_line(app) for app in sorted(members, key=_sort_key)]
    return _chunk(lines)


def cmd_next(apps: list[Application], days: int = 7) -> list[str]:
    """Only what is actually dated and imminent — the daily driver."""
    window_end = _now() + timedelta(days=days)
    upcoming = [
        app for app in apps
        if _is_future(app.key_date)
        and (app.key_date.replace(tzinfo=timezone.utc) if app.key_date.tzinfo is None
             else app.key_date) <= window_end
    ]
    if not upcoming:
        return [f"Nothing scheduled in the next {days} days."]

    lines = [f"<b>Next {days} days</b>  ({len(upcoming)})", ""]
    for app in sorted(upcoming, key=_sort_key):
        lines.append(f"• <b>{_esc(app.label)}</b>")
        lines.append(f"   {_esc(group_of(app))}")
        lines.append(f"   🗓 {_esc(format_key_date(app.key_date))}")
    return _chunk(lines)


def _matches(app: Application, needle: str) -> bool:
    haystack = f"{app.company} {app.department or ''} {app.role}".lower()
    return needle.lower().strip() in haystack


def cmd_brief(apps: list[Application], query: str = "") -> list[str]:
    """With no argument, list who has a brief. With one, show it.

    Listing first rather than dumping every brief: each runs to a few hundred
    words, so ten of them is ten messages nobody asked for.
    """
    with_briefs = [app for app in apps if app.research_brief.strip()]

    if not query:
        if not with_briefs:
            return [
                "No briefs yet.\n\n"
                "A brief is written when an employer invites you to an "
                "interview — not when you apply, and not for assessments."
            ]
        lines = [f"<b>{len(with_briefs)} brief(s) available</b>", ""]
        lines += [f"• {_esc(app.label)}" for app in with_briefs]
        lines += ["", "Send <code>/brief &lt;company&gt;</code> to read one."]
        return _chunk(lines)

    hits = [app for app in with_briefs if _matches(app, query)]
    if not hits:
        # Distinguish "no such application" from "no brief for it" — they lead
        # to different next steps.
        tracked = [app for app in apps if _matches(app, query)]
        if tracked:
            return [
                f"<b>{_esc(tracked[0].label)}</b> is tracked but has no brief yet.\n\n"
                f"Status: {_esc(tracked[0].status.value)}. Briefs are written "
                "at the interview stage."
            ]
        return [f"Nothing tracked matching {_esc(query)!r}. Try /all."]

    messages = []
    for app in hits[:3]:
        # Same renderer the push notification uses, so a brief read on demand
        # looks identical to the one that arrived with the invitation.
        messages += _chunk(
            [f"<b>{_esc(app.label)}</b>", "", format_brief(app.research_brief)]
        )
    if len(hits) > 3:
        messages.append(f"…and {len(hits) - 3} more match. Be more specific.")
    return messages


def cmd_find(apps: list[Application], query: str = "") -> list[str]:
    """Everything known about one company."""
    if not query:
        return ["Send <code>/find &lt;company&gt;</code>, e.g. <code>/find Shopee</code>."]

    hits = [app for app in apps if _matches(app, query)]
    if not hits:
        return [f"Nothing tracked matching {_esc(query)!r}."]

    lines = []
    for app in sorted(hits, key=_sort_key):
        lines.append(f"<b>{_esc(app.label)}</b>")
        lines.append(f"   {_esc(group_of(app))} — {_esc(app.status.value)}")
        if app.key_date:
            lines.append(f"   🗓 {_esc(format_key_date(app.key_date))}")
        if app.logged_at:
            lines.append(f"   last update {_esc(app.logged_at.strftime('%d %b %Y'))}")
        lines.append(f"   brief: {'yes' if app.research_brief.strip() else 'no'}")
        lines.append("")
    return _chunk(lines)


def cmd_stats(apps: list[Application]) -> list[str]:
    """Counts, plus the one number worth knowing: how many replied at all."""
    if not apps:
        return ["Nothing tracked yet."]

    counts: dict[ApplicationStatus, int] = {}
    for app in apps:
        counts[app.status] = counts.get(app.status, 0) + 1

    total = len(apps)
    silent = counts.get(ApplicationStatus.APPLIED, 0)
    engaged = total - silent
    offers = counts.get(ApplicationStatus.OFFER, 0)
    rejected = counts.get(ApplicationStatus.REJECTED, 0)
    decided = offers + rejected

    lines = [f"<b>{total} application(s)</b>", ""]
    for status in ApplicationStatus:
        if counts.get(status):
            lines.append(f"• {_esc(status.value)}: {counts[status]}")

    lines += ["", f"Moved past acknowledgement: {engaged}/{total}"]
    if decided:
        lines.append(f"Decided: {offers} offer(s), {rejected} rejection(s)")
    return _chunk(lines)


def cmd_stale(apps: list[Application], days: int = 21) -> list[str]:
    """Applications that were acknowledged and then went quiet.

    Worth surfacing because it is the one thing the inbox cannot tell you:
    silence leaves no email to notice.
    """
    cutoff = _now() - timedelta(days=days)
    stale = []
    for app in apps:
        if app.status != ApplicationStatus.APPLIED or app.logged_at is None:
            continue
        logged = app.logged_at if app.logged_at.tzinfo else app.logged_at.replace(tzinfo=timezone.utc)
        if logged < cutoff:
            stale.append(app)

    if not stale:
        return [f"Nothing has been silent for more than {days} days."]

    lines = [f"<b>Silent for {days}+ days</b>  ({len(stale)})", ""]
    for app in sorted(stale, key=lambda a: a.logged_at or _now()):
        lines.append(f"• <b>{_esc(app.label)}</b>")
        lines.append(f"   last heard {_esc(app.logged_at.strftime('%d %b %Y'))}")
    return _chunk(lines)


HELP = """\
<b>Internship tracker</b>

/all — every application, grouped by what it needs from you
/next — anything dated in the next 7 days
/brief — list available prep briefs
/brief &lt;company&gt; — read one
/find &lt;company&gt; — everything tracked for one employer
/stats — counts, and how many employers actually replied
/stale — acknowledged, then silent for 21+ days
/help — this

Updates arrive on their own when mail does. These commands are for asking \
in between.\
"""


def dispatch(apps: list[Application], text: str) -> list[str]:
    """Route a raw message to a command. Unknown input gets help, not silence."""
    text = (text or "").strip()
    if not text.startswith("/"):
        return [HELP]

    # Telegram appends @botname when a command is used in a group.
    command, _, argument = text.partition(" ")
    command = command.split("@", 1)[0].lower()
    argument = argument.strip()

    if command == "/all":
        return cmd_all(apps)
    if command == "/next":
        return cmd_next(apps)
    if command == "/brief":
        return cmd_brief(apps, argument)
    if command == "/find":
        return cmd_find(apps, argument)
    if command == "/stats":
        return cmd_stats(apps)
    if command == "/stale":
        return cmd_stale(apps)
    if command in {"/help", "/start"}:
        return [HELP]
    return [f"Unknown command {_esc(command)}.\n\n{HELP}"]
