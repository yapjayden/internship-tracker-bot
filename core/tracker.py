"""The shared tracker: one row per application, stored in Google Sheets.
All agents write here — nothing agent-to-agent.

Schema is CONFIRMED: one row per application, updated in place as things
progress, rather than one row per email. That keeps the sheet readable as a
dashboard, at the cost of two problems an append-only log would not have:

1. Identity. The same application arrives as "Grab", "Grab Holdings" and
   "GrabTaxi Pte Ltd". The extractor is prompted for a short trading name;
   _same_application below closes the rest of the gap.
2. Ordering. Mail does not arrive in the order events happened, so a late
   acknowledgement must not walk an application back from `interview` to
   `applied`. Status only ever moves forward, by STATUS_RANK.
"""

from __future__ import annotations

import asyncio
import logging
import re
from datetime import datetime, timezone

from core import sheets
from core.config import Settings
from core.models import (
    STATUS_RANK,
    TERMINAL_STATUSES,
    Application,
    ApplicationStatus,
    Category,
    Email,
    ExtractedDetails,
    ResearchBrief,
)

logger = logging.getLogger(__name__)

# company | department | role | category | key_date | status | research_brief
#         | source | logged_at
TRACKER_COLUMNS = [
    "company",
    "department",
    "role",
    "category",
    "key_date",
    "status",
    "research_brief",
    "source",
    "logged_at",
]

COL = {name: index for index, name in enumerate(TRACKER_COLUMNS)}

# Legal and structural suffixes that differ between how a company signs its
# emails and how it names itself in a job posting. Stripped before comparing.
COMPANY_NOISE = {
    "pte", "ltd", "limited", "inc", "incorporated", "llc", "plc", "corp",
    "corporation", "company", "co", "holdings", "holding", "group", "technologies",
    "technology", "labs", "sg", "singapore", "asia", "global", "international",
}

# Words that appear in a department name without distinguishing one unit from
# another. "Team" and "Division" are pure scaffolding; the company's own name
# is dropped because units are usually written with it ("Shopee Mall").
DEPARTMENT_NOISE = {
    "team", "department", "dept", "division", "unit", "business", "group",
    "the", "of", "and", "at", "in", "by", "for",
}

# Words that appear in role titles without distinguishing one role from
# another, so they should not prop up a match on their own.
ROLE_NOISE = {
    "intern", "internship", "programme", "program", "summer", "winter", "spring",
    "fall", "autumn", "student", "undergraduate", "graduate", "the", "a", "an",
    "and", "of", "for", "at", "in",
    # The extractor is told to emit "Unknown" when an email states no title.
    # Left as a real word it looks like a distinguishing one, and every
    # title-less email would open a duplicate row for a company we already
    # track. Treating it as noise falls through to the company-only match.
    "unknown", "unspecified", "n", "a",
}

# Serialises read-modify-write. run_once extracts concurrently, and without
# this two emails for the same application would both read "no existing row"
# and both append one.
_write_lock = asyncio.Lock()


def _tokens(text: str, noise: set[str]) -> list[str]:
    words = re.split(r"[^a-z0-9]+", text.lower())
    # Drop bare years ("2027") along with the noise words: a role is not a
    # different role because the intake year changed.
    return [w for w in words if w and w not in noise and not w.isdigit()]


def company_key(company: str) -> frozenset[str]:
    """Identity of an employer, ignoring legal suffixes and word order.

    Shared by the tracker's matching and by run_once's research fan-out, so
    "Grab" and "Grab Holdings" are one company in both places. Two rows would
    otherwise each trigger their own research call for the same employer.
    """
    return frozenset(_tokens(company, COMPANY_NOISE))


def department_tokens(company: str, department: str | None) -> frozenset[str]:
    """The words that actually distinguish one business unit from another.

    The employer's own name is removed. Units are usually written with it —
    "Shopee Mall", "Fulfilled by Shopee" — and leaving it in makes every unit
    at a company overlap on that one token, which is exactly the false match
    this function exists to prevent.
    """
    return frozenset(_tokens(department or "", DEPARTMENT_NOISE)) - frozenset(
        _tokens(company, COMPANY_NOISE)
    )


def same_department(
    company_a: str, department_a: str | None,
    company_b: str, department_b: str | None,
) -> bool:
    """Whether two department strings name the same business unit.

    A named unit and an unnamed one are treated as the same application: the
    first email in a thread often omits the unit a later one states, and
    splitting one application in two is worse than merging those.

    Two *different* named units never match. Shopee's FBS and Shopee Mall hire
    and interview separately, so collapsing them would fuse unrelated
    processes into one row and give one of them the other's brief.
    """
    ta = department_tokens(company_a, department_a)
    tb = department_tokens(company_b, department_b)
    if not ta or not tb:
        return True
    # Overlap rather than equality, so "FBS" matches "Fulfilled by Shopee
    # (FBS)" — the extractor is asked to emit both forms for exactly this.
    return bool(ta & tb)


