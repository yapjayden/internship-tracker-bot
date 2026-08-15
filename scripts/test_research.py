"""Stage 7 test: run the research agent against a real company.

    python -m scripts.test_research --offline        # show the prompt, no calls
    python -m scripts.test_research                  # research a default company
    python -m scripts.test_research "Grab" "Data Analyst Intern"

Each live run is one grounded Gemini request, drawn from the same per-model
daily allowance as routing and extraction — so keep an eye on it if you have
also been running the router over a large inbox.

There is no pass/fail here. Whether a brief is any good is a judgement call,
so this prints it for you to read. What it does check is the mechanical part:
that the model actually searched, that the length is sane, and that the brief
renders inside a Telegram message.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from core import notifier, research_agent
from core.config import load_settings
from core.models import (
    ApplicationStatus,
    Category,
    Email,
    ExtractedDetails,
    MailSource,
)

DEFAULT_COMPANY = "Grab"
DEFAULT_ROLE = "Software Engineer Intern"


def run_offline(company: str, role: str) -> int:
    print("\n" + "=" * 68)
    print("SYSTEM INSTRUCTION")
    print("=" * 68)
    print(research_agent.SYSTEM_INSTRUCTION)
    print("\n" + "=" * 68)
    print("PROMPT")
    print("=" * 68)
    print(research_agent._prompt(company, role))
    print("\nNo API calls made.\n")
    return 0


async def run_live(company: str, role: str) -> int:
    settings = load_settings()
    print(f"\nResearching {company} for a {role} interview...\n")

    brief = await research_agent.research_company(settings, company, role)

    print("=" * 68)
    print(brief.brief_text)
    print("=" * 68)

    words = len(brief.brief_text.split())
    print(f"\n  {words} words, {len(brief.sources)} source(s)")
    for source in brief.sources:
        print(f"    {source}")

    problems = 0
    if not brief.sources:
        # Not a hard failure — the model may decline to search — but the whole
        # reason for choosing grounding over a plain prompt was the citations.
        print("\n  WARNING: no sources. The model answered from memory, so the")
        print("  'recent developments' section is the least trustworthy part.")
        problems += 1
    if words > 350:
        print(f"\n  WARNING: {words} words is past the 300-word instruction.")
        problems += 1

    # The brief only matters if it survives into a notification, so render one.
    email = Email(
        source=MailSource.GMAIL, message_id="t", sender="recruiting@example.com",
        subject=f"Interview — {role}", received_at=datetime.now(timezone.utc),
        body_text="",
    )
    details = ExtractedDetails(
        company=company, role=role, status=ApplicationStatus.INTERVIEW,
        key_date=None, next_steps=None,
    )
    message = notifier.build_message(Category.INTERVIEW, details, email, brief, True)

    print(f"\n  Telegram message: {len(message)}/{notifier.MAX_MESSAGE_CHARS} chars")
    if len(message) > notifier.MAX_MESSAGE_CHARS:
        print("  FAIL: too long to send")
        problems += 1

    print("\n" + "-" * 68)
    print(message)
    print("-" * 68 + "\n")
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("company", nargs="?", default=DEFAULT_COMPANY)
    parser.add_argument("role", nargs="?", default=DEFAULT_ROLE)
    parser.add_argument(
        "--offline", action="store_true", help="print the prompt without calling"
    )
    args = parser.parse_args()
    raise SystemExit(
        run_offline(args.company, args.role)
        if args.offline
        else asyncio.run(run_live(args.company, args.role))
    )
