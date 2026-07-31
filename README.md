# internship-tracker-bot

A Telegram bot that reads Gmail, identifies internship-related emails
(interview invites, OA/assessment invites, results), extracts structured
details, logs them to a Google Sheets tracker, and notifies you on Telegram.
New interviews trigger parallel per-company research agents that return a
prep brief alongside the notification.

## Architecture

- `core/` — shared library: Gmail watcher, router agent, extractor agent,
  research agent, tracker (Google Sheets), notifier.
- `pipeline/run_once.py` — one full pass (watch -> route -> extract ->
  research -> track -> notify), run on a schedule via GitHub Actions.
- `bot/app.py` — Telegram webhook service (on-demand queries), deployed to
  Google Cloud Run.

Hosting is split this way so both halves stay on free tiers: the email
pipeline only needs to wake up periodically (GitHub Actions cron), while the
query bot needs to be reachable but is cheap per-request (Cloud Run,
scale-to-zero). The tracker lives in Google Sheets rather than a local file
since neither environment has persistent disk between runs.

## Setup

Requires **Python 3.11+**. macOS ships 3.9, which will not work — check with
`python3 --version` and install a newer one first if needed (`brew install
python@3.12`).

```bash
python3.12 -m venv .venv       # or python3.11 / python3.13
source .venv/bin/activate
python --version               # must be 3.11 or newer
pip install -r requirements.txt

cp .env.example .env           # then fill in GMAIL_CLIENT_ID / GMAIL_CLIENT_SECRET
python -m scripts.gmail_oauth_setup    # one-time; prints GMAIL_REFRESH_TOKEN
python -m scripts.test_gmail_watcher   # prints new subjects/senders
```

Each credential in `.env` must be on a single line, unquoted — long values
pasted from a browser often wrap, which produces confusing `invalid_client`
errors during OAuth.

The OAuth step needs a browser, so it runs on your machine, not in CI. The
refresh token it mints is what lets the headless GitHub Actions job read
mail later.

## Status

Stage 2: Gmail watcher implemented and testable in isolation. Router,
extractor, research agents, tracker, and Telegram bot are still stubs.
