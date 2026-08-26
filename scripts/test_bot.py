"""Stage 9 test: render every bot command.

    python -m scripts.test_bot --offline   # fabricated applications, no keys
    python -m scripts.test_bot             # your real tracker, read-only
    python -m scripts.test_bot --send      # ...and deliver to Telegram

--offline needs nothing configured and exercises the cases a young tracker
will not have yet: an offer, a rejection, a stale application, a brief.

Neither mode writes anything. The live mode reads the sheet; --send also
pushes the rendered replies to your chat so you can see them as they will
actually arrive.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from bot import commands
from core import notifier, tracker
from core.config import load_settings
from core.models import Application, ApplicationStatus

COMMANDS = [
    "/all", "/next", "/brief", "/brief shopee", "/find grab",
    "/stats", "/stale", "/help",
]

# /research does real work, so it is exercised separately against a stub
# rather than run for every mode.
RESEARCH_CASES = ["/research", "/research grab", "/research nowhere", "/research shopee"]

BRIEF = """\
WHAT THEY DO
- A Southeast Asian superapp: delivery, mobility, digital financial services.

RECENT NEWS
- Invested in Momenta for autonomous vehicles, December 2025.

INTERVIEW PROCESS
- Five rounds: online assessment, HR screen, two technical, hiring manager.

WHAT THEY VALUE
- The "4Hs": Heart, Hunger, Honour, Humility.

QUESTIONS TO ASK
- How is the AV work changing the engineering roadmap?"""


def _fixtures() -> list[Application]:
    now = datetime.now(timezone.utc)

    def app(company, role, status, **kw):
        return Application(company=company, role=role, status=status, **kw)

    return [
        app("Shopee", "Operations Intern", ApplicationStatus.INTERVIEW,
            department="Fulfilled by Shopee (FBS)", key_date=now + timedelta(days=3),
            research_brief=BRIEF, logged_at=now - timedelta(days=1)),
        # Same employer, different unit — must list separately.
        app("Shopee", "Data Analyst Intern", ApplicationStatus.APPLIED,
            department="Shopee Mall", logged_at=now - timedelta(days=2)),
        app("Grab", "Software Engineer Intern", ApplicationStatus.ASSESSMENT,
            key_date=now + timedelta(days=2), logged_at=now - timedelta(days=1)),
        # Interview already sat: same status as Shopee's, different group.
        app("Jane Street", "Quantitative Trading Intern", ApplicationStatus.INTERVIEW,
            key_date=now - timedelta(days=4), logged_at=now - timedelta(days=4)),
        app("GovTech", "Data Engineering Intern", ApplicationStatus.ACTION_NEEDED,
            key_date=now + timedelta(days=1), logged_at=now - timedelta(hours=6)),
        app("Stripe", "Software Engineering Intern", ApplicationStatus.OFFER,
            key_date=now + timedelta(days=10), logged_at=now - timedelta(days=3)),
        app("Sea Group", "Product Analyst Intern", ApplicationStatus.REJECTED,
            logged_at=now - timedelta(days=8)),
        # Acknowledged then silent: the /stale case.
        app("Airwallex", "Software Engineer - Intern 2027", ApplicationStatus.APPLIED,
            logged_at=now - timedelta(days=40)),
        app("GIC", "Investment Analyst Intern", ApplicationStatus.APPLIED,
            logged_at=now - timedelta(days=3)),
    ]


def _render(apps: list[Application]) -> int:
    problems = 0
    for command in COMMANDS:
        replies = commands.dispatch(apps, command)
        print("=" * 68)
        print(command)
        print("=" * 68)
        for reply in replies:
            print(reply)
            if len(reply) > notifier.MAX_MESSAGE_CHARS:
                print(f"  FAIL: {len(reply)} chars exceeds Telegram's limit")
                problems += 1
        print()

    # An empty tracker is what a new user sees first, so it must read as an
    # explanation rather than an error.
    print("=" * 68)
    print("/all with an empty tracker")
    print("=" * 68)
    print(commands.dispatch([], "/all")[0])
    print()
    return problems


def _render_research(apps: list[Application]) -> int:
    """Exercise /research with the agent and the sheet stubbed out."""
    import asyncio
    from unittest import mock

    from core import research_agent, tracker
    from core.models import ResearchBrief

    async def fake_research(settings, company, role, department=None):
        return ResearchBrief(
            company=company, department=department,
            brief_text=BRIEF, generated_at=datetime.now(timezone.utc),
            sources=["Example — https://example.com"],
        )

    saved: list[tuple] = []

    async def fake_attach(settings, company, brief, department=None):
        saved.append((company, department))

    problems = 0
    with mock.patch.object(research_agent, "research_company", fake_research), \
         mock.patch.object(tracker, "attach_research_brief", fake_attach):
        for command in RESEARCH_CASES:
            replies = asyncio.run(commands.dispatch_async(None, apps, command))
            print("=" * 68)
            print(command)
            print("=" * 68)
            for reply in replies:
                print(reply)
            print()

    # "shopee" matches two units, so it must ask rather than pick one and
    # silently write the wrong team's brief.
    ambiguous = asyncio.run(commands.dispatch_async(None, apps, "/research shopee"))
    if "more than one" not in ambiguous[0]:
        print("  FAIL: ambiguous company should have asked which unit")
        problems += 1
    # A company with no unit saving None is correct. What must not happen is a
    # unit-bearing application losing its unit on the way to the sheet, which
    # would write FBS's brief onto every Shopee row.
    saved.clear()
    with mock.patch.object(research_agent, "research_company", fake_research), \
         mock.patch.object(tracker, "attach_research_brief", fake_attach):
        asyncio.run(commands.dispatch_async(None, apps, "/research fulfilled"))
    if not saved:
        print("  FAIL: /research fulfilled matched nothing")
        problems += 1
    elif saved[0][1] is None:
        print("  FAIL: the business unit was dropped before saving")
        problems += 1
    else:
        print(f"  unit carried through to the sheet: {saved[0][1]!r}")
    return problems


def run_offline() -> int:
    apps = _fixtures()
    print(f"\n{len(apps)} fabricated application(s).\n")
    problems = _render(apps)
    problems += _render_research(apps)

    groups = {commands.group_of(a) for a in apps}
    print(f"  groups exercised: {len(groups)}/{len(commands.GROUP_ORDER)}")
    missing = [g for g in commands.GROUP_ORDER if g not in groups]
    if missing:
        print(f"  not covered by fixtures: {missing}")
    print(f"\n  {problems} failure(s)\n")
    return 1 if problems else 0


async def run_live(send: bool) -> int:
    settings = load_settings(require_gmail=False)
    apps = await tracker.load_applications(settings)
    print(f"\n{len(apps)} application(s) in the tracker.\n")
    problems = _render(apps)

    if send:
        print("Sending to Telegram...")
        for command in COMMANDS:
            for reply in commands.dispatch(apps, command):
                ok = await notifier.send_message(settings, reply)
                if not ok:
                    problems += 1
                    print(f"  FAILED to send reply to {command}")
        print("  sent.\n")

    print(f"  {problems} failure(s)\n")
    return 1 if problems else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--offline", action="store_true", help="use fabricated data")
    parser.add_argument("--send", action="store_true", help="also deliver to Telegram")
    args = parser.parse_args()
    raise SystemExit(
        run_offline() if args.offline else asyncio.run(run_live(args.send))
    )
