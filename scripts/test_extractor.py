"""Stage 4 test: run the extractor over the labelled samples and report where
each field disagrees with the expectation.

    python -m scripts.test_extractor
    python -m scripts.test_extractor --limit 3   # spend less daily quota
    python -m scripts.test_extractor --inbox     # real mail, unlabelled

Reports per field rather than per email. "6/7 emails passed" hides which part
is weak; company, status and date fail for different reasons and get fixed by
different prompt changes.
"""

from __future__ import annotations

import argparse
import asyncio

from core import extractor_agent, gemini, gmail_watcher, router_agent, state
from core.config import load_settings
from core.models import Category, ExtractedDetails
from tests.sample_emails import EXTRACTION_EXPECTATIONS, SAMPLES


def _check(details: ExtractedDetails, expected) -> list[str]:
    """Return a list of human-readable failures; empty means the row is right."""
    problems = []

    company = details.company.strip().lower()
    if not any(alias in company for alias in expected.company_any_of):
        problems.append(
            f"company {details.company!r}, wanted one of {expected.company_any_of}"
        )

    if expected.role_contains not in details.role.strip().lower():
        problems.append(f"role {details.role!r}, wanted to contain {expected.role_contains!r}")

    if details.status != expected.status:
        problems.append(
            f"status {details.status.value!r}, wanted {expected.status.value!r}"
        )

    got_department = (details.department or "").strip().lower()
    if expected.department_contains:
        if expected.department_contains not in got_department:
            problems.append(
                f"department {details.department!r}, wanted to contain "
                f"{expected.department_contains!r}"
            )
    elif got_department:
        # A hallucinated unit is worse than none: it splits one application
        # into two rows and spends a research call on a team that may not exist.
        problems.append(f"department {details.department!r}, wanted none")

    got_date = details.key_date.date() if details.key_date else None
    if got_date != expected.key_date and not (
        expected.key_date_optional and got_date in (None, expected.key_date)
    ):
        problems.append(f"key_date {got_date}, wanted {expected.key_date}")

    return problems


async def run_samples(limit: int | None) -> None:
    settings = load_settings()

    # Only emails the router keeps ever reach the extractor, so testing it on
    # the not_relevant samples would measure something the pipeline never does.
    cases = [
        (email, category)
        for email, category in SAMPLES
        if category != Category.NOT_RELEVANT and email.message_id in EXTRACTION_EXPECTATIONS
    ]
    if limit:
        cases = cases[:limit]

    rpm = gemini.get_limiter().rpm
    estimate = max(0, len(cases) - rpm) * 60 // max(rpm, 1)
    print(
        f"Extracting from {len(cases)} relevant samples, paced at {rpm} req/min "
        f"(~{estimate}s).\n"
    )

    results = await asyncio.gather(
        *(extractor_agent.extract(settings, e, c) for e, c in cases),
        return_exceptions=True,
    )

    field_failures: dict[str, int] = {}
    clean = errored = 0

    for (email, _), result in zip(cases, results):
        expected = EXTRACTION_EXPECTATIONS[email.message_id]

        if isinstance(result, BaseException):
            errored += 1
            text = str(result).split("\n", 1)[0][:160]
            print(f"  ERROR  {email.message_id}  {type(result).__name__}: {text}")
            continue

        problems = _check(result, expected)
        if not problems:
            clean += 1
            print(
                f"  ok     {email.message_id}  {result.company}"
                f"{' / ' + result.department if result.department else ''}"
                f" / {result.role} / {result.status.value} / {result.key_date}"
            )
            continue

        print(f"  MISS   {email.message_id}  {email.subject[:48]}")
        for problem in problems:
            field_failures[problem.split()[0]] = field_failures.get(problem.split()[0], 0) + 1
            print(f"           {problem}")

    print(f"\n  {clean} clean, {len(cases) - clean - errored} with issues, {errored} errored")
    if field_failures:
        summary = ", ".join(f"{field} x{n}" for field, n in sorted(field_failures.items()))
        print(f"  weakest fields: {summary}")
    print()


async def run_inbox() -> None:
    """Route then extract real mail. No ground truth — this catches formatting
    the synthetic corpus does not have, like quoted reply chains and HTML."""
    settings = load_settings()
    emails, _ = await gmail_watcher.fetch_new_emails(settings, await state.read_cursor(settings, "gmail"))
    if not emails:
        print("No new mail since the last cursor. Run test_gmail_watcher --reset first.")
        return

    routes = await asyncio.gather(
        *(router_agent.classify(settings, e) for e in emails), return_exceptions=True
    )
    relevant = [
        (email, route.category)
        for email, route in zip(emails, routes)
        if not isinstance(route, BaseException) and route.category != Category.NOT_RELEVANT
    ]
    print(f"\n{len(relevant)} of {len(emails)} email(s) judged relevant.\n")

    results = await asyncio.gather(
        *(extractor_agent.extract(settings, e, c) for e, c in relevant),
        return_exceptions=True,
    )
    for (email, category), result in zip(relevant, results):
        if isinstance(result, BaseException):
            print(f"  ERROR  {type(result).__name__}: {str(result).split(chr(10))[0][:120]}")
            continue
        print(
            f"  {result.company} / {result.role} / {result.status.value} "
            f"/ {result.key_date}\n         from {category.value}: {email.subject[:55]}"
        )
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", action="store_true", help="run against real recent mail")
    parser.add_argument("--limit", type=int, metavar="N", help="only run the first N samples")
    args = parser.parse_args()
    asyncio.run(run_inbox() if args.inbox else run_samples(args.limit))
