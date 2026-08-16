"""Telegram webhook service. Telegram POSTs every update to
/telegram-webhook directly (no polling), so this can scale to zero between
messages and still answer instantly when one arrives.

Handles only the on-demand query side. Push notifications for new items are
sent separately, by core/notifier.py from within the pipeline run — this
service never initiates a message.

Command rendering lives in bot/commands.py, which is pure and testable. This
file is transport: check the caller, read the sheet, send the reply.
"""

from __future__ import annotations

import logging
import os

from fastapi import FastAPI, Request, Response

from bot import commands
from core import notifier, tracker
from core.config import load_settings

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Telegram sends this header on every webhook call when a secret was supplied
# at registration. Without it the endpoint is a public URL that anyone who
# guesses it can drive.
SECRET_HEADER = "X-Telegram-Bot-Api-Secret-Token"


def _authorised(request: Request) -> bool:
    expected = os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()
    if not expected:
        # Refuse rather than run open. Failing closed on a missing secret is
        # the difference between a broken bot and a public one.
        logger.error("TELEGRAM_WEBHOOK_SECRET is not set; rejecting webhook call")
        return False
    return request.headers.get(SECRET_HEADER, "") == expected


@app.post("/telegram-webhook")
async def telegram_webhook(request: Request) -> Response:
    if not _authorised(request):
        return Response(status_code=403)

    update = await request.json()
    message = update.get("message") or update.get("edited_message") or {}
    chat_id = str((message.get("chat") or {}).get("id", ""))
    text = message.get("text", "")

    # Always 200 for anything unhandled — joins, photos, stickers. A non-2xx
    # makes Telegram retry the same update indefinitely.
    if not chat_id or not text:
        return Response(status_code=200)

    settings = load_settings()

    # The bot's username is public, so anyone can message it. Without this
    # check a stranger could read where you are interviewing.
    if chat_id != settings.telegram_chat_id:
        logger.warning("Ignoring message from unknown chat %s", chat_id)
        return Response(status_code=200)

    try:
        applications = await tracker.load_applications(settings)
    except Exception as exc:
        logger.exception("Failed to read tracker")
        await notifier.send_message(settings, f"Could not read the tracker: {exc}")
        return Response(status_code=200)

    for reply in commands.dispatch(applications, text):
        await notifier.send_message(settings, reply)

    return Response(status_code=200)


@app.get("/healthz")
async def healthz() -> dict:
    return {"status": "ok"}
