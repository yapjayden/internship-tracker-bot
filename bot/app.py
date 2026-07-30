"""Telegram webhook service, deployed to Cloud Run. Telegram POSTs every
update to /telegram-webhook directly (no polling), so this can scale to zero
between messages and still answer instantly when one arrives.

Handles only the on-demand query side ("what interviews this week") by
reading the tracker. Push notifications for new items are sent separately,
by core/notifier.py from within the pipeline run — this service never
initiates a message on its own.
"""

from __future__ import annotations

from fastapi import FastAPI, Request

from core import tracker
from core.config import load_settings

app = FastAPI()


@app.post("/telegram-webhook")
async def telegram_webhook(request: Request):
    raise NotImplementedError("Stage 6/10: parse Telegram update, route to tracker.query")


@app.get("/healthz")
async def healthz():
    return {"status": "ok"}
