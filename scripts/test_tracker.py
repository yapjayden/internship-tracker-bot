"""Stage 5 test: exercise the Google Sheets tracker.

    python -m scripts.test_tracker --offline   # matching logic, no credentials
    python -m scripts.test_tracker             # writes to your real spreadsheet

--offline checks the parts that decide whether two emails are the same
application, which is where this stage actually goes wrong. No network, no
credentials, instant.

The live run writes a handful of synthetic applications to the configured
spreadsheet, then walks one of them through applied -> interview -> rejected
to prove updates land in place rather than piling up new rows. It uses an
obvious TEST- prefix on company names so you can spot and delete them.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone

from core import sheets, tracker
from core.config import load_settings
from core.models import (
    ApplicationStatus,
    Category,
    Email,
    ExtractedDetails,
    MailSource,
)

TEST_PREFIX = "TEST-"


def _email(message_id: str) -> Email:
    return Email(
        source=MailSource.GMAIL,
        message_id=message_id,
        sender="recruiting@example.com",
        subject="synthetic tracker test",
        received_at=datetime(2026, 8, 1, 9, 0, tzinfo=timezone.utc),
        body_text="synthetic",
    )


def _details(company: str, role: str, status: ApplicationStatus, key_date=None):
    return ExtractedDetails(
        company=company, role=role, status=status, key_date=key_date, next_steps=None
    )


# (a_company, a_role, b_company, b_role, should_match, why)
MATCH_CASES = [
    ("Grab", "Software Engineer Intern", "Grab Holdings Limited",
     "Software Engineering Intern", True, "legal suffix and noun form differ"),
    ("Shopee", "Software Engineer Intern (Summer 2027)", "Shopee",
     "Software Engineer Intern", True, "intake year is not a different role"),
    ("Sea", "Product Analyst Intern", "Sea Group", "Product Analyst Intern",
     True, "'group' is a company-noise word"),
    ("GovTech", "Data Engineering Intern", "GovTech", "Investment Analyst Intern",
     False, "same employer, genuinely different roles"),
    ("Grab", "Software Engineer Intern", "Gojek", "Software Engineer Intern",
     False, "different employers must never merge"),
    ("Jane Street", "Quantitative Trading Intern", "Jane Street", "Intern",
     True, "role has no distinguishing words left, company already matched"),
    ("Stripe", "Software Engineering Intern", "Stripe", "Unknown",
     True, "extractor could not read a title"),
]

# (existing_status, incoming_status, expected, why)
STATUS_CASES = [
    ("applied", ApplicationStatus.INTERVIEW, ApplicationStatus.INTERVIEW, "forward"),
    ("interview", ApplicationStatus.APPLIED, ApplicationStatus.INTERVIEW,
     "late acknowledgement must not walk it back"),
    ("rejected", ApplicationStatus.INTERVIEW, ApplicationStatus.REJECTED,
     "terminal states are final"),
    ("offer", ApplicationStatus.REJECTED, ApplicationStatus.OFFER,
     "already terminal, first one wins"),
    ("", ApplicationStatus.ASSESSMENT, ApplicationStatus.ASSESSMENT, "empty cell"),
    ("nonsense", ApplicationStatus.APPLIED, ApplicationStatus.APPLIED,
     "unparseable cell falls back to incoming"),
]


def run_offline() -> int:
    failures = 0

    print("\nApplication identity:\n")
    for ca, ra, cb, rb, expected, why in MATCH_CASES:
        got = tracker._same_application(ca, ra, cb, rb)
        ok = got == expected
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {ca!r}/{ra!r} vs {cb!r}/{rb!r}")
        print(f"        {'match' if got else 'separate'} — {why}")

    print("\nStatus progression:\n")
    for current, incoming, expected, why in STATUS_CASES:
        got = tracker._next_status(current, incoming)
        ok = got == expected
        failures += not ok
        print(f"  {'ok  ' if ok else 'FAIL'}  {current or '(empty)'} + {incoming.value} "
              f"-> {got.value}  ({why})")

    print(f"\n  {failures} failure(s)\n")
    return 1 if failures else 0


async def run_live() -> int:
    settings = load_settings()
    print(f"\nSpreadsheet: {settings.tracker_spreadsheet_id}\n")

    await tracker.ensure_ready(settings)
    print("  tabs and headers ready")

    # Two distinct applications at the same employer, to confirm they do not
    # collapse into one row.
    added_a = await tracker.upsert_row(
        settings, Category.OTHER,
        _details(f"{TEST_PREFIX}Acme", "Software Engineer Intern", ApplicationStatus.APPLIED),
        _email("t1"),
    )
    added_b = await tracker.upsert_row(
        settings, Category.OTHER,
        _details(f"{TEST_PREFIX}Acme", "Data Analyst Intern", ApplicationStatus.APPLIED),
        _email("t2"),
    )
    print(f"  two roles at one employer added as separate rows: {added_a and added_b}")

    # Same application arriving under a longer legal name, now at interview.
    key_date = datetime(2026, 9, 1, 10, 0, tzinfo=timezone.utc)
    added_c = await tracker.upsert_row(
        settings, Category.INTERVIEW,
        _details(f"{TEST_PREFIX}Acme Pte Ltd", "Software Engineering Intern",
                 ApplicationStatus.INTERVIEW, key_date),
        _email("t3"),
    )
    print(f"  legal-name variant updated in place (not added): {not added_c}")

    # A late acknowledgement, which must not undo the interview status, and
    # carries no date, which must not erase the one we have.
    await tracker.upsert_row(
        settings, Category.OTHER,
        _details(f"{TEST_PREFIX}Acme", "Software Engineer Intern", ApplicationStatus.APPLIED),
        _email("t4"),
    )

    rows = await sheets.get_values(settings, f"{sheets.APPLICATIONS_TAB}!A2:H")
    ours = [r for r in rows if r and r[0].startswith(TEST_PREFIX)]

    print(f"\n  {len(ours)} test row(s) in the sheet (expected 2):\n")
    for row in ours:
        padded = row + [""] * (len(tracker.TRACKER_COLUMNS) - len(row))
        print(f"    {padded[0]:<18} {padded[1]:<32} {padded[4]:<12} {padded[3]}")

    failures = 0
    if len(ours) != 2:
        print("\n  FAIL: expected exactly 2 rows")
        failures += 1

    swe = next((r for r in ours if "Software" in (r[1] if len(r) > 1 else "")), None)
    if swe is None:
        print("  FAIL: could not find the software engineering row")
        failures += 1
    else:
        status = swe[tracker.COL["status"]] if len(swe) > 4 else ""
        if status != ApplicationStatus.INTERVIEW.value:
            print(f"  FAIL: status is {status!r}, wanted 'interview' — a late "
                  "acknowledgement walked it backwards")
            failures += 1
        if not (len(swe) > 3 and swe[tracker.COL["key_date"]]):
            print("  FAIL: key_date was erased by the dateless follow-up")
            failures += 1

    print(f"\n  {failures} failure(s). Delete the {TEST_PREFIX}* rows when done.\n")
    return 1 if failures else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--offline", action="store_true",
        help="check matching and status logic only; no credentials needed",
    )
    args = parser.parse_args()
    raise SystemExit(run_offline() if args.offline else asyncio.run(run_live()))
