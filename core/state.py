"""Cursor persistence between pipeline runs.

File-backed for now, which is enough for local Stage 2 testing but does NOT
survive a GitHub Actions run (fresh container each time). At Stage 5, when
the Google Sheets tracker exists, this moves to a small state tab in that
same spreadsheet so both halves of the system share one durable store.
"""

from __future__ import annotations

import json
from pathlib import Path

STATE_PATH = Path(".pipeline_state.json")


def read_cursor(key: str) -> str | None:
    if not STATE_PATH.exists():
        return None
    try:
        return json.loads(STATE_PATH.read_text()).get(key)
    except (json.JSONDecodeError, OSError):
        return None


def write_cursor(key: str, value: str) -> None:
    data = {}
    if STATE_PATH.exists():
        try:
            data = json.loads(STATE_PATH.read_text())
        except (json.JSONDecodeError, OSError):
            data = {}
    data[key] = value
    STATE_PATH.write_text(json.dumps(data, indent=2))
