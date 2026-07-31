"""Central config/env loading. Import `settings` everywhere else instead of
reading os.environ directly, so there's exactly one place that knows how
config is sourced (a local .env file vs. real env vars in CI/Cloud Run)."""

import os
import sys
from dataclasses import dataclass

from dotenv import load_dotenv

if sys.version_info < (3, 11):
    raise RuntimeError(
        f"This project needs Python 3.11+, found {sys.version_info.major}."
        f"{sys.version_info.minor}. macOS ships 3.9 by default; create the venv "
        "with a newer interpreter (see README)."
    )

load_dotenv()


def get_env(name: str, default: str = "") -> str:
    """Read an env var, tolerating the usual copy-paste damage: surrounding
    quotes, stray whitespace, and the line breaks that sneak in when a long
    credential is pasted out of a browser."""
    value = os.environ.get(name)
    if value is None:
        return default
    value = value.strip().strip("\"'").strip()
    return "".join(value.split())


def _require(name: str) -> str:
    value = get_env(name)
    if not value:
        raise RuntimeError(f"Missing required env var: {name}")
    return value


@dataclass(frozen=True)
class Settings:
    # Gmail OAuth (installed-app flow, refresh token minted once locally)
    gmail_client_id: str
    gmail_client_secret: str
    gmail_refresh_token: str

    # Everything below belongs to a later build stage. They default to empty
    # so earlier stages can be run and tested before those accounts exist —
    # the module that needs one calls require_setting() at point of use.
    gemini_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    google_service_account_json: str = ""
    tracker_spreadsheet_id: str = ""


def require_setting(value: str, env_name: str) -> str:
    """Assert a later-stage setting is present, at the point it's actually
    used rather than at load time."""
    if not value:
        raise RuntimeError(f"Missing required env var: {env_name}")
    return value


def load_settings() -> Settings:
    return Settings(
        gmail_client_id=_require("GMAIL_CLIENT_ID"),
        gmail_client_secret=_require("GMAIL_CLIENT_SECRET"),
        gmail_refresh_token=_require("GMAIL_REFRESH_TOKEN"),
        gemini_api_key=get_env("GEMINI_API_KEY"),
        telegram_bot_token=get_env("TELEGRAM_BOT_TOKEN"),
        telegram_chat_id=get_env("TELEGRAM_CHAT_ID"),
        # Service-account JSON is the one value that legitimately contains
        # whitespace, so it skips get_env's whitespace stripping.
        google_service_account_json=os.environ.get("GOOGLE_SERVICE_ACCOUNT_JSON", "").strip(),
        tracker_spreadsheet_id=get_env("TRACKER_SPREADSHEET_ID"),
    )
