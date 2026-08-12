"""Stage 6 test: check message formatting, then optionally send for real.

    python -m scripts.test_notifier --offline   # print messages, no token
    python -m scripts.test_notifier             # send them to your chat

--offline needs no credentials. It renders one message per status so you can
see what will actually arrive, and checks the two things that silently break
a live send: HTML escaping and the 4096-character limit.

The live run sends the same messages to TELEGRAM_CHAT_ID. It ignores
NOTIFY_STATUSES deliberately — you are testing delivery here, not the filter.
"""

from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timedelta, timezone

from core import notifier
from core.config import load_settings
from core.models import (
    ApplicationStatus,
    Category,
    Email,
    ExtractedDetails,
    MailSource,
    ResearchBrief,
)

CATEGORY_FOR = {
    ApplicationStatus.APPLIED: Category.OTHER,
    ApplicationStatus.ASSESSMENT: Category.OA,
    ApplicationStatus.INTERVIEW: Category.INTERVIEW,
    ApplicationStatus.OFFER: Category.RESULT,
    ApplicationStatus.REJECTED: Category.RESULT,
    ApplicationStatus.ACTION_NEEDED: Category.OTHER,
    ApplicationStatus.UNKNOWN: Category.OTHER,
}


def _email(subject: str) -> Email:
    return Email(
        source=MailSource.GMAIL,
        message_id="test",
        sender="recruiting@example.com",
        subject=subject,
        received_at=datetime.now(timezone.utc),
        body_text="",
    )


def _cases() -> list[tuple[str, ExtractedDetails, Email, ResearchBrief | None, bool]]:
    soon = datetime.now(timezone.utc) + timedelta(days=5)

    def details(company, role, status, key_date=None, next_steps=None):
        return ExtractedDetails(
            company=company, role=role, status=status,
            key_date=key_date, next_steps=next_steps,
        )

    return [
        (
            "interview, new, with date and next steps",
            details("Shopee", "Software Engineer Intern", ApplicationStatus.INTERVIEW,
                    soon, "Book a slot before 25 August."),
            _email("Interview Invitation — Software Engineer Intern"),
            None, True,
        ),
        (
            "offer",
            details("GovTech", "Software Engineering Intern", ApplicationStatus.OFFER,
                    soon, "Return the signed letter by 25 August."),
            _email("Offer — Software Engineering Intern"),
            None, True,
        ),
        (
            "rejection, updating an existing row",
            details("Sea", "Product Analyst Intern", ApplicationStatus.REJECTED),
            _email("Update on your application"),
            None, False,
        ),
        (
            "acknowledgement — silent under the default filter",
            details("Grab", "Backend Engineer Intern", ApplicationStatus.APPLIED),
            _email("We've received your application"),
            None, True,
        ),
        (
            "no date, no next steps — optional fields absent",
            details("Jane Street", "Quantitative Trading Intern", ApplicationStatus.INTERVIEW),
            _email("Scheduling your final round"),
            None, True,
        ),
        (
            # The characters that would break the send if escaping regressed.
            "HTML-hostile company and role",
            details("<b>Ampersand & Co</b>", 'Intern "quoted" <script>alert(1)</script>',
                    ApplicationStatus.ASSESSMENT, soon, "Finish the test <before> Friday & report."),
            _email("Assessment ready & waiting <urgent>"),
            None, True,
        ),
        (
            "interview with an oversized research brief",
            details("Stripe", "Software Engineering Intern", ApplicationStatus.INTERVIEW, soon),
            _email("Technical interview"),
            ResearchBrief(
                company="Stripe",
                brief_text="Payments infrastructure. " * 400,
                generated_at=datetime.now(timezone.utc),
            ),
            True,
        ),
    ]


def run_offline() -> int:
    failures = 0
    print(f"\nDefault notify statuses: "
          f"{sorted(s.value for s in notifier.notify_statuses())}\n")

    for label, details, email, brief, is_new in _cases():
        text = notifier.build_message(
            CATEGORY_FOR[details.status], details, email, brief, is_new
        )
        silent = "" if notifier.should_notify(details.status) else "   [SILENT by default]"
        print("=" * 68)
        print(f"{label}{silent}")
        print("=" * 68)
        print(text)
        print()

        if len(text) > notifier.MAX_MESSAGE_CHARS:
            print(f"  FAIL: {len(text)} chars exceeds Telegram's limit\n")
            failures += 1

        # Raw angle brackets from email text would make Telegram reject the
        # whole message as malformed HTML.
        stripped = text
        for tag in ("<b>", "</b>", "<i>", "</i>"):
            stripped = stripped.replace(tag, "")
        if "<" in stripped or ">" in stripped:
            print("  FAIL: unescaped angle bracket outside the intended tags\n")
            failures += 1

    print(f"  {failures} failure(s)\n")
    return 1 if failures else 0


async def run_live() -> int:
    settings = load_settings()
    cases = _cases()
    print(f"\nSending {len(cases)} test message(s) to chat {settings.telegram_chat_id}...\n")

    sent = 0
    for label, details, email, brief, is_new in cases:
        text = notifier.build_message(
            CATEGORY_FOR[details.status], details, email, brief, is_new
        )
        ok = await notifier.send_message(settings, text)
        sent += ok
        print(f"  {'sent' if ok else 'FAILED'}  {label}")

    print(f"\n  {sent}/{len(cases)} delivered.\n")
    return 0 if sent == len(cases) else 1


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--offline", action="store_true",
        help="render messages without sending; no credentials needed",
    )
    args = parser.parse_args()
    raise SystemExit(run_offline() if args.offline else asyncio.run(run_live()))
