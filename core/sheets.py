"""Low-level Google Sheets access, shared by the tracker and the state store.

The google-api-python-client is synchronous, and the rest of the pipeline is
asyncio, so every call here is pushed to a worker thread. A blocking HTTP call
on the event loop would stall the concurrent agents running alongside it.

One spreadsheet holds everything: an Applications tab that the user reads, and
a State tab holding the Gmail cursor. GitHub Actions gives the pipeline no
persistent disk, so the cursor has to live somewhere durable, and a second tab
is cheaper than a second service.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import Any

import google.auth
import google.auth.exceptions
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

from core.config import Settings, require_setting

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

APPLICATIONS_TAB = "Applications"
STATE_TAB = "State"

_service: Any = None


def _ambient_credentials():
    """Credentials from the runtime itself, with no key material anywhere.

    On Cloud Run the service runs *as* a service account, so the platform can
    mint tokens for it directly. Shipping a downloaded key there would mean
    putting a permanent credential into a public web service to obtain access
    it already has — and a key in an environment variable is a credential that
    can leak, be committed, or outlive its usefulness. A key file is still the
    right answer locally, where there is no ambient identity to borrow.
    """
    credentials, _ = google.auth.default(scopes=SCOPES)
    return credentials


def _load_key(settings: Settings) -> dict:
    """Read the service-account key from a file path or an inline JSON string.

    The file path is preferred locally. A key downloaded from Google Cloud is
    pretty-printed across ~12 lines, and .env is line-based, so pasting it in
    unflattened leaves the variable holding just "{" — which fails later, at
    the first API call, with a JSON error that says nothing about .env.
    """
    path = settings.google_service_account_file
    if path:
        key_path = Path(path).expanduser()
        if not key_path.is_file():
            raise RuntimeError(
                f"GOOGLE_SERVICE_ACCOUNT_FILE points to {key_path}, which does "
                "not exist. Use the path to the .json key you downloaded from "
                "Google Cloud."
            )
        try:
            return json.loads(key_path.read_text())
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{key_path} is not valid JSON: {exc}") from exc

    raw = require_setting(
        settings.google_service_account_json,
        "GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON",
    )
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        # Name the actual mistake. "Invalid JSON" sends people hunting for a
        # typo in a file that is usually fine; the value simply got truncated.
        truncated = raw.strip() in {"{", "{{"} or len(raw.strip()) < 40
        hint = (
            "The value looks truncated — .env is line-based, so a "
            "pretty-printed key ends up as just its first line.\n"
            if truncated
            else ""
        )
        raise RuntimeError(
            f"GOOGLE_SERVICE_ACCOUNT_JSON is not valid JSON (got "
            f"{raw.strip()[:40]!r}...).\n{hint}"
            "Easiest fix — point at the file instead, and delete the inline "
            "variable:\n"
            "    GOOGLE_SERVICE_ACCOUNT_FILE=/absolute/path/to/key.json\n"
            "Or flatten it to one line:\n"
            "    python -c \"import json,sys;print(json.dumps(json.load(open(sys.argv[1]))))\" key.json"
        ) from exc


def _build_service(settings: Settings) -> Any:
    global _service
    if _service is not None:
        return _service

    if settings.google_service_account_file or settings.google_service_account_json:
        creds = Credentials.from_service_account_info(_load_key(settings), scopes=SCOPES)
    else:
        try:
            creds = _ambient_credentials()
            logger.info("Using the runtime's own service account for Sheets")
        except google.auth.exceptions.DefaultCredentialsError as exc:
            raise RuntimeError(
                "No Google credentials. Locally, set GOOGLE_SERVICE_ACCOUNT_FILE "
                "to the downloaded key. On Cloud Run, deploy with "
                "--service-account so the service has an identity of its own."
            ) from exc
    _service = build("sheets", "v4", credentials=creds, cache_discovery=False)
    return _service


def _spreadsheet_id(settings: Settings) -> str:
    return require_setting(settings.tracker_spreadsheet_id, "TRACKER_SPREADSHEET_ID")


async def _run(fn, *args, **kwargs):
    """Execute a blocking Sheets call off the event loop."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def ensure_tabs(settings: Settings, headers: dict[str, list[str]]) -> None:
    """Create any missing tab and write its header row.

    Called once per run rather than per write. Doing it lazily means a fresh
    spreadsheet works with no manual setup beyond sharing it with the service
    account — the failure mode we are avoiding is a first run that dies on a
    404 for a tab the user was never told to create.
    """
    service = _build_service(settings)
    sheet_id = _spreadsheet_id(settings)

    def _existing() -> set[str]:
        meta = service.spreadsheets().get(spreadsheetId=sheet_id).execute()
        return {s["properties"]["title"] for s in meta.get("sheets", [])}

    try:
        existing = await _run(_existing)
    except Exception as exc:
        raise RuntimeError(
            f"Cannot open spreadsheet {sheet_id!r}. Check TRACKER_SPREADSHEET_ID "
            "is the id from the sheet URL, and that the sheet is shared with "
            "the service account's client_email as an Editor."
        ) from exc

    missing = [title for title in headers if title not in existing]
    if missing:
        def _add() -> None:
            service.spreadsheets().batchUpdate(
                spreadsheetId=sheet_id,
                body={
                    "requests": [
                        {"addSheet": {"properties": {"title": title}}}
                        for title in missing
                    ]
                },
            ).execute()

        await _run(_add)
        logger.info("Created tab(s): %s", ", ".join(missing))

    # A tab can exist with no header if someone cleared it, so check content
    # rather than assuming creation and headers happen together.
    for title, header in headers.items():
        rows = await get_values(settings, f"{title}!A1:Z1")
        existing_header = [cell.strip() for cell in rows[0]] if rows else []

        if not any(existing_header):
            await update_values(settings, f"{title}!A1", [header])
            logger.info("Wrote header row for %s", title)
            continue

        if existing_header == header:
            continue

        # The schema gained a column. Rewriting row 1 keeps new writes correct,
        # but any pre-existing data rows were written against the old column
        # order and are now misaligned from the insertion point onward. That
        # cannot be repaired safely from here without guessing, so say so
        # loudly rather than silently producing a scrambled sheet.
        data = await get_values(settings, f"{title}!A2:A")
        await update_values(settings, f"{title}!A1", [header])
        logger.warning(
            "Header for %r changed from %s to %s.", title, existing_header, header
        )
        if any(cell for row in data for cell in row):
            logger.warning(
                "%r already holds %d data row(s) written against the old "
                "columns. Values from the changed column onward will be "
                "shifted. Delete those rows and re-run, or realign them by "
                "hand.",
                title, len(data),
            )


