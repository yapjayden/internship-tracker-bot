"""One-time interactive Gmail OAuth. Run this locally (it needs a browser),
approve access, and paste the refresh token it prints into your .env as
GMAIL_REFRESH_TOKEN.

    python -m scripts.gmail_oauth_setup

Needs GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET set in .env first. The token
this mints is what lets the headless GitHub Actions job read mail later —
CI can't do an interactive consent flow itself, which is why this is a
separate manual step you only do once.
"""

from oauthlib.oauth2.rfc6749.errors import InvalidClientError

# Importing config first enforces the Python version check and loads .env.
from core.config import get_env

from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CLIENT_SECRET_HELP = """
The client secret was rejected (invalid_client). The consent step worked, so
the client ID is fine — only the secret is wrong. Check, in order:

  1. Open .env and confirm GMAIL_CLIENT_SECRET is on ONE line with no quotes,
     no spaces, and nothing wrapped onto a second line.
  2. Confirm it starts with 'GOCSPX-'.
  3. If you reset the secret in Google Cloud Console, the old value is dead.
     Go to Credentials -> your OAuth client -> copy the current secret, or
     'Add secret' and use the new one.
  4. Confirm the secret belongs to the SAME OAuth client as the client ID
     (easy to mix up if you created more than one).
"""


def main() -> None:
    client_id = get_env("GMAIL_CLIENT_ID")
    client_secret = get_env("GMAIL_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise SystemExit(
            "Set GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET in .env first "
            "(copy .env.example to .env)."
        )

    # Cheap shape checks, so obvious paste damage is caught before a browser
    # round-trip rather than after it.
    if not client_id.endswith(".apps.googleusercontent.com"):
        raise SystemExit(
            f"GMAIL_CLIENT_ID looks wrong: {client_id!r}\n"
            "It should end in '.apps.googleusercontent.com'."
        )
    if not client_secret.startswith("GOCSPX-"):
        raise SystemExit(
            f"GMAIL_CLIENT_SECRET looks wrong (starts with {client_secret[:8]!r}).\n"
            "Google client secrets start with 'GOCSPX-'."
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
    try:
        creds = flow.run_local_server(
            port=0,
            access_type="offline",
            prompt="consent",
        )
    except InvalidClientError:
        raise SystemExit(CLIENT_SECRET_HELP)

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
