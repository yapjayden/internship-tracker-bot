"""Web search via Tavily, for the research agent.

Exists because Gemini's built-in Google Search grounding, which this project
tried first, is billed against the ordinary generate_content free-tier request
quota rather than the separate search-grounding quota. On the free tier that
means grounding buys no search budget at all — it spends the same small daily
allowance the router and extractor need, and runs out alongside them.

Tavily has its own free pool (~1,000 searches/month), so research no longer
competes with classification. It also returns cleaned extracts rather than raw
HTML, which is the only form worth putting in a prompt.

Plain httpx rather than the Tavily SDK: one POST, one JSON body, and the
project already depends on httpx for Telegram.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from core.config import Settings, require_setting

logger = logging.getLogger(__name__)

API_URL = "https://api.tavily.com/search"
TIMEOUT_S = 30.0
MAX_ATTEMPTS = 3

# Per query. Enough for the model to cross-check a claim, few enough that
# three queries still fit comfortably in a prompt.
RESULTS_PER_QUERY = 4


class SearchResult:
    __slots__ = ("title", "url", "content")

    def __init__(self, title: str, url: str, content: str):
        self.title = title
        self.url = url
        self.content = content

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"SearchResult({self.title!r}, {self.url!r})"


def is_configured(settings: Settings) -> bool:
    return bool(settings.tavily_api_key)


async def search(settings: Settings, query: str) -> list[SearchResult]:
    """Run one search. Returns [] on failure rather than raising.

    A brief missing one of its three searches is still worth sending; a brief
    that fails entirely because one query timed out is not.
    """
    api_key = require_setting(settings.tavily_api_key, "TAVILY_API_KEY")

    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": RESULTS_PER_QUERY,
        # "basic" is one hop and much faster. The extra depth mostly helps for
        # obscure queries; company news is well covered.
        "search_depth": "basic",
        "include_answer": False,
    }

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await client.post(API_URL, json=payload)
            except httpx.RequestError as exc:
                logger.warning("Tavily request failed (%d/%d): %s", attempt, MAX_ATTEMPTS, exc)
                if attempt == MAX_ATTEMPTS:
                    return []
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code == 200:
                results = response.json().get("results", []) or []
                return [
                    SearchResult(
                        title=(item.get("title") or "").strip(),
                        url=(item.get("url") or "").strip(),
                        content=(item.get("content") or "").strip(),
                    )
                    for item in results
                    if item.get("url")
                ]

            if response.status_code in {401, 403}:
                logger.error(
                    "Tavily rejected the key (%d). Check TAVILY_API_KEY at "
                    "https://app.tavily.com", response.status_code
                )
                return []

            if response.status_code == 432 or response.status_code == 429:
                # Tavily uses 432 for a spent plan. Neither is worth retrying
                # inside a single run.
                logger.error(
                    "Tavily quota exhausted (%d). The free pool resets monthly.",
                    response.status_code,
                )
                return []

            logger.warning(
                "Tavily %d (%d/%d): %s",
                response.status_code, attempt, MAX_ATTEMPTS, response.text[:200],
            )
            if attempt == MAX_ATTEMPTS:
                return []
            await asyncio.sleep(2**attempt)

    return []


async def search_many(settings: Settings, queries: list[str]) -> list[SearchResult]:
    """Run several searches concurrently and merge them round-robin.

    Round-robin rather than one query's results after another's: downstream
    consumers truncate, and concatenating means the first query fills every
    slot. A brief showed four news links and dropped both interview write-ups
    purely because the news query happened to run first.

    Deduplicated by URL, since the queries overlap by design — a company's
    newsroom answers both "recent news" and "what they value" — and the same
    page twice in a prompt only spends tokens.
    """
    batches = await asyncio.gather(
        *(search(settings, query) for query in queries), return_exceptions=True
    )

    usable: list[list[SearchResult]] = []
    for query, batch in zip(queries, batches):
        if isinstance(batch, BaseException):
            logger.warning("Search failed for %r: %s", query, batch)
            continue
        usable.append(batch)

    merged: list[SearchResult] = []
    seen: set[str] = set()
    for rank in range(max((len(b) for b in usable), default=0)):
        for batch in usable:
            if rank >= len(batch):
                continue
            result = batch[rank]
            if result.url in seen:
                continue
            seen.add(result.url)
            merged.append(result)

    logger.info(
        "Tavily returned %d unique result(s) across %d queries", len(merged), len(queries)
    )
    return merged
