"""Stage 3 test: run the router against the sample corpus and report where
it disagrees with the expected label.

    python -m scripts.test_router          # synthetic samples
    python -m scripts.test_router --inbox  # your real recent mail, unlabelled

The synthetic run is the one to trust for correctness — it has ground truth.
The --inbox run is a smoke test against real formatting quirks; it prints
subjects and classifications only, never bodies.
"""

import argparse
import asyncio

from core import gemini, gmail_watcher, router_agent, state
from core.config import load_settings
from tests.sample_emails import SAMPLES


async def run_samples() -> None:
    settings = load_settings()

    rpm = gemini.get_limiter().rpm
    estimate = max(0, (len(SAMPLES) - rpm)) * 60 // max(rpm, 1)
    print(
        f"Classifying {len(SAMPLES)} samples, paced at {rpm} req/min "
        f"(~{estimate}s). Set GEMINI_RPM in .env if your quota allows more."
    )

    # Classification of one email never depends on another, so these are
    # safe to run concurrently; the shared rate limiter paces the actual
    # API calls so concurrency never becomes a quota burst.
    results = await asyncio.gather(
        *(router_agent.classify(settings, email) for email, _ in SAMPLES),
        return_exceptions=True,
    )

    passed = failed = errored = 0
    print()
    for (email, expected), result in zip(SAMPLES, results):
        if isinstance(result, Exception):
            errored += 1
            print(f"  ERROR  {email.subject[:55]}\n         {type(result).__name__}: {result}")
            continue

        ok = result.category == expected
        passed, failed = (passed + 1, failed) if ok else (passed, failed + 1)
        mark = "  ok  " if ok else " MISS "
        print(f"  {mark} {email.subject[:55]}")
        print(f"         got {result.category.value} ({result.confidence:.2f})")
        if not ok:
            print(f"         expected {expected.value}")

    print(f"\n  {passed} passed, {failed} misclassified, {errored} errored\n")


async def run_inbox() -> None:
    settings = load_settings()
    emails, _ = await gmail_watcher.fetch_new_emails(settings, state.read_cursor("gmail"))
    if not emails:
        print("No new mail since the last cursor. Run test_gmail_watcher --reset first.")
        return

    results = await asyncio.gather(
        *(router_agent.classify(settings, e) for e in emails),
        return_exceptions=True,
    )
    print()
    for email, result in zip(emails, results):
        verdict = (
            f"{type(result).__name__}: {result}"
            if isinstance(result, Exception)
            else f"{result.category.value} ({result.confidence:.2f})"
        )
        print(f"  {verdict:<40} {email.subject[:50]}")
    print()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--inbox", action="store_true", help="classify real recent mail")
    args = parser.parse_args()
    asyncio.run(run_inbox() if args.inbox else run_samples())
