# internship-tracker-bot

A Telegram bot that reads Gmail and Outlook, identifies internship-related
emails (interview invites, OA/assessment invites, results), extracts
structured details, logs them to a Google Sheets tracker, and notifies you
on Telegram. New interviews trigger parallel per-company research agents
that return a prep brief alongside the notification.

## Architecture

- `core/` — shared library: mail watchers (Gmail + Outlook), router agent,
  extractor agent, research agent, tracker (Google Sheets), notifier.
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

Copy `.env.example` to `.env` and fill in credentials. See the project plan
for the one-time OAuth setup scripts (Gmail, Outlook) needed to mint refresh
tokens.

## Status

Stage 1: project scaffolded, module interfaces stubbed. Not yet functional.
