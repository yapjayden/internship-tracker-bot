"""The shared tracker: one row per application, stored in Google Sheets.
All agents write here — nothing agent-to-agent.

Schema is CONFIRMED: one row per application, updated in place as things
progress, rather than one row per email. That keeps the sheet readable as a
dashboard, at the cost of having to decide whether an incoming email belongs
to a row that already exists.
"""

from __future__ import annotations

from core.config import Settings
from core.models import Category, Email, ExtractedDetails, ResearchBrief

# company | role | category | key_date | status | research_brief | source | logged_at
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


async def upsert_row(
    settings: Settings, category: Category, extracted: ExtractedDetails, email: Email
) -> None:
    """Create the application's row, or update it in place if it already exists.

    One row per application means Stage 5 has two problems an append-only log
    would not:

    1. Identity. The same application arrives as "Grab", "Grab Holdings" and
       "GrabTaxi Pte Ltd" across three emails. The extractor is prompted for a
       short trading name, which gets most of the way; matching should still
       normalise case and punctuation and compare role loosely before deciding
       two emails belong together.
    2. Ordering. Mail does not arrive in the order events happened. Use
       models.STATUS_RANK so a late acknowledgement cannot walk an application
       back from `interview` to `applied`, and treat TERMINAL_STATUSES as final.
    """
    raise NotImplementedError("Stage 5: implement Google Sheets upsert here")


async def attach_research_brief(settings: Settings, company: str, brief: ResearchBrief) -> None:
    raise NotImplementedError("Stage 5/9: implement Sheets update-by-company here")


async def query(settings: Settings, natural_language_question: str) -> str:
    raise NotImplementedError("Stage 10: read tracker rows and answer NL queries")
