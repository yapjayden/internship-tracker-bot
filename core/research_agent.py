"""Research agent: one independent instance per company, run concurrently.

Each instance must have no shared mutable state with any other instance
running alongside it — that's what makes asyncio.gather-ing many of them
across companies safe. A failure in one instance must not crash the others;
callers should wrap each task so a raised exception becomes a logged miss,
not a cancelled gather.

Two ways to get the facts, chosen by whether TAVILY_API_KEY is set:

- Tavily. Searches run first, and their extracts go into the prompt. Preferred,
  because Tavily's free pool is separate from Gemini's.
- Gemini's built-in Google Search grounding. One call, no extra key — but on
  the free tier it is billed against the same generate_content request quota
  the router and extractor spend, not the separate search-grounding quota. It
  therefore adds no search budget and runs out alongside classification. Kept
  as a fallback for anyone on a paid tier, where that attribution is correct.

Both paths end at the same ResearchBrief, so callers never learn which ran.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from core import gemini, search
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
- Today's date is given in the prompt. Use it. Search results are undated \
snapshots, so without checking you will describe a passed event as upcoming — \
"they are scheduled to report on 11 February" when February was months ago. \
Put past events in the past tense, and skip anything too old to be news.
- Prefer the last 12 months. An old funding round presented as news is worse \
than no news at all.
- Plain text bullets only. No markdown headers, bold, or tables — this is \
delivered in a Telegram message.\
"""


def _prompt(company: str, role: str, today: date | None = None) -> str:
    # Role is included because interview format differs sharply between
    # engineering, quant and analyst tracks at the same employer.
    #
    # The date is included because search extracts carry no reliable timestamp,
    # so the model has no way to tell a forthcoming event from one that has
    # already happened — it reported a February results announcement as
    # upcoming in August.
    today = today or date.today()
    return (
        f"Today's date: {today:%d %B %Y}\n"
        f"Company: {company}\n"
        f"Role the student is interviewing for: {role}\n\n"
        f"Research {company} and write the briefing."
    )


def search_queries(company: str, role: str, today: date | None = None) -> list[str]:
    """The three searches the brief is built from, one per section that needs
    external facts. Culture and interview format are separate queries because
    a single "tell me about X" search returns marketing copy for both."""
    today = today or date.today()
    return [
        # The year is computed, not written in. A hardcoded one silently
        # narrows to stale results as soon as the calendar moves on.
        f"{company} news announcement {today.year}",
        f"{company} {role} interview process rounds candidate experience",
        f"{company} company values culture careers",
    ]


def _render_results(results: list[search.SearchResult], budget: int = 6000) -> str:
    """Flatten search extracts into the prompt, newest-first, within a budget.

    Truncated per result rather than globally so a single long page cannot
    crowd out every other source.
    """
    blocks = []
    used = 0
    for index, result in enumerate(results, 1):
        content = result.content[:900]
        block = f"[{index}] {result.title}\n{result.url}\n{content}\n"
        if used + len(block) > budget:
            break
        blocks.append(block)
        used += len(block)
    return "\n".join(blocks)


async def _brief_via_tavily(
    settings: Settings, company: str, role: str
) -> tuple[str, list[str]]:
    results = await search.search_many(settings, search_queries(company, role))
    if not results:
        raise RuntimeError(
            f"No search results for {company}. Check TAVILY_API_KEY and its "
            "remaining monthly quota at https://app.tavily.com"
        )

    prompt = (
        f"{_prompt(company, role)}\n\n"
        "Use only the search results below. If they do not support a section, "
        "write \"not found\" for it.\n\n"
        "SEARCH RESULTS\n"
        f"{_render_results(results)}"
    )

    # use_search_tool=False: the facts are already in the prompt, and enabling
    # the tool here would spend the Gemini request quota this path exists to
    # avoid.
    generated = await gemini.generate_grounded_text(
        settings,
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=prompt,
        max_output_tokens=2048,
        use_search_tool=False,
    )
    return generated.text, [f"{r.title} — {r.url}" if r.title else r.url for r in results]


async def _brief_via_grounding(
    settings: Settings, company: str, role: str
) -> tuple[str, list[str]]:
    generated = await gemini.generate_grounded_text(
        settings,
        system_instruction=SYSTEM_INSTRUCTION,
        prompt=_prompt(company, role),
        max_output_tokens=2048,
    )
    return generated.text, generated.sources


async def research_company(
    settings: Settings, company: str, role: str
) -> ResearchBrief:
    """Produce one prep brief. Raises on failure; callers decide the policy."""
    if search.is_configured(settings):
        text, sources = await _brief_via_tavily(settings, company, role)
    else:
        logger.info(
            "TAVILY_API_KEY not set; using Gemini search grounding, which draws "
            "on the same request quota as routing and extraction."
        )
        text, sources = await _brief_via_grounding(settings, company, role)

    if not sources:
        # Worth flagging rather than hiding: with no sources the model answered
        # from training data, which for "recent developments" is exactly where
        # it is least reliable.
        logger.warning(
            "No sources for %s — brief is unverified model recall", company
        )

    logger.info(
        "Researched %s (%d chars, %d source(s))", company, len(text), len(sources)
    )

    return ResearchBrief(
        company=company,
        brief_text=text,
        generated_at=datetime.now(timezone.utc),
        sources=sources[:MAX_SOURCES],
    )
