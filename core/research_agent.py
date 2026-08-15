"""Research agent: one independent instance per company, run concurrently.

Each instance must have no shared mutable state with any other instance
running alongside it — that's what makes asyncio.gather-ing many of them
across companies safe. A failure in one instance must not crash the others;
callers should wrap each task so a raised exception becomes a logged miss,
not a cancelled gather.

Search happens inside the model via Google Search grounding rather than
through a separate search API. That keeps this to one call and one
credential, and returns source URLs alongside the prose. The trade is that it
spends the same per-model daily Gemini allowance as routing and extraction;
if that becomes the binding constraint, moving to a dedicated search provider
is contained, since only the gemini call below would change.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

from core import gemini
from core.config import Settings
from core.models import ResearchBrief

logger = logging.getLogger(__name__)

MAX_SOURCES = 6

SYSTEM_INSTRUCTION = """\
You prepare a student for an internship interview at a specific company. \
Search the web, then write a briefing they can read in two minutes on the way \
to the interview.

Cover, in this order, as bullet points:

- What the company does, in one line. Assume the reader knows the name and \
nothing else about it.
- Two or three genuinely recent developments: a funding round, a launch, an \
acquisition, a regulatory change, a market entry. Give the approximate date. \
Skip anything you cannot date.
- What their interview process for this kind of role actually looks like, if \
candidates have reported it: number of rounds, format, what is assessed.
- One or two things the company says it values, in its own words where \
possible.
- Two questions the candidate could ask that show they have paid attention. \
These must follow from the specifics above, not be generic.

Rules:
- Under 300 words. A brief that needs scrolling will not get read.
- Write "not found" for any section the search did not support. A gap is \
useful; a confident guess is dangerous, because the reader may repeat it to \
an interviewer.
- No preamble, no sign-off, no restating the request. Start with the bullets.
- Prefer the last 12 months. An old funding round presented as news is worse \
than no news at all.
- Plain text bullets only. No markdown headers, bold, or tables — this is \
delivered in a Telegram message.\
"""


def _prompt(company: str, role: str) -> str:
    # Role is included because interview format differs sharply between
    # engineering, quant and analyst tracks at the same employer.
    return (
        f"Company: {company}\n"
        f"Role the student is interviewing for: {role}\n\n"
        f"Research {company} and write the briefing."
    )


async def research_company(
    settings: Settings, company: str, role: str
) -> ResearchBrief:
    """Produce one prep brief. Raises on failure; callers decide the policy."""
    result = await gemini.generate_grounded_text(
        settings,
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=_prompt(company, role),
        max_output_tokens=2048,
    )

    if not result.sources:
        # Worth flagging rather than hiding: with no grounding hits the model
        # answered from training data, which for "recent developments" is
        # exactly where it is least reliable.
        logger.warning(
            "No grounding sources for %s — brief is unverified model recall", company
        )

    logger.info(
        "Researched %s (%d chars, %d source(s))",
        company, len(result.text), len(result.sources),
    )

    return ResearchBrief(
        company=company,
        brief_text=result.text,
        generated_at=datetime.now(timezone.utc),
        sources=result.sources[:MAX_SOURCES],
    )
