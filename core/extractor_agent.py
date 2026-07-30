"""Extractor agent: one narrow Gemini call per relevant email, extracting
structured fields into the Pydantic model matching its category. Only runs
on emails the router has already classified as relevant.
"""

from __future__ import annotations

from core.config import Settings
from core.models import Category, Email, ExtractedInterview, ExtractedOA, ExtractedResult

Extracted = ExtractedInterview | ExtractedOA | ExtractedResult


async def extract(settings: Settings, email: Email, category: Category) -> Extracted:
    raise NotImplementedError("Stage 4: implement Gemini extraction call here")
