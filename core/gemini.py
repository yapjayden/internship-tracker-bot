"""Shared Gemini access: one client, one retry policy, one way to get
strict JSON back.

Every agent goes through here so rate-limit handling lives in exactly one
place. That matters most at Stage 8, when research agents fan out
concurrently and a burst of 429s is the expected failure mode rather than a
surprise.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from core.config import Settings, require_setting

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"

# Transient on the free tier: 429 is the quota ceiling, 5xx is Google's side.
RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

T = TypeVar("T", bound=BaseModel)


def build_client(settings: Settings) -> genai.Client:
    api_key = require_setting(settings.gemini_api_key, "GEMINI_API_KEY")
    return genai.Client(api_key=api_key)


async def generate_json(
    settings: Settings,
    *,
    system_instruction: str,
    prompt: str,
    response_schema: type[T],
    max_output_tokens: int = 1024,
) -> T:
    """Run one Gemini call constrained to `response_schema` and return the
    parsed model. Retries transient failures with exponential backoff and
    jitter; raises on non-retryable errors or after MAX_ATTEMPTS."""
    client = build_client(settings)

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        response_schema=response_schema,
        # 2.5 models think by default and those tokens come out of
        # max_output_tokens, which can consume the whole budget and return
        # empty text. These are narrow extraction tasks that don't need it.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        try:
            response = await client.aio.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=config,
            )
            parsed = response.parsed
            if parsed is None:
                finish = response.candidates[0].finish_reason if response.candidates else None
                raise RuntimeError(f"Gemini returned no parseable JSON (finish_reason={finish})")
            return parsed

        except errors.APIError as exc:
            if exc.code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS - 1:
                raise
            last_error = exc
            # Jitter so concurrent research agents that all hit the quota
            # ceiling together don't retry in lockstep and re-collide.
            delay = 2**attempt + random.uniform(0, 1)
            logger.warning(
                "Gemini %s, retrying in %.1fs (attempt %d/%d)",
                exc.code, delay, attempt + 1, MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(f"Gemini failed after {MAX_ATTEMPTS} attempts") from last_error
