"""The shared tracker: one row per internship item, stored in Google Sheets.
All agents write here — nothing agent-to-agent. Schema below is a DRAFT,
confirm with the user before Stage 5 is built for real.
"""

from __future__ import annotations

from core.config import Settings
from core.models import Category, Email, ResearchBrief

# DRAFT schema — one row per internship item:
#   company | role | category | key_date | status | research_brief | source | logged_at
TRACKER_COLUMNS = [
    "company",
    "role",
    "category",
    "key_date",
    "status",
    "research_brief",
    "source",
    "logged_at",
]


async def append_row(settings: Settings, category: Category, extracted, email: Email) -> None:
    raise NotImplementedError("Stage 5: implement Google Sheets append here")


async def attach_research_brief(settings: Settings, company: str, brief: ResearchBrief) -> None:
    raise NotImplementedError("Stage 5/9: implement Sheets update-by-company here")


async def query(settings: Settings, natural_language_question: str) -> str:
    raise NotImplementedError("Stage 10: read tracker rows and answer NL queries")
