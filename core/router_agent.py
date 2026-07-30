"""Router agent: one narrow Gemini call per email, classifying it into a
Category. Nothing else — no extraction, no research. Keep this prompt small
and single-purpose.
"""

from __future__ import annotations

from core.config import Settings
from core.models import Email, RouterResult


async def classify(settings: Settings, email: Email) -> RouterResult:
    raise NotImplementedError("Stage 3: implement Gemini classification call here")
