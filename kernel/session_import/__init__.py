# -*- coding: utf-8 -*-
"""Import conversations from other coding agents as Kvist sessions (``/export``).

Each module here reads one agent's on-disk format into the neutral
``ImportedSession``; ``writer`` turns that into a Kvist session. Reading never
modifies the other agent's files.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

from kernel.session_import import (
    aider,
    claude_code,
    codex,
    continue_dev,
    cursor,
    opencode,
    qwen_code,
)
from kernel.session_import.model import SessionSummary, Source, is_inside

SOURCES: tuple[Source, ...] = (
    claude_code.SOURCE,
    codex.SOURCE,
    opencode.SOURCE,
    cursor.SOURCE,
    qwen_code.SOURCE,
    continue_dev.SOURCE,
    aider.SOURCE,
)


class SourceUnreadable(Exception):
    """An agent's store exists but could not be read (locked, damaged, unknown layout)."""


def source_by_key(key: str) -> Source | None:
    return next((source for source in SOURCES if source.key == key.casefold()), None)


def find_sessions(source: Source, home: Path, workspace: Path) -> list[SessionSummary]:
    """Sessions of one agent, newest first; an unreadable store counts as having none."""
    try:
        found = source.find(home, workspace)
    except (OSError, sqlite3.Error, ValueError) as error:
        raise SourceUnreadable(f"{source.label}: {error}") from error
    return sorted(found, key=lambda summary: summary.updated_at, reverse=True)


def here(summaries: list[SessionSummary], workspace: Path) -> list[SessionSummary]:
    """The sessions that ran in the workspace or a folder below it."""
    return [summary for summary in summaries if is_inside(summary.cwd, workspace)]
