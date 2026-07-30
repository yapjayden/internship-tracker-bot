"""Research agent: one independent instance per company, run concurrently.

Each instance must have no shared mutable state with any other instance
running alongside it — that's what makes asyncio.gather-ing many of them
across companies safe. A failure in one instance must not crash the others;
callers should wrap each task so a raised exception becomes a logged miss,
not a cancelled gather.
"""

from __future__ import annotations

from core.config import Settings
from core.models import ResearchBrief


async def research_company(settings: Settings, company: str, role: str) -> ResearchBrief:
    """Runs 2-3 searches (recent news, interview process, culture/values) and
    synthesizes a <300 word bullet-point brief for one company. Stage 7:
    single-company version. Stage 8: called many-at-once via asyncio.gather
    from pipeline/run_once.py, each call independent."""
    raise NotImplementedError("Stage 7: implement single-company research here")
