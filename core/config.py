"""Central config/env loading. Import `settings` everywhere else instead of
reading os.environ directly, so there's exactly one place that knows how
config is sourced (a local .env file vs. real env vars in CI/Cloud Run)."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _require(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    # Gemini
    gemini_api_key: str

    # Gmail OAuth (installed-app flow, refresh token minted once locally)
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str

    # Outlook / Microsoft Graph OAuth (Azure AD app, refresh token minted once locally)
    outlook_client_id: str
    outlook_client_secret: str
    outlook_refresh_token: str
    outlook_tenant_id: str

    # Telegram
    telegram_bot_token: str
    telegram_chat_id: str

    # Google Sheets tracker
    google_service_account_json: str
    tracker_spreadsheet_id: str


def load_settings() -> Settings:
    return Settings(
        gemini_api_key=_require("GEMINI_API_KEY"),
        gmail_client_id=_require("GMAIL_CLIENT_ID"),
        gmail_client_secret=_require("GMAIL_CLIENT_SECRET"),
        gmail_refresh_token=_require("GMAIL_REFRESH_TOKEN"),
        outlook_client_id=_require("OUTLOOK_CLIENT_ID"),
        outlook_client_secret=_require("OUTLOOK_CLIENT_SECRET"),
        outlook_refresh_token=_require("OUTLOOK_REFRESH_TOKEN"),
        outlook_tenant_id=os.environ.get("OUTLOOK_TENANT_ID", "common"),
        telegram_bot_token=_require("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=_require("TELEGRAM_CHAT_ID"),
        google_service_account_json=_require("GOOGLE_SERVICE_ACCOUNT_JSON"),
        tracker_spreadsheet_id=_require("TRACKER_SPREADSHEET_ID"),
    )
