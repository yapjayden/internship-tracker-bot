"""Stage 2 isolation test: fetch new mail and print subjects/senders only.

    python -m scripts.test_gmail_watcher

Confirms OAuth and polling work before any Gemini/tracker/Telegram wiring
exists. Prints nothing but envelope metadata — no bodies — so a test run
never dumps email content to a terminal or CI log.

Passing --reset ignores the stored cursor and re-reads the last day, which
is handy for re-running against the same messages while testing.
"""

import argparse
import asyncio

from core import gmail_watcher, state
from core.config import load_settings

CURSOR_KEY = "gmail"


async def main(reset: bool) -> None:
    settings = load_settings()
    cursor = None if reset else state.read_cursor(CURSOR_KEY)

    print(f"Fetching (cursor={cursor or 'none, first run'})...")
    emails, next_cursor = await gmail_watcher.fetch_new_emails(settings, cursor)

    print(f"\n{len(emails)} new message(s):\n")
    for email in emails:
        print(f"  [{email.received_at:%Y-%m-%d %H:%M}] {email.sender}")
        print(f"      {email.subject}")
        print(f"      ({len(email.body_text)} chars of body parsed)")

    state.write_cursor(CURSOR_KEY, next_cursor)
    print(f"\nCursor advanced to {next_cursor}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--reset", action="store_true", help="ignore stored cursor")
    asyncio.run(main(parser.parse_args().reset))
