# -*- coding: utf-8 -*-
"""Cursor: ``<user data>/Cursor/User/globalStorage/state.vscdb`` (SQLite).

``cursorDiskKV`` holds one ``composerData:<id>`` row per chat, whose
``fullConversationHeadersOnly`` lists ``{bubbleId, type}`` in order, and one
``bubbleId:<id>:<bubbleId>`` row per message (``type`` 1 is the person, 2 the
agent). The folder a chat belongs to is not in the chat: ``composerHeaders``
names a workspace id, and ``workspaceStorage/<id>/workspace.json`` its folder.
Only the message text is read; Cursor's tool-call records are not.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
from contextlib import closing
from pathlib import Path
from urllib.parse import unquote

from kernel.session_import.model import (
    ImportedSession,
    ImportedTurn,
    SessionSummary,
    Source,
    file_uri_path,
    millis_to_epoch,
    short_title,
)

_USER_BUBBLE = 1
_AGENT_BUBBLE = 2


def _user_dir(home: Path) -> Path:
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / "Cursor" / "User"
    if sys.platform == "win32":
        return Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Cursor" / "User"
    return Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "Cursor" / "User"


def _connect(path: Path) -> sqlite3.Connection:
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def _workspace_folders(user_dir: Path) -> dict[str, str]:
    folders: dict[str, str] = {}
    for descriptor in (user_dir / "workspaceStorage").glob("*/workspace.json"):
        try:
            folder = json.loads(descriptor.read_text(encoding="utf-8")).get("folder")
        except (OSError, ValueError):
            continue
        if isinstance(folder, str):
            folders[descriptor.parent.name] = unquote(file_uri_path(folder))
    return folders


def _bubbles(connection: sqlite3.Connection, composer: dict, composer_id: str) -> list[dict]:
    inline = composer.get("conversation")
    if isinstance(inline, list) and inline:
        return [bubble for bubble in inline if isinstance(bubble, dict)]
    bubbles: list[dict] = []
    for header in composer.get("fullConversationHeadersOnly") or []:
        row = connection.execute(
            "SELECT value FROM cursorDiskKV WHERE key = ?", (f"bubbleId:{composer_id}:{header.get('bubbleId')}",)
        ).fetchone()
        if row:
            bubbles.append(json.loads(row[0]))
    return bubbles


def _turns(bubbles: list[dict]) -> list[ImportedTurn]:
    turns: list[ImportedTurn] = []
    for bubble in bubbles:
        text = str(bubble.get("text") or "").strip()
        if bubble.get("type") == _USER_BUBBLE and text:
            turns.append(ImportedTurn(text))
        elif bubble.get("type") == _AGENT_BUBBLE and text and turns:
            turns[-1].answer = text
    return turns


def find(home: Path, _workspace: Path) -> list[SessionSummary]:
    user_dir = _user_dir(home)
    database = user_dir / "globalStorage" / "state.vscdb"
    if not database.is_file():
        return []
    folders = _workspace_folders(user_dir)
    found: list[SessionSummary] = []
    with closing(_connect(database)) as connection:
        rows = connection.execute("SELECT key, value FROM cursorDiskKV WHERE key LIKE 'composerData:%'").fetchall()
        workspaces = dict(connection.execute("SELECT composerId, workspaceId FROM composerHeaders"))
        for key, raw in rows:
            composer_id = key.split(":", 1)[1]
            composer = json.loads(raw)
            turns = _turns(_bubbles(connection, composer, composer_id))
            if not turns:
                continue
            updated = millis_to_epoch(composer.get("lastUpdatedAt") or composer.get("createdAt"))
            title = str(composer.get("name") or "") or short_title(turns[0].user)
            cwd = folders.get(workspaces.get(composer_id, ""), "")
            found.append(SessionSummary("cursor", composer_id, title, cwd, updated, str(database)))
    return found


def load(summary: SessionSummary) -> ImportedSession:
    with closing(_connect(Path(summary.location))) as connection:
        row = connection.execute("SELECT value FROM cursorDiskKV WHERE key = ?", (f"composerData:{summary.id}",)).fetchone()
        composer = json.loads(row[0]) if row else {}
        turns = _turns(_bubbles(connection, composer, summary.id))
    return ImportedSession(summary, turns)


SOURCE = Source("cursor", "Cursor", "cu", find, load)
