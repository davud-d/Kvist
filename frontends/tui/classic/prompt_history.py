# -*- coding: utf-8 -*-
"""prompt_toolkit adapter for the workspace input history."""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from prompt_toolkit.history import History

from kernel.input_history import InputHistoryStore

class WorkspaceInputHistory(History):
    """prompt_toolkit history backed by :class:`~input_history.InputHistoryStore`."""

    def __init__(self, workspace: str | Path) -> None:
        super().__init__()
        self._store = InputHistoryStore(workspace)

    def load_history_strings(self) -> Iterable[str]:
        # prompt_toolkit expects the newest entry first.
        return reversed(self._store.entries())

    def store_string(self, string: str) -> None:
        self._store.add(string)