async def get_values(settings: Settings, range_: str) -> list[list[str]]:
    service = _build_service(settings)
    sheet_id = _spreadsheet_id(settings)

    def _get() -> list[list[str]]:
        result = (
            service.spreadsheets()
            .values()
            .get(spreadsheetId=sheet_id, range=range_)
            .execute()
        )
        return result.get("values", [])

    return await _run(_get)


async def update_values(settings: Settings, range_: str, rows: list[list[Any]]) -> None:
    service = _build_service(settings)
    sheet_id = _spreadsheet_id(settings)

    def _update() -> None:
        service.spreadsheets().values().update(
            spreadsheetId=sheet_id,
            range=range_,
            # RAW, not USER_ENTERED: a role like "-" or a company starting with
            # "=" would otherwise be parsed as a formula.
            valueInputOption="RAW",
            body={"values": rows},
        ).execute()

    await _run(_update)


async def append_values(settings: Settings, tab: str, rows: list[list[Any]]) -> None:
    service = _build_service(settings)
    sheet_id = _spreadsheet_id(settings)

    def _append() -> None:
        service.spreadsheets().values().append(
            spreadsheetId=sheet_id,
            range=f"{tab}!A1",
            valueInputOption="RAW",
            insertDataOption="INSERT_ROWS",
            body={"values": rows},
        ).execute()

    await _run(_append)
