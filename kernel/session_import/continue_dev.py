# -*- coding: utf-8 -*-
"""Continue: ``$CONTINUE_GLOBAL_DIR/sessions/<session id>.json``.

One JSON document per chat: ``history`` is a list of ``{message}`` items whose
``role`` is ``user``, ``assistant``, ``tool`` or ``system``. ``content`` is
a string or a list of ``{type: "text", text}`` parts. ``sessions.json`` is an
index of the same chats and is not read.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from kernel.session_import.model import (
    ImportedSession,
    ImportedTurn,
    SessionSummary,
    Source,
    file_uri_path,
    short_title,
)

_FILE_TOOLS = frozenset({"edit_existing_file", "create_new_file", "single_find_and_replace"})
_SHELL_TOOLS = frozenset({"run_terminal_command"})


def _sessions_dir(home: Path) -> Path:
    configured = os.environ.get("CONTINUE_GLOBAL_DIR")
    return (Path(configured) if configured else home / ".continue") / "sessions"


def _text(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n\n".join(
        part["text"].strip()
        for part in content
        if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
    ).strip()


def _document(path: Path) -> dict[str, Any]:
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return document if isinstance(document, dict) and isinstance(document.get("history"), list) else {}


def find(home: Path, _workspace: Path) -> list[SessionSummary]:
    found: list[SessionSummary] = []
    for path in _sessions_dir(home).glob("*.json"):
        document = _document(path)
        if not any(_text((item.get("message") or {}).get("content")) for item in document.get("history", [])):
            continue
        folder = file_uri_path(str(document.get("workspaceDirectory") or ""))
        title = str(document.get("title") or "") or short_title(path.stem)
        found.append(SessionSummary("continue", str(document.get("sessionId") or path.stem), title, folder, path.stat().st_mtime, str(path)))
    return found


def _absorb_calls(turn: ImportedTurn, calls: object) -> None:
    for call in calls if isinstance(calls, list) else []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            continue
        if function.get("name") in _FILE_TOOLS:
            turn.note_file(_filepath(function.get("arguments")))
        elif function.get("name") in _SHELL_TOOLS:
            turn.commands += 1


def _filepath(arguments: object) -> object:
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except ValueError:
            return None
    return arguments.get("filepath") if isinstance(arguments, dict) else None


def load(summary: SessionSummary) -> ImportedSession:
    turns: list[ImportedTurn] = []
    for item in _document(Path(summary.location)).get("history", []):
        message = item.get("message") if isinstance(item, dict) else None
        if not isinstance(message, dict):
            continue
        text = _text(message.get("content"))
        if message.get("role") == "user" and text:
            turns.append(ImportedTurn(text))
        elif message.get("role") == "assistant" and turns:
            turns[-1].answer = text or turns[-1].answer
            _absorb_calls(turns[-1], message.get("toolCalls"))
    return ImportedSession(summary, turns)


SOURCE = Source("continue", "Continue", "ct", find, load)
