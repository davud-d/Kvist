# -*- coding: utf-8 -*-
"""Codex CLI: ``$CODEX_HOME/sessions/YYYY/MM/DD/rollout-*-<id>.jsonl``.

Each line is ``{"type", "payload"}``. Current versions record the dialogue as
``event_msg`` ``item_completed`` items (``UserMessage``, ``AgentMessage`` with a
``phase``, ``CommandExecution``, ``FileChange``). Older versions have only the
model-facing ``response_item`` messages, and the oldest write those items
without the ``payload`` wrapper; there the user's request is the first
``user`` message that is not injected context.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from kernel.session_import.model import (
    ImportedSession,
    ImportedTurn,
    SessionSummary,
    Source,
    head_jsonl,
    iso_to_epoch,
    patch_paths,
    read_jsonl,
    short_title,
)

_ITEM_TYPES = frozenset({"message", "function_call", "custom_tool_call", "local_shell_call"})
_SHELL_CALLS = frozenset({"shell", "exec_command", "container.exec", "local_shell_call"})
_INJECTED_PREFIXES = ("<", "# AGENTS.md instructions", "# Context from my IDE")


def _root(home: Path) -> Path:
    configured = os.environ.get("CODEX_HOME")
    return (Path(configured) if configured else home / ".codex") / "sessions"


def _text_of(parts: object) -> str:
    if not isinstance(parts, list):
        return ""
    return "".join(
        part["text"] for part in parts if isinstance(part, dict) and isinstance(part.get("text"), str)
    ).strip()


def _item_completed(record: dict[str, Any]) -> dict[str, Any] | None:
    payload = record.get("payload")
    if record.get("type") != "event_msg" or not isinstance(payload, dict):
        return None
    if payload.get("type") != "item_completed" or not isinstance(payload.get("item"), dict):
        return None
    return payload["item"]


def _response_item(record: dict[str, Any]) -> dict[str, Any] | None:
    payload = record.get("payload")
    if record.get("type") == "response_item" and isinstance(payload, dict):
        return payload
    if record.get("type") in _ITEM_TYPES:
        return record
    return None


def _meta(records: list[dict[str, Any]]) -> dict[str, Any]:
    for record in records:
        if record.get("type") == "session_meta" and isinstance(record.get("payload"), dict):
            return record["payload"]
    return {}


def _first_request(records: list[dict[str, Any]]) -> str:
    for record in records:
        item = _item_completed(record)
        if item and item.get("type") == "UserMessage":
            return _text_of(item.get("content"))
        message = _response_item(record)
        if message and message.get("type") == "message" and message.get("role") == "user":
            text = _text_of(message.get("content"))
            if text and not text.startswith(_INJECTED_PREFIXES):
                return text
    return ""


def find(home: Path, _workspace: Path) -> list[SessionSummary]:
    found: list[SessionSummary] = []
    for path in _root(home).glob("*/*/*/rollout-*.jsonl"):
        head = head_jsonl(path, max_bytes=262144)
        meta = _meta(head)
        title = _first_request(head)
        if not title:
            continue
        identifier = str(meta.get("id") or path.stem)
        found.append(
            SessionSummary(
                "codex", identifier, short_title(title), str(meta.get("cwd") or ""), path.stat().st_mtime, str(path)
            )
        )
    return found


def _start_turn(turns: list[ImportedTurn], text: str, record: dict[str, Any]) -> None:
    turns.append(ImportedTurn(text, ts=iso_to_epoch(record.get("timestamp"))))


def _load_items(records: list[dict[str, Any]]) -> list[ImportedTurn]:
    """Turns from ``item_completed`` events (Codex 0.1xx and newer)."""
    turns: list[ImportedTurn] = []
    for record in records:
        payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
        if record.get("type") == "event_msg" and payload.get("type") == "turn_aborted" and turns:
            turns[-1].stopped = True
        item = _item_completed(record)
        if item is None:
            continue
        kind = item.get("type")
        if kind == "UserMessage":
            _start_turn(turns, _text_of(item.get("content")), record)
        elif not turns:
            continue
        elif kind == "AgentMessage" and item.get("phase") != "commentary":
            turns[-1].answer = _text_of(item.get("content")) or turns[-1].answer
        elif kind == "CommandExecution":
            turns[-1].commands += 1
        elif kind == "FileChange" and isinstance(item.get("changes"), dict):
            for path in item["changes"]:
                turns[-1].note_file(path)
    return turns


def _load_responses(records: list[dict[str, Any]]) -> list[ImportedTurn]:
    """Turns from the model-facing ``response_item`` stream (older Codex)."""
    turns: list[ImportedTurn] = []
    for record in records:
        item = _response_item(record)
        if item is None:
            continue
        if item.get("type") == "message":
            _absorb_message(turns, item, record)
        elif turns:
            _absorb_call(turns[-1], item)
    return turns


def _absorb_message(turns: list[ImportedTurn], item: dict[str, Any], record: dict[str, Any]) -> None:
    text = _text_of(item.get("content"))
    if not text:
        return
    if item.get("role") == "user" and not text.startswith(_INJECTED_PREFIXES):
        _start_turn(turns, text, record)
    elif item.get("role") == "assistant" and turns:
        turns[-1].answer = text


def _absorb_call(turn: ImportedTurn, item: dict[str, Any]) -> None:
    name = item.get("name") or item.get("type")
    arguments = str(item.get("arguments") or item.get("input") or "")
    if name == "apply_patch" or "*** Begin Patch" in arguments:
        for path in patch_paths(arguments):
            turn.note_file(path)
    elif name in _SHELL_CALLS:
        turn.commands += 1


def load(summary: SessionSummary) -> ImportedSession:
    records = read_jsonl(Path(summary.location))
    has_items = any(_item_completed(record) for record in records)
    turns = _load_items(records) if has_items else _load_responses(records)
    return ImportedSession(summary, [turn for turn in turns if turn.user])


SOURCE = Source("codex", "Codex", "cx", find, load)
