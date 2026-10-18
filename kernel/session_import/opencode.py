# -*- coding: utf-8 -*-
"""OpenCode: ``$XDG_DATA_HOME/opencode/opencode.db`` (SQLite).

Two schemas live in one database. The current one keeps a message per row in
``session_message`` (``user`` rows hold ``text``, ``assistant`` rows hold a
``content`` list of ``text``/``reasoning``/``tool`` blocks). The earlier one
keeps ``message`` rows whose ``part`` rows hold the same blocks. A session is
read from the current tables when it has rows there, otherwise from the earlier
ones. Child sessions (``parent_id`` set) belong to a sub-agent call.
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

from kernel.session_import.model import (
    ImportedSession,
    ImportedTurn,
    SessionSummary,
    Source,
    millis_to_epoch,
)

_FILE_TOOLS = frozenset({"edit", "write", "multiedit", "patch", "apply_patch"})
_SHELL_TOOLS = frozenset({"bash", "shell"})
_PATH_KEYS = ("filePath", "file_path", "path")


def _database(home: Path) -> Path:
    configured = os.environ.get("XDG_DATA_HOME")
    return (Path(configured) if configured else home / ".local" / "share") / "opencode" / "opencode.db"


def _connect(path: Path) -> sqlite3.Connection:
    # Read-only: OpenCode may be running and holds the write-ahead log.
    return sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)


def _tables(connection: sqlite3.Connection) -> set[str]:
    return {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def find(home: Path, _workspace: Path) -> list[SessionSummary]:
    path = _database(home)
    if not path.is_file():
        return []
    with closing(_connect(path)) as connection:
        rows = connection.execute(
            "SELECT id, title, directory, time_updated FROM session WHERE parent_id IS NULL"
        ).fetchall()
        if "session_v2" in _tables(connection):
            known = {row[0] for row in rows}
            rows += [
                row
                for row in connection.execute(
                    "SELECT id, title, directory, time_updated FROM session_v2 WHERE parent_id IS NULL"
                )
                if row[0] not in known
            ]
    return [
        SessionSummary("opencode", sid, str(title or ""), str(directory or ""), millis_to_epoch(updated), str(path))
        for sid, title, directory, updated in rows
    ]


def _absorb_blocks(turn: ImportedTurn, blocks: list[Any]) -> None:
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text" and str(block.get("text", "")).strip():
            turn.answer = block["text"].strip()
        elif block.get("type") == "tool":
            _absorb_tool(turn, block)
        elif block.get("type") == "patch":
            for path in block.get("files") or []:
                turn.note_file(path)


def _absorb_tool(turn: ImportedTurn, block: dict[str, Any]) -> None:
    name = str(block.get("name") or block.get("tool") or "").lower()
    state = block.get("state")
    arguments = state.get("input") if isinstance(state, dict) else None
    if not isinstance(arguments, dict):
        return
    if name in _FILE_TOOLS:
        for key in _PATH_KEYS:
            turn.note_file(arguments.get(key))
    elif name in _SHELL_TOOLS:
        turn.commands += 1


def _turns_v2(connection: sqlite3.Connection, session_id: str) -> list[ImportedTurn]:
    turns: list[ImportedTurn] = []
    for kind, raw in connection.execute(
        "SELECT type, data FROM session_message WHERE session_id = ? ORDER BY seq", (session_id,)
    ):
        data = json.loads(raw)
        if kind == "user" and str(data.get("text", "")).strip():
            created = (data.get("time") or {}).get("created")
            turns.append(ImportedTurn(data["text"].strip(), ts=millis_to_epoch(created)))
        elif kind == "assistant" and turns:
            _absorb_blocks(turns[-1], data.get("content") or [])
    return turns


def _turns_v1(connection: sqlite3.Connection, session_id: str) -> list[ImportedTurn]:
    turns: list[ImportedTurn] = []
    messages = connection.execute(
        "SELECT id, data FROM message WHERE session_id = ? ORDER BY time_created, id", (session_id,)
    ).fetchall()
    for message_id, raw in messages:
        role = json.loads(raw).get("role")
        parts = [
            json.loads(data)
            for (data,) in connection.execute(
                "SELECT data FROM part WHERE message_id = ? ORDER BY time_created, id", (message_id,)
            )
        ]
        if role == "user":
            text = "\n\n".join(p["text"].strip() for p in parts if p.get("type") == "text" and p.get("text"))
            if text.strip():
                created = (json.loads(raw).get("time") or {}).get("created")
                turns.append(ImportedTurn(text.strip(), ts=millis_to_epoch(created)))
        elif role == "assistant" and turns:
            _absorb_blocks(turns[-1], parts)
    return turns


def load(summary: SessionSummary) -> ImportedSession:
    with closing(_connect(Path(summary.location))) as connection:
        has_v2 = "session_message" in _tables(connection) and connection.execute(
            "SELECT 1 FROM session_message WHERE session_id = ? LIMIT 1", (summary.id,)
        ).fetchone()
        turns = _turns_v2(connection, summary.id) if has_v2 else _turns_v1(connection, summary.id)
    return ImportedSession(summary, turns)


SOURCE = Source("opencode", "OpenCode", "oc", find, load)
