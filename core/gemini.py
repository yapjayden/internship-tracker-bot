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

# Not every 429 is the same. A per-minute quota clears if you wait; a per-day
# one does not, and its RetryInfo still suggests a ~60s wait that accomplishes
# nothing except spending more of the allowance. Google distinguishes them only
# in the quotaId, e.g.
#   GenerateRequestsPerMinutePerProjectPerModel-FreeTier
#   GenerateRequestsPerDayPerProjectPerModel-FreeTier
DAILY_QUOTA_RE = re.compile(r"'quotaId':\s*'([^']*PerDay[^']*)'")


class DailyQuotaExceeded(RuntimeError):
    """The model's per-day free-tier allowance is gone. Retrying cannot help."""


# Latched for the life of the process. Ten concurrent agents all discover the
# daily wall within milliseconds of each other; without this each would burn
# MAX_ATTEMPTS more requests learning the same thing.
_daily_quota_hit: str | None = None

# How to ask for minimal reasoning, most preferred first.
#
# These narrow classification/extraction tasks gain nothing from reasoning
# tokens, and those tokens come out of max_output_tokens — left unchecked they
# eat the whole budget and the model returns empty text. But *how* you ask for
# less has changed across model generations: 2.5 took an integer
# `thinking_budget`, 3.x replaced it with a `thinking_level` enum and rejects
# the integer form outright with a bare 400 INVALID_ARGUMENT that names no
# field.
#
# Since DEFAULT_MODEL is a moving alias, the generation can change under us
# without a commit. So rather than hardcode one dialect, try them in order and
# remember the first that the endpoint accepts.
THINKING_VARIANTS: tuple[tuple[str, types.ThinkingConfig | None], ...] = (
    ("thinking_budget=0", types.ThinkingConfig(thinking_budget=0)),
    ("thinking_level=MINIMAL", types.ThinkingConfig(thinking_level=types.ThinkingLevel.MINIMAL)),
    ("model default", None),
)

# Once a variant leaves thinking enabled, reasoning tokens share the output
# budget, so a limit sized for bare JSON is no longer enough.
MIN_TOKENS_WHEN_THINKING = 2048

_thinking_variant = 0

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


def _raise_if_daily_quota_spent() -> None:
    if _daily_quota_hit is not None:
        raise DailyQuotaExceeded(
            f"Daily free-tier quota already exhausted this run for "
            f"{default_model()!r} ({_daily_quota_hit}). Skipping further calls; "
            "set GEMINI_MODEL in .env to a different model to keep going today."
        )


def _server_retry_delay(exc: errors.APIError) -> float | None:
    """Pull the server's requested retry delay out of a 429. Google returns a
    RetryInfo block saying exactly how long to wait; guessing a shorter
    exponential backoff just wastes another request against the quota."""
    match = re.search(r"'retryDelay':\s*'(\d+(?:\.\d+)?)s'", str(exc))
    return float(match.group(1)) if match else None


