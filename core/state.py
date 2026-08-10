"""Cursor persistence between pipeline runs.

Two backends, chosen by whether a spreadsheet is configured:

- Google Sheets (a State tab in the tracker spreadsheet) when
  TRACKER_SPREADSHEET_ID is set. GitHub Actions gives each run a fresh
  container, so a file-backed cursor would be lost every time and the
  pipeline would re-read the same mail forever.
- A local JSON file otherwise, so the Stage 2/3 scripts still work before
  any Google Sheets credentials exist.

Callers do not choose; they pass settings and get whichever is available.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from core import sheets
from core.config import Settings

logger = logging.getLogger(__name__)

STATE_PATH = Path(".pipeline_state.json")


def _use_sheet(settings: Settings) -> bool:
    has_key = bool(
        settings.google_service_account_file or settings.google_service_account_json
    )
    return bool(settings.tracker_spreadsheet_id and has_key)


def _read_file(key: str) -> str | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text()).get(key)
    except (json.JSONDecodeError, OSError):
        return None


def _write_file(key: str, value: str) -> None:
    data = {}
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    data[key] = value
    STATE_PATH.write_text(json.dumps(data, indent=2))


async def read_cursor(settings: Settings, key: str) -> str | None:
    if not _use_sheet(settings):
        return _read_file(key)

    rows = await sheets.get_values(settings, f"{sheets.STATE_TAB}!A2:B")
    for row in rows:
        if row and row[0].strip() == key:
            return row[1].strip() if len(row) > 1 and row[1].strip() else None
    return None


async def write_cursor(settings: Settings, key: str, value: str) -> None:
    if not _use_sheet(settings):
        _write_file(key, value)
        return

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = await sheets.get_values(settings, f"{sheets.STATE_TAB}!A2:B")
    for offset, row in enumerate(rows):
        if row and row[0].strip() == key:
            sheet_row = offset + 2
            await sheets.update_values(
                settings, f"{sheets.STATE_TAB}!A{sheet_row}:C{sheet_row}",
                [[key, value, now]],
            )
            return

    await sheets.append_values(settings, sheets.STATE_TAB, [[key, value, now]])