def department_key(company: str, department: str | None) -> tuple:
    """Research identity: an employer plus, if known, its business unit.

    Distinct from company_key because a brief on Shopee Mall is not a brief on
    Fulfilled by Shopee. Deduping research by company alone would give one of
    them the other's briefing.

    Exact-equality key, so it cannot recognise "FBS" and "Fulfilled by Shopee
    (FBS)" as one unit — same_department does that. Callers that need the
    looser notion must group with same_department rather than rely on this.
    """
    return (company_key(company), department_tokens(company, department))


def _same_application(
    company_a: str, role_a: str, company_b: str, role_b: str,
    department_a: str | None = None, department_b: str | None = None,
) -> bool:
    """Decide whether two applications are the same.

    Company must match on its distinguishing words — that is the hard
    constraint, since merging two employers is far worse than splitting one.
    Department must not contradict: two differently named units at one company
    are separate applications even when the role title is identical, because
    "Operations Intern" at Shopee Mall and at FBS are different jobs run by
    different teams. Role is compared loosely, because "Software Engineer
    Intern (Summer 2027)" and "Software Engineering Intern" are the same job.
    """
    ca, cb = _tokens(company_a, COMPANY_NOISE), _tokens(company_b, COMPANY_NOISE)
    if not ca or not cb or set(ca) != set(cb):
        return False

    if not same_department(company_a, department_a, company_b, department_b):
        return False

    ra, rb = set(_tokens(role_a, ROLE_NOISE)), set(_tokens(role_b, ROLE_NOISE))
    if not ra or not rb:
        # One side has no distinguishing role words left (e.g. "Intern", or the
        # extractor's "Unknown"). Company already matched, so treat as the same
        # application rather than opening a duplicate row.
        return True

    overlap = len(ra & rb) / min(len(ra), len(rb))
    return overlap >= 0.5


def _next_status(
    current_raw: str, incoming: ApplicationStatus
) -> ApplicationStatus:
    """Status only moves forward, and never leaves a terminal state."""
    try:
        current = ApplicationStatus(current_raw.strip().lower())
    except ValueError:
        return incoming

    if current in TERMINAL_STATUSES:
        return current
    return incoming if STATUS_RANK[incoming] > STATUS_RANK[current] else current


def _cell(row: list[str], name: str) -> str:
    """Sheets truncates trailing empty cells, so a short row is normal."""
    index = COL[name]
    return row[index].strip() if index < len(row) else ""


def _format_date(value: datetime | None) -> str:
    return value.isoformat() if value else ""


def _build_row(
    category: Category,
    extracted: ExtractedDetails,
    email: Email,
    existing: list[str] | None = None,
    research_brief: ResearchBrief | None = None,
) -> list[str]:
    prior_brief = _cell(existing, "research_brief") if existing else ""
    # A freshly researched brief supersedes an older one, but a run without
    # research must never blank out a brief already in the sheet.
    brief_text = research_brief.brief_text if research_brief else prior_brief
    prior_date = _cell(existing, "key_date") if existing else ""
    prior_status = _cell(existing, "status") if existing else ""

    status = (
        _next_status(prior_status, extracted.status) if existing else extracted.status
    )
    # Keep a date we already knew if this email does not carry one — a
    # follow-up asking for documents should not erase the interview time.
    key_date = _format_date(extracted.key_date) or prior_date

    row = [""] * len(TRACKER_COLUMNS)
    row[COL["company"]] = extracted.company
    # Keep a unit we already knew if a later email omits it.
    row[COL["department"]] = extracted.department or (
        _cell(existing, "department") if existing else ""
    )
    row[COL["role"]] = extracted.role
    row[COL["category"]] = category.value
    row[COL["key_date"]] = key_date
    row[COL["status"]] = status.value
    row[COL["research_brief"]] = brief_text
    row[COL["source"]] = email.message_id
    row[COL["logged_at"]] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    return row


async def _load_rows(settings: Settings) -> list[list[str]]:
    """Every data row, header excluded."""
    values = await sheets.get_values(
        settings, f"{sheets.APPLICATIONS_TAB}!A2:{chr(ord('A') + len(TRACKER_COLUMNS) - 1)}"
    )
    return values


