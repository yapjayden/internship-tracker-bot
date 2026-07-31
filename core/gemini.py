"""Shared Gemini access: one client, one rate limiter, one retry policy, one
way to get strict JSON back.

Every agent goes through here so quota handling lives in exactly one place.
The free tier's ceiling is low enough (single-digit requests per minute) that
firing a handful of concurrent calls exhausts it instantly, so requests are
paced rather than merely retried — retrying into a quota wall just burns the
same quota again.
"""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from collections import deque
from typing import TypeVar

from google import genai
from google.genai import errors, types
from pydantic import BaseModel

from core.config import Settings, get_env, require_setting

logger = logging.getLogger(__name__)

# Model IDs get retired for new API keys while existing keys keep working,
# so this is overridable without a code change. Run scripts/list_models.py
# to see what your key can actually reach.
DEFAULT_MODEL = "gemini-flash-latest"

# Free-tier requests/minute. Deliberately conservative: exceeding it costs a
# 30s+ forced wait, which is far more expensive than pacing slightly slower.
DEFAULT_RPM = 5

RETRYABLE_STATUS = {429, 500, 502, 503, 504}
MAX_ATTEMPTS = 4

# A 429 can ask for a wait longer than any sane backoff. Honour the server's
# number, but refuse to hang a cron job for minutes on end.
MAX_RETRY_DELAY_S = 90.0

T = TypeVar("T", bound=BaseModel)


def default_model() -> str:
    return get_env("GEMINI_MODEL") or DEFAULT_MODEL


def _rpm() -> int:
    raw = get_env("GEMINI_RPM")
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_RPM


class RateLimiter:
    """Sliding-window limiter shared by every agent in the process.

    This is what makes concurrent research agents safe on the free tier: they
    still run concurrently, but their API calls queue through here instead of
    arriving as a burst that trips the quota.
    """

    def __init__(self, rpm: int):
        self.rpm = rpm
        self._recent: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                while self._recent and now - self._recent[0] >= 60.0:
                    self._recent.popleft()
                if len(self._recent) < self.rpm:
                    self._recent.append(now)
                    return
                wait = 60.0 - (now - self._recent[0])
            logger.debug("Rate limit reached, waiting %.1fs", wait)
            await asyncio.sleep(wait)


_limiter: RateLimiter | None = None
_client: genai.Client | None = None


def get_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(_rpm())
    return _limiter


def build_client(settings: Settings) -> genai.Client:
    global _client
    if _client is None:
        api_key = require_setting(settings.gemini_api_key, "GEMINI_API_KEY")
        _client = genai.Client(api_key=api_key)
    return _client


def _server_retry_delay(exc: errors.APIError) -> float | None:
    """Pull the server's requested retry delay out of a 429. Google returns a
    RetryInfo block saying exactly how long to wait; guessing a shorter
    exponential backoff just wastes another request against the quota."""
    match = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", str(exc))
    return float(match.group(1)) if match else None


async def generate_json(
    settings: Settings,
    *,
    system_instruction: str,
    prompt: str,
    response_schema: type[T],
    max_output_tokens: int = 1024,
) -> T:
    """Run one Gemini call constrained to `response_schema` and return the
    parsed model. Paced by the shared rate limiter, retrying transient
    failures; raises on non-retryable errors or after MAX_ATTEMPTS."""
    client = build_client(settings)
    limiter = get_limiter()

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=max_output_tokens,
        response_mime_type="application/json",
        response_schema=response_schema,
        # 2.5+ models think by default and those tokens come out of
        # max_output_tokens, which can consume the whole budget and return
        # empty text. These are narrow extraction tasks that don't need it.
        thinking_config=types.ThinkingConfig(thinking_budget=0),
    )

    last_error: Exception | None = None
    for attempt in range(MAX_ATTEMPTS):
        await limiter.acquire()
        try:
            response = await client.aio.models.generate_content(
                model=default_model(),
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
            # Jitter so concurrent agents that hit the ceiling together don't
            # retry in lockstep and re-collide.
            delay = _server_retry_delay(exc) or 2**attempt
            delay = min(delay, MAX_RETRY_DELAY_S) + random.uniform(0, 1)
            logger.warning(
                "Gemini %s, retrying in %.1fs (attempt %d/%d)",
                exc.code, delay, attempt + 1, MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(f"Gemini failed after {MAX_ATTEMPTS} attempts") from last_error
