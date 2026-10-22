# -*- coding: utf-8 -*-
"""Turn an imported conversation into a Kvist session.

The session is built the way Kvist itself would have left it after those
turns, so ``/session`` can open it and the next request continues it:

* the event journal gets every turn (what a frontend redraws);
* ``session_history`` and ``chat_history`` get the last ``HISTORY_TURNS`` turns,
  the same cap the running agent keeps, because those two reach the model;
* the message ledger numbers every request and reply of the conversation.

Tool calls of the other agent are reduced to the files it changed and the
number of commands it ran: that is a fact about the past, and nothing of it is
executed or replayed here.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from kernel.session.agent_logging import create_session, delete_session, get_journal_path
from kernel.session.durable_json import JsonStorageError
from kernel.context.language_policy import detect_response_language
from kernel.sandbox.redaction import redact_text, redact_value
from kernel.session.session_events import write_imported_turns
from kernel.session_import.model import ImportedSession, ImportedTurn, Source

# Matches the cap ``LocalAgent._add_to_session_history`` applies.
HISTORY_TURNS = 10
_NAME_TAIL_CHARS = 16

_NO_ANSWER = "[No answer was recorded in the imported session.]"
_STOPPED_ANSWER = "[The user stopped this reply before it finished.]"


@dataclass(frozen=True, slots=True)
class ImportResult:
    name: str
    turns: int
    turns_in_context: int


def session_name(source: Source, source_id: str) -> str:
    """The Kvist session a source session imports to; the same input gives the same name."""
    tail = re.sub(r"[^a-z0-9]", "", source_id.lower())[-_NAME_TAIL_CHARS:] or "session"
    return f"{source.prefix}-{tail}"


def _trail(source: Source, turn: ImportedTurn) -> str:
    facts: list[str] = []
    if turn.files_changed:
        facts.append(f"changed {len(turn.files_changed)} files: {', '.join(turn.files_changed)}")
    if turn.commands:
        facts.append(f"ran {turn.commands} shell commands")
    return f"Imported from {source.label}; the agent {' and '.join(facts)}." if facts else ""


def _history_entry(source: Source, turn: ImportedTurn, request_id: int) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "goal": turn.user,
        "role": "coder" if turn.files_changed or turn.commands else "chat",
        "result": turn.answer,
        "reply_id": request_id + 1,
        "msg_id": request_id,
    }
    trail = _trail(source, turn)
    if trail:
        entry["outputs"] = [trail]
    if turn.stopped:
        entry["stopped_by_user"] = True
    return entry


def _chat_messages(turn: ImportedTurn) -> list[dict[str, str]]:
    answer = turn.answer or (_STOPPED_ANSWER if turn.stopped else _NO_ANSWER)
    return [{"role": "user", "content": turn.user}, {"role": "assistant", "content": answer}]


def _journal_events(turn: ImportedTurn) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = [{"type": "user", "text": redact_text(turn.user)}]
    if turn.answer:
        events.append(
            {"type": "final", "message": redact_text(turn.answer), "user_messages": [], "outcome": "done"}
        )
    end: dict[str, Any] = {"type": "turn_end"}
    if turn.stopped:
        end["stopped_by_user"] = True
    events.append(end)
    return events


def _document_fields(source: Source, imported: ImportedSession, turns: list[ImportedTurn]) -> dict[str, Any]:
    first_in_context = len(turns) - min(len(turns), HISTORY_TURNS)
    recent = turns[first_in_context:]
    chat = [{"role": "system", "content": ""}]
    for turn in recent:
        chat.extend(_chat_messages(turn))
    summary = imported.summary
    fields: dict[str, Any] = {
        "updated_at": summary.updated_at,
        "session_history": [
            _history_entry(source, turn, 2 * (first_in_context + index) + 1)
            for index, turn in enumerate(recent)
        ],
        "chat_history": chat,
        "response_language": detect_response_language(turns[-1].user),
        "title": f"{source.label}: {summary.title}" if summary.title else source.label,
        "messages": {"last_id": 2 * len(turns), "user_ids": list(range(1, 2 * len(turns), 2))},
    }
    redacted = redact_value(fields)
    assert isinstance(redacted, dict)
    return redacted


def import_session(host: Any, source: Source, imported: ImportedSession) -> ImportResult | None:
    """Create the session; None when this conversation was imported before.

    ``host`` is what ``agent_logging`` takes: it has ``executor.workspace``.
    A journal that cannot be written removes the half-made session, so a
    failed import can be repeated.
    """
    turns = [turn for turn in imported.turns if turn.user]
    if not turns:
        raise ValueError("The conversation has no requests to import")
    name = session_name(source, imported.summary.id)
    if not create_session(host, name, _document_fields(source, imported, turns)):
        return None
    workspace = host.executor.workspace
    stamped = [(turn.ts or imported.summary.updated_at, _journal_events(turn)) for turn in turns]
    try:
        write_imported_turns(get_journal_path(host, name), workspace, stamped)
    except (JsonStorageError, OSError):
        delete_session(host, name)
        raise
    return ImportResult(name, len(turns), min(len(turns), HISTORY_TURNS))