async def ensure_ready(settings: Settings) -> None:
    """Create tabs and headers if this is a fresh spreadsheet."""
    await sheets.ensure_tabs(
        settings,
        {
            sheets.APPLICATIONS_TAB: TRACKER_COLUMNS,
            sheets.STATE_TAB: ["key", "value", "updated_at"],
        },
    )


async def upsert_row(
    settings: Settings,
    category: Category,
    extracted: ExtractedDetails,
    email: Email,
    research_brief: ResearchBrief | None = None,
) -> bool:
    """Create the application's row, or update it in place if it exists.

    Returns True when a new application was added, False when an existing one
    was updated — Stage 6 words the notification differently for each.
    """
    async with _write_lock:
        rows = await _load_rows(settings)

        for offset, row in enumerate(rows):
            if _same_application(
                _cell(row, "company"), _cell(row, "role"),
                extracted.company, extracted.role,
                _cell(row, "department"), extracted.department,
            ):
                sheet_row = offset + 2  # 1-based, and row 1 is the header
                updated = _build_row(
                    category, extracted, email, existing=row,
                    research_brief=research_brief,
                )
                last_col = chr(ord("A") + len(TRACKER_COLUMNS) - 1)
                await sheets.update_values(
                    settings,
                    f"{sheets.APPLICATIONS_TAB}!A{sheet_row}:{last_col}{sheet_row}",
                    [updated],
                )
                logger.info(
                    "Updated row %d: %s / %s -> %s",
                    sheet_row, extracted.company, extracted.role,
                    updated[COL["status"]],
                )
                return False

        await sheets.append_values(
            settings,
            sheets.APPLICATIONS_TAB,
            [_build_row(category, extracted, email, research_brief=research_brief)],
        )
        logger.info(
            "Added application: %s / %s (%s)",
            extracted.company, extracted.role, extracted.status.value,
        )
        return True


async def attach_research_brief(
    settings: Settings, company: str, brief: ResearchBrief
) -> None:
    """Write a prep brief onto every row for a company.

    Matching on company alone is intentional: the brief is about the employer,
    not the specific role, so it is useful on all of that company's rows.
    """
    async with _write_lock:
        rows = await _load_rows(settings)
        brief_col = chr(ord("A") + COL["research_brief"])
        target = set(_tokens(company, COMPANY_NOISE))

        written = 0
        for offset, row in enumerate(rows):
            if set(_tokens(_cell(row, "company"), COMPANY_NOISE)) != target:
                continue
            sheet_row = offset + 2
            await sheets.update_values(
                settings,
                f"{sheets.APPLICATIONS_TAB}!{brief_col}{sheet_row}",
                [[brief.brief_text]],
            )
            written += 1

        if written:
            logger.info("Attached research brief to %d row(s) for %s", written, company)
        else:
            logger.warning("No tracker row matched company %r for research brief", company)


def _parse_datetime(raw: str) -> datetime | None:
    """Sheets hands back whatever was written. Be forgiving: a row a human has
    edited by hand should not break the read path for every other row."""
    raw = raw.strip()
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw)
    except ValueError:
        logger.debug("Unparseable date in tracker: %r", raw)
        return None


async def load_applications(settings: Settings) -> list[Application]:
    """Every tracker row, parsed. The bot's read path."""
    rows = await _load_rows(settings)

    applications = []
    for row in rows:
        company = _cell(row, "company")
        if not company:
            # Blank separator rows are a normal thing to find in a spreadsheet
            # someone actually looks at.
            continue
        try:
            status = ApplicationStatus(_cell(row, "status").lower())
        except ValueError:
            status = ApplicationStatus.UNKNOWN
        applications.append(
            Application(
                company=company,
                department=_cell(row, "department") or None,
                role=_cell(row, "role"),
                category=_cell(row, "category"),
                key_date=_parse_datetime(_cell(row, "key_date")),
                status=status,
                research_brief=_cell(row, "research_brief"),
                source=_cell(row, "source"),
                logged_at=_parse_datetime(_cell(row, "logged_at")),
            )
        )
    return applications


async def companies_with_briefs(settings: Settings) -> list[tuple[str, str]]:
    """(company, department) pairs that already have a research brief.

    Used to skip re-researching a unit already briefed. Interview threads run
    to several emails — invitation, reschedule, confirmation — and each would
    otherwise spend three searches and a Gemini call rewriting a brief the
    reader already has.

    Returns the raw strings rather than keys so callers can compare with
    same_department. A sheet row saying "FBS" and a new email saying
    "Fulfilled by Shopee (FBS)" are one unit, and exact key equality would
    miss that and research it again.
    """
    rows = await _load_rows(settings)
    return [
        (_cell(row, "company"), _cell(row, "department"))
        for row in rows
        if _cell(row, "research_brief") and _cell(row, "company")
    ]
