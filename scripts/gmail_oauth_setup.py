"""One-time interactive Gmail OAuth. Run this locally (it needs a browser),
approve access, and paste the refresh token it prints into your .env as
GMAIL_REFRESH_TOKEN.

    python -m scripts.gmail_oauth_setup

Needs GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET set in .env first. The token
this mints is what lets the headless GitHub Actions job read mail later —
CI can't do an interactive consent flow itself, which is why this is a
separate manual step you only do once.
"""

import os

from dotenv import load_dotenv
from google_auth_oauthlib.flow import InstalledAppFlow

load_dotenv()

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def main() -> None:
    client_id = os.environ.get("GMAIL_CLIENT_ID")
    client_secret = os.environ.get("GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first "
            "(copy .env.example to .env)."
        )

    flow = InstalledAppFlow.from_client_config(
        {
            "installed": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": ["http://localhost"],
            }
        },
        scopes=SCOPES,
    )

    # access_type=offline + prompt=consent is what actually returns a refresh
    # token; without prompt=consent Google omits it on repeat authorizations.
    creds = flow.run_local_server(
        port=0,
        access_type="offline",
        prompt="consent",
    )

    if not creds.refresh_token:
        raise SystemExit(
            "No refresh token returned. Revoke the app's access at "
            "https://myaccount.google.com/permissions and run this again."
        )

    print("\n" + "=" * 60)
    print("Success. Add this line to your .env:\n")
    print(f"GMAIL_REFRESH_TOKEN={creds.refresh_token}")
    print("=" * 60)


if __name__ == "__main__":
    main()
