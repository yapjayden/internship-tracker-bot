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
    role: str = Field(description="Role title as written, without the company name.")
    status: ApplicationStatus
    key_date: Optional[datetime] = Field(
        default=None,
        description="Interview time or assessment deadline, if the email states one.",
    )
    next_steps: Optional[str] = Field(
        default=None, description="One short sentence on what the student must do next."
    )


class ResearchBrief(BaseModel):
    company: str
    brief_text: str
    generated_at: datetime
