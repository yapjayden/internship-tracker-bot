"""Find the right TELEGRAM_CHAT_ID, and check the one you have.

    python -m scripts.telegram_chat_id

Needs TELEGRAM_BOT_TOKEN set. Asks Telegram who the bot is, reads its recent
updates, and prints the chat id of everyone who has messaged it.

This exists because the obvious guess is wrong in a way the error does not
explain. A bot token looks like "8254553195:AAH...", and that leading number
is the *bot's* id, not yours. Using it produces "Forbidden: the bot can't send
messages to the bot", which reads like a permissions problem rather than a
copy-paste one.
"""

from __future__ import annotations

import asyncio

import httpx

from core.config import get_env
from core.notifier import API_BASE, TIMEOUT_S


async def main() -> None:
    token = get_env("TELEGRAM_BOT_TOKEN")
    if not token:
        raise SystemExit(
            "TELEGRAM_BOT_TOKEN is not set. Get one from @BotFather with /newbot."
        )

    bot_id = token.split(":", 1)[0]
    configured = get_env("TELEGRAM_CHAT_ID")

    async with httpx.AsyncClient(timeout=TIMEOUT_S) as client:
        me = await client.get(f"{API_BASE}/bot{token}/getMe")
        if me.status_code != 200:
            raise SystemExit(
                f"Telegram rejected the token ({me.status_code}): {me.text[:200]}\n"
                "Check TELEGRAM_BOT_TOKEN is the full value including the colon."
            )
        bot = me.json()["result"]
        print(f"\nBot:  @{bot['username']}  (id {bot['id']})")

        if configured:
            if configured == bot_id:
                print(
                    f"\n  TELEGRAM_CHAT_ID is {configured}, which is the BOT's own id.\n"
                    "  That is the number before the colon in the token. You want\n"
                    "  YOUR id instead — see the chats listed below.\n"
                )
            else:
                print(f"Configured TELEGRAM_CHAT_ID: {configured}")

        updates = await client.get(f"{API_BASE}/bot{token}/getUpdates")
        if updates.status_code != 200:
            raise SystemExit(
                f"getUpdates failed ({updates.status_code}): {updates.text[:200]}"
            )

    # A chat only appears here once it has messaged the bot, which is also
    # exactly the condition Telegram requires before the bot may message it.
    chats = {}
    for update in updates.json().get("result", []):
        message = (
            update.get("message")
            or update.get("edited_message")
            or update.get("channel_post")
            or {}
        )
        chat = message.get("chat")
        if chat:
            chats[chat["id"]] = chat

    if not chats:
        print(
            "\nNo chats found.\n\n"
            f"Open Telegram, search for @{bot['username']}, and send it any message —\n"
            "Telegram does not let a bot start a conversation, so this step is\n"
            "required before it can ever message you. Then run this again.\n\n"
            "If you have already messaged it and still see nothing, a webhook may be\n"
            "swallowing the updates. Clear it with:\n"
            f"  curl -s '{API_BASE}/bot<TOKEN>/deleteWebhook'\n"
        )
        return

    print(f"\n{len(chats)} chat(s) have messaged this bot:\n")
    for chat_id, chat in chats.items():
        who = chat.get("username") or chat.get("title") or chat.get("first_name") or "?"
        marker = "  <- currently configured" if str(chat_id) == configured else ""
        print(f"  {chat_id:<16} {chat.get('type'):<10} {who}{marker}")

    private = [cid for cid, c in chats.items() if c.get("type") == "private"]
    if private and str(private[0]) != configured:
        print(f"\nPut this in .env:\n  TELEGRAM_CHAT_ID={private[0]}\n")


if __name__ == "__main__":
    asyncio.run(main())
