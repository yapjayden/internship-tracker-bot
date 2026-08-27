#!/usr/bin/env bash
# Drive the pipeline from Cloud Scheduler instead of GitHub's cron.
#
#   ./scripts/setup_scheduler.sh
#
# Safe to re-run: updates the job in place if it already exists.
#
# Why this exists. The workflow asks for */15, and GitHub does not deliver it.
# Measured over 32 hours: 30 runs against 128 requested, a median gap of 46
# minutes, and one hole of five hours — followed by ten hours of nothing at
# all. It does not even fire on the requested minute; observed starts were
# scattered across every part of the hour, so GitHub's own advice about moving
# off the top of the hour does not apply. Scheduled events are best-effort, and
# a delayed one causes the next slot to be skipped rather than queued, which is
# how */15 collapses to roughly hourly.
#
# Nothing was ever lost to this — the cursor lives in the sheet and a late run
# reads everything since the last one — but an interview invitation arriving
# ten hours late is the thing the tracker exists to prevent.
#
# So the pipeline stays on Actions, which is free and unlimited for a public
# repo, and only the clock moves. Cloud Scheduler calls the workflow_dispatch
# API, which dispatches immediately: measured at 4 seconds from call to a
# running job. The first 3 scheduler jobs are free, so this stays inside the
# free tier.
#
# The GitHub cron is deliberately left in place as a backstop. If this job or
# the token stops working, the unreliable schedule still eventually fires, and
# the workflow's concurrency group keeps a doubled-up run from overlapping.

set -euo pipefail

JOB="${JOB:-internship-pipeline}"
REGION="${REGION:-asia-southeast1}"   # Singapore
REPO="${REPO:-yapjayden/internship-tracker-bot}"
WORKFLOW="${WORKFLOW:-pipeline.yml}"
BRANCH="${BRANCH:-main}"
SCHEDULE="${SCHEDULE:-*/15 * * * *}"

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env found. Run this from the repo with your local .env in place." >&2
  exit 1
fi

# Same guard as deploy_bot.sh: grep exits 1 on an absent key, and under
# `set -e` that would kill the script with no output at all.
value_of() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed 's/^["'"'"']//;s/["'"'"']$//' || true; }

TOKEN="$(value_of GITHUB_DISPATCH_TOKEN)"

if [[ -z "$TOKEN" ]]; then
  cat >&2 <<'MSG'
GITHUB_DISPATCH_TOKEN is empty in .env.

Cloud Scheduler needs a GitHub token to trigger the workflow. Make a
fine-grained one scoped to this repository alone:

  https://github.com/settings/personal-access-tokens/new

  Repository access  ->  Only select repositories  ->  internship-tracker-bot
  Permissions        ->  Repository permissions -> Actions -> Read and write

That is the whole grant: it can start this workflow and nothing else. It
cannot read your code, your other repositories, or your account. Set an
expiry you are willing to renew — when it lapses the job starts failing,
and the GitHub cron is what carries you until you notice.

Then add it to .env on one line:

  GITHUB_DISPATCH_TOKEN=github_pat_...
MSG
  exit 1
fi

PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "No gcloud project is set. Run: gcloud config set project <project-id>" >&2
  exit 1
fi

URI="https://api.github.com/repos/${REPO}/actions/workflows/${WORKFLOW}/dispatches"

echo
echo "Project:  $PROJECT"
echo "Job:      $JOB  ($REGION)"
echo "Schedule: $SCHEDULE  UTC"
echo "Target:   $REPO  $WORKFLOW  on $BRANCH"
echo

gcloud services enable cloudscheduler.googleapis.com --quiet

# GitHub rejects API calls with no User-Agent, so set one explicitly rather
# than relying on what Cloud Scheduler happens to send.
HEADERS="Accept=application/vnd.github+json"
HEADERS="$HEADERS,Authorization=Bearer $TOKEN"
HEADERS="$HEADERS,X-GitHub-Api-Version=2022-11-28"
HEADERS="$HEADERS,User-Agent=cloud-scheduler-internship-tracker"

# create fails if the job exists; update fails if it does not. Try update
# first so re-running this after a token rotation just works.
if gcloud scheduler jobs describe "$JOB" --location "$REGION" >/dev/null 2>&1; then
  ACTION=update
else
  ACTION=create
fi

# --format=none, and stdout to /dev/null, because gcloud echoes the created
# job back in full — including the Authorization header. Printing that puts a
# live credential into terminal scrollback, CI logs, and any screenshot or
# paste of a failed run. Errors still reach stderr, so a real failure is not
# hidden by this.
gcloud scheduler jobs "$ACTION" http "$JOB" \
  --location "$REGION" \
  --schedule "$SCHEDULE" \
  --time-zone "UTC" \
  --uri "$URI" \
  --http-method POST \
  --headers "$HEADERS" \
  --message-body "{\"ref\":\"$BRANCH\"}" \
  --attempt-deadline 30s \
  --format=none >/dev/null

echo
echo "Job ${ACTION}d. Firing once now to check the token and the target..."
gcloud scheduler jobs run "$JOB" --location "$REGION" --format=none >/dev/null

echo
echo "A 204 from GitHub means it worked; the run appears within seconds at"
echo "  https://github.com/${REPO}/actions"
echo
echo "Last result (give it a moment, then re-run this line if it says nothing):"
echo "  gcloud scheduler jobs describe $JOB --location $REGION --format 'value(status)'"
echo
# --format=none on pause for the same reason as above: it echoes the job back,
# Authorization header and all.
echo "To stop it:  gcloud scheduler jobs pause $JOB --location $REGION --format=none"