def _build_config(
    *,
    system_instruction: str,
    response_schema: type[T],
    max_output_tokens: int,
    variant_index: int,
) -> types.GenerateContentConfig:
    thinking = THINKING_VARIANTS[variant_index][1]
    # Only the budget=0 dialect actually suppresses reasoning. Under the other
    # variants the model still thinks, so give it room to finish and still emit
    # the JSON.
    tokens = max_output_tokens
    if thinking is None or thinking.thinking_budget != 0:
        tokens = max(tokens, MIN_TOKENS_WHEN_THINKING)

    return types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=tokens,
        response_mime_type="application/json",
        response_schema=response_schema,
        thinking_config=thinking,
    )


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
    failures; raises on non-retryable errors or after MAX_ATTEMPTS.

    A 400 caused by an unsupported thinking dialect is not treated as fatal:
    the next variant is tried and the working one is cached process-wide, so
    only the first call ever pays for the discovery.
    """
    global _thinking_variant, _daily_quota_hit

    _raise_if_daily_quota_spent()

    client = build_client(settings)
    limiter = get_limiter()

    used_variant = _thinking_variant
    config = _build_config(
        system_instruction=system_instruction,
        response_schema=response_schema,
        max_output_tokens=max_output_tokens,
        variant_index=used_variant,
    )

    last_error: Exception | None = None
    attempt = 0
    while attempt < MAX_ATTEMPTS:
        # Another coroutine may have hit the daily wall while this one queued
        # behind the rate limiter.
        _raise_if_daily_quota_spent()
        await limiter.acquire()
        _raise_if_daily_quota_spent()
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
            # Probing for the right thinking dialect is not a retry — it is a
            # different request — so it must not consume a retry attempt.
            if exc.code == 400 and used_variant + 1 < len(THINKING_VARIANTS):
                # Concurrent callers all fail on the same variant at once.
                # Advance the shared pointer only if nobody else already did,
                # so a burst of 400s steps forward one notch rather than
                # skipping over the variant that would have worked.
                if _thinking_variant == used_variant:
                    _thinking_variant = used_variant + 1
                    logger.warning(
                        "Model %s rejected %s (400); falling back to %s",
                        default_model(),
                        THINKING_VARIANTS[used_variant][0],
                        THINKING_VARIANTS[_thinking_variant][0],
                    )
                used_variant = _thinking_variant
                config = _build_config(
                    system_instruction=system_instruction,
                    response_schema=response_schema,
                    max_output_tokens=max_output_tokens,
                    variant_index=used_variant,
                )
                last_error = exc
                continue

            # A spent daily allowance is terminal. Waiting the ~60s the server
            # suggests just spends more of tomorrow's budget on the same wall.
            daily = DAILY_QUOTA_RE.search(str(exc))
            if daily:
                _daily_quota_hit = daily.group(1)
                raise DailyQuotaExceeded(
                    f"Daily free-tier quota exhausted for {default_model()!r} "
                    f"({_daily_quota_hit}). It resets at midnight US/Pacific. "
                    "The quota is per project *per model*, so setting GEMINI_MODEL "
                    "in .env to a different model gives you a fresh allowance now."
                ) from exc

            attempt += 1
            if exc.code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                raise
            last_error = exc
            # Jitter so concurrent agents that hit the ceiling together don't
            # retry in lockstep and re-collide.
            delay = _server_retry_delay(exc) or 2 ** (attempt - 1)
            delay = min(delay, MAX_RETRY_DELAY_S) + random.uniform(0, 1)
            logger.warning(
                "Gemini %s, retrying in %.1fs (attempt %d/%d)",
                exc.code, delay, attempt, MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)

    raise RuntimeError(f"Gemini failed after {MAX_ATTEMPTS} attempts") from last_error


class GroundedText(BaseModel):
    """Free-text answer plus the pages the model actually consulted."""

    text: str
    sources: list[str] = []


def _extract_sources(response) -> list[str]:
    """Pull deduplicated source URLs out of grounding metadata.

    Best-effort: grounding metadata is absent whenever the model chose not to
    search, and the shape has moved between SDK versions, so a missing field
    means "no citations", never an error.
    """
    seen: list[str] = []
    for candidate in getattr(response, "candidates", None) or []:
        metadata = getattr(candidate, "grounding_metadata", None)
        for chunk in getattr(metadata, "grounding_chunks", None) or []:
            web = getattr(chunk, "web", None)
            uri = getattr(web, "uri", None)
            if not uri:
                continue
            title = (getattr(web, "title", None) or getattr(web, "domain", None) or "").strip()
            entry = f"{title} — {uri}" if title else uri
            if entry not in seen:
                seen.append(entry)
    return seen


async def generate_grounded_text(
    settings: Settings,
    *,
    system_instruction: str,
    prompt: str,
    max_output_tokens: int = 2048,
    use_search_tool: bool = True,
) -> GroundedText:
    """Answer a question in prose, optionally letting the model search.

    Deliberately not generate_json. Grounding and structured output can be
    combined on current models, but the citations come back empty when they
    are, and this call's output is one block of prose anyway — a schema would
    buy nothing and cost the sources.

    Thinking is left at the model's default too: unlike routing and
    extraction, synthesising several search results is exactly the kind of
    work reasoning tokens help with, and the output is prose, so there is no
    empty-JSON failure mode to guard against.
    """
    _raise_if_daily_quota_spent()

    client = build_client(settings)
    limiter = get_limiter()

    config = types.GenerateContentConfig(
        system_instruction=system_instruction,
        max_output_tokens=max_output_tokens,
        tools=(
            [types.Tool(google_search=types.GoogleSearch())] if use_search_tool else None
        ),
    )

    last_error: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        _raise_if_daily_quota_spent()
        await limiter.acquire()
        try:
            response = await client.aio.models.generate_content(
                model=default_model(), contents=prompt, config=config
            )
        except errors.APIError as exc:
            daily = DAILY_QUOTA_RE.search(str(exc))
            if daily:
                globals()["_daily_quota_hit"] = daily.group(1)
                raise DailyQuotaExceeded(
                    f"Daily free-tier quota exhausted for {default_model()!r} "
                    f"({daily.group(1)}). Grounded search draws on the same "
                    "per-model allowance as routing and extraction. Either set "
                    "GEMINI_MODEL to a different model, or move research onto a "
                    "separate search provider."
                ) from exc

            if exc.code not in RETRYABLE_STATUS or attempt == MAX_ATTEMPTS:
                raise
            last_error = exc
            delay = _server_retry_delay(exc) or 2 ** (attempt - 1)
            delay = min(delay, MAX_RETRY_DELAY_S) + random.uniform(0, 1)
            logger.warning(
                "Gemini %s on grounded call, retrying in %.1fs (attempt %d/%d)",
                exc.code, delay, attempt, MAX_ATTEMPTS,
            )
            await asyncio.sleep(delay)
            continue

        text = (response.text or "").strip()
        if not text:
            finish = response.candidates[0].finish_reason if response.candidates else None
            raise RuntimeError(f"Gemini returned no text (finish_reason={finish})")
        return GroundedText(text=text, sources=_extract_sources(response))

    raise RuntimeError(f"Gemini failed after {MAX_ATTEMPTS} attempts") from last_error
