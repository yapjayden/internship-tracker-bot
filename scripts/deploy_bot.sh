#!/usr/bin/env bash
# Deploy the query bot to Cloud Run and point Telegram at it.
#
#   ./scripts/deploy_bot.sh
#
# Reads .env for the values the service needs, deploys, then registers the
# webhook. Safe to re-run: Cloud Run replaces the revision, and re-registering
# the webhook is idempotent.
#
# Deliberately NOT passed to the service:
#   - Gmail credentials. The bot reads the tracker and never opens a mailbox.
#   - A service-account key. The service runs AS the service account, so Google
#     mints its tokens directly; a downloaded key would be a permanent
#     credential sitting in a public web service to obtain access it has anyway.

set -euo pipefail

SERVICE="${SERVICE:-internship-bot}"
REGION="${REGION:-asia-southeast1}"   # Singapore

cd "$(dirname "$0")/.."

if [[ ! -f .env ]]; then
  echo "No .env found. Run this from the repo with your local .env in place." >&2
  exit 1
fi

# Pull values from .env without exporting the whole file into this shell.
#
# The trailing `|| true` matters. grep exits 1 when a key is absent, pipefail
# carries that out of the pipeline, and `VAR="$(value_of X)"` takes the
# substitution's status as its own — so under `set -e` one missing optional key
# aborts the whole deploy before it prints a single line. Absent must read as
# empty here; the required ones are checked explicitly just below.
value_of() { grep -E "^$1=" .env | tail -1 | cut -d= -f2- | sed 's/^["'"'"']//;s/["'"'"']$//' || true; }

BOT_TOKEN="$(value_of TELEGRAM_BOT_TOKEN)"
CHAT_ID="$(value_of TELEGRAM_CHAT_ID)"
SPREADSHEET_ID="$(value_of TRACKER_SPREADSHEET_ID)"
GEMINI_KEY="$(value_of GEMINI_API_KEY)"
GEMINI_MODEL="$(value_of GEMINI_MODEL)"
TAVILY_KEY="$(value_of TAVILY_API_KEY)"
DISPLAY_TZ="$(value_of DISPLAY_TIMEZONE)"
WEBHOOK_SECRET="$(value_of TELEGRAM_WEBHOOK_SECRET)"
KEY_FILE="$(value_of GOOGLE_SERVICE_ACCOUNT_FILE)"

for name in BOT_TOKEN CHAT_ID SPREADSHEET_ID; do
  [[ -n "${!name}" ]] || { echo "$name is empty in .env" >&2; exit 1; }
done

# The webhook secret is what stops anyone who guesses the URL from driving the
# bot. Generate one rather than let the deploy proceed without it.
if [[ -z "$WEBHOOK_SECRET" ]]; then
  WEBHOOK_SECRET="$(openssl rand -hex 32)"
  printf '\nTELEGRAM_WEBHOOK_SECRET=%s\n' "$WEBHOOK_SECRET" >> .env
  echo "Generated TELEGRAM_WEBHOOK_SECRET and appended it to .env"
fi

# The service needs the same Sheets identity the pipeline uses, so it can read
# the tracker. Take it from the key file rather than asking again.
if [[ -z "${SHEETS_SA:-}" ]]; then
  [[ -f "$KEY_FILE" ]] || {
    echo "Cannot find $KEY_FILE to read the service-account email." >&2
    echo "Set SHEETS_SA=<name>@<project>.iam.gserviceaccount.com and re-run." >&2
    exit 1
  }
  SHEETS_SA="$(python3 -c "import json,sys;print(json.load(open(sys.argv[1]))['client_email'])" "$KEY_FILE")"
fi

# Same trap as value_of: stderr is suppressed here, so without the guard a
# gcloud that is not logged in would also exit silently.
PROJECT="$(gcloud config get-value project 2>/dev/null || true)"
if [[ -z "$PROJECT" || "$PROJECT" == "(unset)" ]]; then
  echo "No gcloud project is set. Run: gcloud config set project <project-id>" >&2
  exit 1
fi

echo
echo "Project:  $PROJECT"
echo "Service:  $SERVICE  ($REGION)"
echo "Identity: $SHEETS_SA"
echo

# Cloud Build needs to read the source; the runtime needs the Sheets identity.
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --service-account "$SHEETS_SA" \
  --allow-unauthenticated \
  --min-instances 0 \
  --max-instances 2 \
  --memory 512Mi \
  --set-env-vars "TELEGRAM_BOT_TOKEN=$BOT_TOKEN,TELEGRAM_CHAT_ID=$CHAT_ID,TELEGRAM_WEBHOOK_SECRET=$WEBHOOK_SECRET,TRACKER_SPREADSHEET_ID=$SPREADSHEET_ID,GEMINI_API_KEY=$GEMINI_KEY,GEMINI_MODEL=$GEMINI_MODEL,TAVILY_API_KEY=$TAVILY_KEY,DISPLAY_TIMEZONE=$DISPLAY_TZ"

URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format 'value(status.url)')"
echo
echo "Deployed: $URL"

# --allow-unauthenticated is required because Telegram cannot present a Google
# credential. The endpoint is not open: it checks the shared secret header and
# the chat id before doing anything.
echo "Registering webhook..."
curl -sS -X POST "https://api.telegram.org/bot${BOT_TOKEN}/setWebhook" \
  -d "url=${URL}/telegram-webhook" \
  -d "secret_token=${WEBHOOK_SECRET}" \
  -d "drop_pending_updates=true" | python3 -m json.tool

echo
echo "Done. Send /help to the bot."
echo "Logs:"
echo "  gcloud run services logs read $SERVICE --region $REGION --limit 50"
echo "  gcloud beta run services logs tail $SERVICE --region $REGION   # streaming"
