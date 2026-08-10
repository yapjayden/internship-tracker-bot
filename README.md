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

python -m scripts.list_models --probe  # find a model your key can really call
                                       # then set GEMINI_MODEL in .env
python -m scripts.test_router          # classify 10 labelled sample emails
python -m scripts.test_router --limit 3   # cheaper run while tuning the prompt
python -m scripts.test_router --inbox  # classify your real recent mail
```

### Choosing a model

Pin `GEMINI_MODEL` explicitly rather than leaving it blank. The default is
the `gemini-flash-latest` alias, which silently moves between model
generations and has already broken this project twice — once when its target
changed the thinking-config format, once when it landed on a flagship whose
free tier allows only 20 requests *per day*.

Do not pick a model by reading `list_models` output. That listing is what
exists, not what your key may call: it advertises the entire 2.5 generation,
all of which returns `404 no longer available to new users` for keys created
recently. `--probe` sends one real request per model and reports what
actually answers. Prefer a `flash-lite` model — routing is a short
single-label task, and lite tiers carry much larger free daily quotas.

When Gemini calls fail, the error tells you which tool to reach for:

| Error | Meaning | Next step |
|---|---|---|
| `404 no longer available` | model retired for your key | `list_models --probe` |
| `400 INVALID_ARGUMENT` | request option unsupported | `diagnose_gemini` |
| `429 ...PerMinute...` | rate too high | lower `GEMINI_RPM` |
| `429 ...PerDay...` | daily allowance spent | switch `GEMINI_MODEL` — the quota is per model, so a different one is usable immediately |

Each credential in `.env` must be on a single line, unquoted — long values
pasted from a browser often wrap, which produces confusing `invalid_client`
errors during OAuth.

The OAuth step needs a browser, so it runs on your machine, not in CI. The
refresh token it mints is what lets the headless GitHub Actions job read
mail later.

## Status

Stage 3 complete: Gmail watcher and router agent working, 10/10 on the
labelled sample corpus. Extractor, research agents, tracker, and Telegram bot
are still stubs.

Caveat on that score: the router returned confidence 1.00 on all ten samples,
including the ones written to be ambiguous. Confidence is currently carrying
no information, so nothing downstream should gate on it until it has been
checked against real mail via `--inbox`.

The pipeline runs on a GitHub Actions cron, and the schedule in
`.github/workflows/pipeline.yml` is still commented out. Gmail push
notifications (Pub/Sub -> Cloud Run) are the intended trigger but require a
billing account; see the hosting note above.
