"""Shared data shapes used across watchers, agents, and the tracker.

Tracker schema is settled: one row per application, updated in place as the
application progresses, with the columns listed in core/tracker.py.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class MailSource(str, Enum):
    GMAIL = "gmail"


class Email(BaseModel):
    """Common shape any mail watcher returns, so everything downstream stays
    source-agnostic if another mailbox is added later."""

    source: MailSource
    message_id: str
    sender: str
    subject: str
    received_at: datetime
    body_text: str


class Category(str, Enum):
    INTERVIEW = "internship_interview"
    OA = "internship_oa"
    RESULT = "internship_result"
    OTHER = "internship_other"
    NOT_RELEVANT = "not_relevant"


class RouterResult(BaseModel):
    category: Category
    confidence: float


class ApplicationStatus(str, Enum):
    """Where an application currently stands.

    This is the tracker's `status` column. Because the sheet holds one row per
    application rather than one per email, status is a state that gets
    overwritten as things progress — not a log entry.
    """

    APPLIED = "applied"
    ASSESSMENT = "assessment"
    INTERVIEW = "interview"
    OFFER = "offer"
    REJECTED = "rejected"
    ACTION_NEEDED = "action_needed"
    UNKNOWN = "unknown"


# Later stages must not silently walk an application backwards: a stray
# acknowledgement arriving after an interview invite should not reset the row
# to "applied". Terminal states are final; otherwise higher rank wins.
STATUS_RANK: dict[ApplicationStatus, int] = {
    ApplicationStatus.UNKNOWN: 0,
    ApplicationStatus.APPLIED: 1,
    ApplicationStatus.ACTION_NEEDED: 2,
    ApplicationStatus.ASSESSMENT: 3,
    ApplicationStatus.INTERVIEW: 4,
    ApplicationStatus.REJECTED: 5,
    ApplicationStatus.OFFER: 6,
}

TERMINAL_STATUSES = frozenset({ApplicationStatus.OFFER, ApplicationStatus.REJECTED})


class ExtractedDetails(BaseModel):
    """Everything the extractor pulls from one email.

    A single shape rather than one per category, because all of them land in
    the same row of the same sheet. `key_date` is whichever date matters for
    this category — interview time, assessment deadline — so the tracker has
    one column to sort and remind on instead of several mostly-empty ones.
    """

    company: str = Field(
        description="Short trading name, e.g. 'Grab' not 'Grab Holdings Limited'."
    )
    department: Optional[str] = Field(
        default=None,
        description=(
            "Business unit or team within the company, if the email names one — "
            "e.g. 'Fulfilled by Shopee (FBS)'. Null when the email names none."
        ),
    )
    role: str = Field(description="Role title as written, without the company name.")
    status: ApplicationStatus
    key_date: Optional[datetime] = Field(
        default=None,
        description="Interview time or assessment deadline, if the email states one.",
    )
    next_steps: Optional[str] = Field(
        default=None, description="One short sentence on what the student must do next."
    )


class Application(BaseModel):
    """One tracker row, parsed back out of the sheet.

    The write path builds rows from ExtractedDetails; this is the read path,
    for the bot answering questions. Kept separate because a row carries
    things extraction never produced — the brief, when it was logged — and
    because everything in a spreadsheet arrives as a string.
    """

    company: str
    department: Optional[str] = None
    role: str = ""
    category: str = ""
    key_date: Optional[datetime] = None
    status: ApplicationStatus = ApplicationStatus.UNKNOWN
    research_brief: str = ""
    source: str = ""
    logged_at: Optional[datetime] = None

    @property
    def label(self) -> str:
        """Company, unit and role as one line, skipping what is absent."""
        parts = [self.company]
        if self.department:
            parts.append(self.department)
        if self.role and self.role.lower() != "unknown":
            parts.append(self.role)
        return " — ".join(parts)


class ResearchBrief(BaseModel):
    company: str
    # The business unit the brief is about, kept separate from `company` so
    # callers can match a brief to an application without parsing a display
    # string. None means the brief covers the employer as a whole.
    department: Optional[str] = None
    brief_text: str
    generated_at: datetime
    # Pages the model actually consulted, from Google Search grounding. A
    # brief you might repeat out loud in an interview needs to be checkable,
    # and an empty list is itself informative: it means the model answered
    # from memory rather than searching, so trust it less.
    sources: list[str] = Field(default_factory=list)
