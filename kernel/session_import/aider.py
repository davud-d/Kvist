# -*- coding: utf-8 -*-
"""Aider: ``.aider.chat.history.md`` in the project folder.

Markdown with one ``# aider chat started at <time>`` heading per run. Inside a
run the person's messages are lines starting ``#### ``, Aider's own status and
tool output are lines starting ``> `` and everything else is the model's
answer. Aider keeps no per-user index, so only the workspace is searched.
"""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from kernel.session_import.model import (
    ImportedSession,
    ImportedTurn,
    SessionSummary,
    Source,
    short_title,
)

HISTORY_FILE = ".aider.chat.history.md"
_RUN_HEADING = "# aider chat started at "
_USER_PREFIX = "#### "
_STATUS_PREFIX = "> "
_APPLIED_EDIT = "Applied edit to "


def _runs(text: str) -> dict[str, list[str]]:
    """Lines of each run, keyed by the run's start time (``YYYYMMDDHHMMSS``)."""
    runs: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.splitlines():
        if line.startswith(_RUN_HEADING):
            stamp = re.sub(r"\D", "", line[len(_RUN_HEADING):])
            current = runs.setdefault(stamp, [])
        elif current is not None:
            current.append(line)
    return runs


def _turns(lines: list[str], started: float) -> list[ImportedTurn]:
    turns: list[ImportedTurn] = []
    request: list[str] = []
    answer: list[str] = []
    in_request = False
    for line in lines:
        if line.startswith(_USER_PREFIX):
            if not in_request:
                _close_turn(turns, request, answer)
                request, answer, in_request = [], [], True
                turns.append(ImportedTurn("", ts=started))
            request.append(line[len(_USER_PREFIX):])
        elif line.startswith(_STATUS_PREFIX):
            in_request = False
            if turns and line[len(_STATUS_PREFIX):].startswith(_APPLIED_EDIT):
                turns[-1].note_file(line[len(_STATUS_PREFIX) + len(_APPLIED_EDIT):].strip())
        elif turns:
            in_request = False
            answer.append(line)
    _close_turn(turns, request, answer)
    return [turn for turn in turns if turn.user]


def _close_turn(turns: list[ImportedTurn], request: list[str], answer: list[str]) -> None:
    if turns:
        turns[-1].user = "\n".join(request).strip()
        turns[-1].answer = "\n".join(answer).strip()


def _started(stamp: str) -> float:
    try:
        return datetime.strptime(stamp, "%Y%m%d%H%M%S").timestamp()
    except ValueError:
        return 0.0


def find(_home: Path, workspace: Path) -> list[SessionSummary]:
    path = workspace / HISTORY_FILE
    if not path.is_file():
        return []
    found: list[SessionSummary] = []
    for stamp, lines in _runs(path.read_text(encoding="utf-8", errors="replace")).items():
        turns = _turns(lines, _started(stamp))
        if turns:
            found.append(SessionSummary("aider", stamp, short_title(turns[0].user), str(workspace), _started(stamp), str(path)))
    return found


def load(summary: SessionSummary) -> ImportedSession:
    lines = _runs(Path(summary.location).read_text(encoding="utf-8", errors="replace")).get(summary.id, [])
    return ImportedSession(summary, _turns(lines, _started(summary.id)))


SOURCE = Source("aider", "Aider", "ai", find, load)
