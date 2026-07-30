"""Shared data shapes used across watchers, agents, and the tracker.

The extraction schemas below are a DRAFT — confirm the final tracker schema
with the user at Stage 4/5 before treating these as settled.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel


class MailSource(str, Enum):
    GMAIL = "gmail"
    OUTLOOK = "outlook"


class Email(BaseModel):
    """Common shape both gmail_watcher and outlook_watcher must return, so
    everything downstream is source-agnostic."""

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


class ResultStatus(str, Enum):
    OFFER = "offer"
    REJECT = "reject"
    PENDING = "pending"


class ExtractedInterview(BaseModel):
    company: str
    role: str
    interview_datetime: Optional[datetime] = None
    next_steps: Optional[str] = None


class ExtractedOA(BaseModel):
    company: str
    role: str
    deadline: Optional[datetime] = None
    next_steps: Optional[str] = None


class ExtractedResult(BaseModel):
    company: str
    role: str
    result: ResultStatus
    next_steps: Optional[str] = None


class ResearchBrief(BaseModel):
    company: str
    brief_text: str
    generated_at: datetime
