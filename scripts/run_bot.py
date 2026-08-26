"""Run the bot from your own machine, no hosting required.

    python -m scripts.run_bot

Polls Telegram for new messages and answers them, using exactly the command
code bot/app.py serves. Ctrl-C to stop.

This exists because the webhook needs a public URL and therefore a host, and
that decision has a cost attached. Polling needs neither: Telegram is asked
for updates rather than pushing them, so nothing has to be reachable from the
internet.

The catch is the obvious one — it only answers while this is running. Close
the terminal and the bot goes quiet again. Notifications are unaffected; those
come from the scheduled pipeline and do not involve this process at all.

Useful for trying the commands against real data before committing to a
deployment, and a perfectly reasonable permanent setup if you are happy to
start it when you want to ask something.
"""

from __future__ import annotations

import asyncio
import logging

import httpx

from bot import commands
from core import notifier, tracker
from core.config import load_settings

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("bot")

# Long-poll: Telegram holds the request open until something arrives or this
# elapses, so an idle bot makes one request every 30s rather than hammering.
POLL_TIMEOUT_S = 30
HTTP_TIMEOUT_S = POLL_TIMEOUT_S + 15


async def _answer(settings, text: str) -> None:
    try:
        applications = await tracker.load_applications(settings)
    except Exception as exc:
        logger.exception("Failed to read tracker")
        await notifier.send_message(settings, f"Could not read the tracker: {exc}")
        return

    try:
        replies = await commands.dispatch_async(settings, applications, text)
    except Exception as exc:
        logger.exception("Command failed")
        await notifier.send_message(settings, f"That command failed: {exc}")
        return

    for reply in replies:
        await notifier.send_message(settings, reply)


async def main() -> None:
    settings = load_settings(require_gmail=False)
    token = settings.telegram_bot_token
    if not token:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set.")
    if not settings.telegram_chat_id:
        raise SystemExit(
            "TELEGRAM_CHAT_ID is not set. Run: python -m scripts.telegram_chat_id"
        )

    base = f"{notifier.API_BASE}/bot{token}"
    print(
        f"\nListening for commands. Ctrl-C to stop.\n"
        f"Answering chat {settings.telegram_chat_id} only.\n"
        f"Try /help in Telegram.\n"
    )

    offset: int | None = None
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT_S) as client:
        while True:
            params = {"timeout": POLL_TIMEOUT_S}
            if offset is not None:
                params["offset"] = offset
            try:
                response = await client.get(f"{base}/getUpdates", params=params)
            except httpx.RequestError as exc:
                logger.warning("Poll failed (%s); retrying in 5s", exc)
                await asyncio.sleep(5)
                continue

            if response.status_code == 409:
                # Telegram refuses getUpdates while a webhook is registered.
                raise SystemExit(
                    "Telegram reports a webhook is registered for this bot, which "
                    "blocks polling. Remove it with:\n"
                    f"  curl -s '{notifier.API_BASE}/bot<TOKEN>/deleteWebhook'"
                )
            if response.status_code != 200:
                logger.warning("Telegram %d: %s", response.status_code, response.text[:200])
                await asyncio.sleep(5)
                continue

            for update in response.json().get("result", []):
                # Advance past this update whatever happens to it, so one
                # unparseable message cannot wedge the loop replaying forever.
                offset = update["update_id"] + 1

                message = update.get("message") or update.get("edited_message") or {}
                chat_id = str((message.get("chat") or {}).get("id", ""))
                text = (message.get("text") or "").strip()
                if not text:
                    continue
                if chat_id != settings.telegram_chat_id:
                    logger.warning("Ignoring message from unknown chat %s", chat_id)
                    continue

                logger.info("→ %s", text)
                await _answer(settings, text)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nStopped. The bot will not answer commands until you run this again.")
