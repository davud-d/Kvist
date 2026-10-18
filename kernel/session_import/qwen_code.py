# -*- coding: utf-8 -*-
"""Qwen Code: ``~/.qwen/projects/<folder>/chats/<session id>.jsonl``.

A fork of Gemini CLI that records one JSON object per line. ``user`` and
``assistant`` records carry Gemini-style ``message.parts``; tool results are
separate records and are not requests. ``thought`` parts are the model's
reasoning and are not part of the answer.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from kernel.session_import.model import (
    ImportedSession,
    ImportedTurn,
    SessionSummary,
    Source,
    head_jsonl,
    iso_to_epoch,
    read_jsonl,
    short_title,
)

_FILE_TOOLS = frozenset({"write_file", "edit", "replace"})
_SHELL_TOOLS = frozenset({"run_shell_command"})
_PATH_KEYS = ("file_path", "absolute_path", "path")


def _parts(record: dict[str, Any]) -> list[dict[str, Any]]:
    message = record.get("message")
    parts = message.get("parts") if isinstance(message, dict) else None
    return [part for part in parts if isinstance(part, dict)] if isinstance(parts, list) else []


def _request_text(record: dict[str, Any]) -> str:
    parts = _parts(record)
    if any("functionResponse" in part for part in parts):
        return ""
    return "\n\n".join(
        part["text"].strip() for part in parts if isinstance(part.get("text"), str) and part["text"].strip()
    )


def find(home: Path, _workspace: Path) -> list[SessionSummary]:
    found: list[SessionSummary] = []
    for path in (home / ".qwen" / "projects").glob("*/chats/*.jsonl"):
        head = head_jsonl(path)
        title = next((_request_text(r) for r in head if r.get("type") == "user" and _request_text(r)), "")
        cwd = next((r["cwd"] for r in head if isinstance(r.get("cwd"), str)), "")
        if title:
            found.append(SessionSummary("qwen-code", path.stem, short_title(title), cwd, path.stat().st_mtime, str(path)))
    return found


def _absorb_assistant(turn: ImportedTurn, record: dict[str, Any]) -> None:
    text = "".join(
        part["text"] for part in _parts(record) if isinstance(part.get("text"), str) and not part.get("thought")
    ).strip()
    if text:
        turn.answer = text
    for part in _parts(record):
        call = part.get("functionCall")
        if isinstance(call, dict):
            _absorb_call(turn, call)


def _absorb_call(turn: ImportedTurn, call: dict[str, Any]) -> None:
    arguments = call.get("args") if isinstance(call.get("args"), dict) else {}
    if call.get("name") in _FILE_TOOLS:
        for key in _PATH_KEYS:
            turn.note_file(arguments.get(key))
    elif call.get("name") in _SHELL_TOOLS:
        turn.commands += 1


def load(summary: SessionSummary) -> ImportedSession:
    turns: list[ImportedTurn] = []
    for record in read_jsonl(Path(summary.location)):
        if record.get("type") == "assistant" and turns:
            _absorb_assistant(turns[-1], record)
        elif record.get("type") == "user" and _request_text(record):
            turns.append(ImportedTurn(_request_text(record), ts=iso_to_epoch(record.get("timestamp"))))
    return ImportedSession(summary, turns)


SOURCE = Source("qwen-code", "Qwen Code", "qw", find, load)
