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
python -m scripts.test_router          # classify the labelled sample emails
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

Stage 8 complete. The whole pipeline runs end to end: watch -> route ->
extract -> research (parallel, interviews only) -> track -> notify. The bot's
on-demand query side (`bot/app.py`) is the remaining stub.

Research fans out with `asyncio.gather`, one independent agent per company
*and business unit*, filtered three ways so it stays cheap: interviews only,
one brief per unit however many emails mention it, and nothing already briefed
in the sheet. A research failure degrades that notification's brief without
costing the row or the alert.

Business units are first-class. Shopee runs Fulfilled by Shopee (FBS), Shopee
Mall and Shopee Supported Logistics as separate operations that hire and
interview separately, so they get separate tracker rows and separate briefs. A
unit named in one email and omitted in the next is still one application; two
differently named units never merge.

Three LLM agents so far: router (which category), extractor (which fields),
research (what to know before the interview). The notifier and tracker are not
agents; they push and persist what the agents decide.

Research needs a free Tavily key (`TAVILY_API_KEY`, no card). Gemini's
built-in Google Search grounding was tried first and does not work on the free
tier: grounded requests are billed against the ordinary `generate_content`
request quota rather than the separate search-grounding quota, so they buy no
search budget and fail alongside routing and extraction. The grounding path is
still there and is selected automatically when no Tavily key is set — it is
the right choice on a paid tier, where the attribution is correct.

- Router: 13/13 on the labelled corpus, and running live on a schedule. Two
  of those cases came from real mail: a verification code it first filed as an
  application update, and a feedback-form confirmation it correctly rejected.
- Tracker: verified against a real spreadsheet — two roles at one employer
  stay separate, a legal-name variant updates in place, out-of-order mail
  cannot walk a status backwards, and a dateless follow-up does not erase a
  known key_date.
- Extractor: 8/8 clean on the labelled corpus, every field. Both intermediary
  fixtures resolved to the employer rather than the sender's domain
  (HackerRank -> GovTech, Greenhouse -> Sea Group); the one email that implies
  a date without naming one yielded no key_date; and the Shopee/FBS fixture
  produced the business unit while the seven emails naming none left it empty
  rather than inventing one.
- Notifier: verified live — messages delivered to a real chat. Rendering,
  HTML escaping, length capping and retry behaviour also covered offline and
  against a mocked transport.

Two caveats worth carrying forward:

Router confidence is nearly always 1.00 and should not be gated on. Real mail
moved it slightly — the one false positive so far scored 0.90 and a borderline
rejection 0.95, against 1.00 elsewhere — which is suggestive but nowhere near
a usable threshold.

The tracker's one-row-per-application design depends on company-name
matching. `scripts/test_tracker.py --offline` covers the cases we thought of;
real mail will find others. A wrongly merged row is the failure to watch for.

The pipeline runs live on a GitHub Actions cron, every 15 minutes. GitHub
treats scheduled runs as best-effort and delays them under load, so treat that
as approximate; nothing depends on the interval, since the cursor lives in the
sheet and a late run simply picks up more. Two operational notes: GitHub
disables scheduled workflows after 60 days of repository inactivity, and a
failing run emails you, which is the only signal the tracker has stopped.

Gmail push notifications (Pub/Sub -> Cloud Run) would replace the cron with
seconds-latency delivery, but require a billing account; see the hosting note
above.
