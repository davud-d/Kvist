# -*- coding: utf-8 -*-
"""The last inputs of a workspace, kept across restarts for the Up arrow."""
from __future__ import annotations

from pathlib import Path

from durable_json import JsonStorageError, read_json_object, update_json_object

MAX_ENTRIES = 100
# One paste must not turn the file into a megabyte-sized document.
MAX_ENTRY_CHARS = 20_000


class InputHistoryStore:
    """``<workspace>/.kvist/input_history.json``: ``{"entries": [oldest, ..., newest]}``."""

    def __init__(self, workspace: str | Path) -> None:
        self._boundary = Path(workspace).expanduser().resolve(strict=True)
        self._path = self._boundary / ".kvist" / "input_history.json"

    def entries(self) -> list[str]:
        """Oldest first; a missing or unreadable file is an empty history."""
        try:
            data = read_json_object(self._path, boundary=self._boundary)
        except (JsonStorageError, OSError):
            # The history is a convenience: a damaged file must not stop the UI.
            return []
        stored = data.get("entries")
        if not isinstance(stored, list):
            return []
        return [entry for entry in stored if isinstance(entry, str)][-MAX_ENTRIES:]

    def add(self, text: str) -> None:
        """Append one input; blank text, over-long text and a repeat of the last entry are skipped."""
        if not text.strip() or len(text) > MAX_ENTRY_CHARS:
            return

        def append(data: dict) -> dict:
            stored = data.get("entries")
            entries = [e for e in stored if isinstance(e, str)] if isinstance(stored, list) else []
            if not entries or entries[-1] != text:
                entries.append(text)
            return {"entries": entries[-MAX_ENTRIES:]}

        try:
            update_json_object(
                self._path, append, boundary=self._boundary, backup_existing=False
            )
        except (JsonStorageError, OSError):
            # Losing one history entry is harmless next to failing a turn.
            return

