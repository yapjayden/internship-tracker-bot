"""Stage 2 isolation test: fetch new mail and print subjects/senders only.

    python -m scripts.test_gmail_watcher

Confirms OAuth and polling work before any Gemini/tracker/Telegram wiring
exists. Prints nothing but envelope metadata — no bodies — so a test run
never dumps email content to a terminal or CI log.

Passing --reset ignores the stored cursor and re-reads from the start of the
lookback window, which is handy for re-running against the same messages.

This script does NOT advance the cursor unless you pass --commit. It is a
diagnostic: advancing by default made it destructive, because the next tool
you reach for then sees an empty mailbox. Pass --commit only when you
deliberately want to mark this mail as processed.
"""

import argparse
import asyncio

from core import gmail_watcher, state
from core.config import load_settings

CURSOR_KEY = "gmail"


async def main(reset: bool, commit: bool) -> None:
    settings = load_settings()
    cursor = None if reset else await state.read_cursor(settings, CURSOR_KEY)

    print(f"Fetching (cursor={cursor or 'none, first run'})...")
    emails, next_cursor = await gmail_watcher.fetch_new_emails(settings, cursor)

    print(f"\n{len(emails)} new message(s):\n")
    for email in emails:
        print(f"  [{email.received_at:%Y-%m-%d %H:%M}] {email.sender}")
        print(f"      {email.subject}")
        print(f"      ({len(email.body_text)} chars of body parsed)")

    if commit:
        await state.write_cursor(settings, CURSOR_KEY, next_cursor)
        print(f"\nCursor advanced to {next_cursor}")
    else:
        print(
            f"\nCursor left unchanged (would have moved to {next_cursor}).\n"
            "The same mail is still available to test_router --inbox and "
            "test_extractor --inbox.\nPass --commit to mark it processed."
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="ignore stored cursor")
    parser.add_argument(
        "--commit", action="store_true",
        help="advance the stored cursor past this mail (off by default)",
    )
    args = parser.parse_args()
    asyncio.run(main(args.reset, args.commit))
