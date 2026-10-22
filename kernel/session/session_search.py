# -*- coding: utf-8 -*-
"""Earlier conversations of one workspace: list, read in pages, search with exact references.

Built on ``session_store`` and the session event journals, read-only: reading
another session never switches the person's active conversation and never
touches the active-session marker. Results are redacted and every hit names
its session and journal sequence number, so an answer can cite where a
decision was made ("session chat-…, event 42").
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from kernel.session.agent_logging import get_journal_path, is_valid_session_name, read_session_document
from kernel.session.durable_json import JsonStorageError
from kernel.sandbox.redaction import redact_text
from kernel.session.session_events import read_history
from kernel.session.session_store import SessionStore

_TEXT_KEYS = ("text", "message", "markdown", "user_message", "result", "summary", "plan", "reason", "content")
_EVENT_TYPES = frozenset({"user", "final", "message", "stream_commit", "step", "plan", "error", "action_result"})
MAX_EXCERPT = 400
MAX_READ_LIMIT = 200
_SCAN_EVENTS_PER_SESSION = 5000


class SessionSearchError(ValueError):
    pass


def list_sessions(workspace: Path, *, include_service: bool = True) -> list[dict[str, Any]]:
    items = SessionStore(workspace).listing()["session_items"]
    rows = []
    for item in items:
        if not include_service and item.get("service"):
            continue
        rows.append({
            "session": item["name"],
            "title": item.get("title") or item["name"],
            "updated_at": item.get("updated_at"),
            "has_checkpoint": item.get("has_checkpoint", False),
            "closed": bool(item.get("closed")),
            "service": item.get("service"),
        })
    return rows


def read_session(workspace: Path, session: str, *, before: int | None = None, limit: int = 40) -> dict[str, Any]:
    if not is_valid_session_name(session):
        raise SessionSearchError(f"invalid session name {session!r}")
    limit = max(1, min(int(limit), MAX_READ_LIMIT))
    store = SessionStore(workspace)
    if not read_session_document(store, session):
        raise SessionSearchError(f"no session {session!r} in this workspace")
    try:
        page = read_history(get_journal_path(store, session), Path(workspace), limit=limit, before=before)
    except JsonStorageError as error:
        raise SessionSearchError(f"the journal of {session} cannot be read: {error}") from None
    events = []
    for entry in page["events"]:
        event = entry["event"]
        text = _event_text(event)
        if event.get("type") not in _EVENT_TYPES or not text:
            continue
        events.append({"seq": entry["seq"], "turn": entry["turn"], "ts": entry["ts"], "type": event.get("type"),
                       "text": redact_text(text)[:4000]})
    first = page["events"][0]["seq"] if page["events"] else None
    return {"session": session, "events": events, "has_more": page["has_more"],
            "next_before": first if page["has_more"] else None}


def search_sessions(workspace: Path, query: str, *, limit: int = 20, include_service: bool = True) -> dict[str, Any]:
    words = [word.lower() for word in re.findall(r"\w{2,}", query or "", re.UNICODE)]
    if not words:
        raise SessionSearchError("the query needs at least one word")
    limit = max(1, min(int(limit), 100))
    store = SessionStore(workspace)
    hits: list[dict[str, Any]] = []
    scanned = 0
    for session in list_sessions(workspace, include_service=include_service):
        name = session["session"]
        try:
            page = read_history(get_journal_path(store, name), Path(workspace), limit=_SCAN_EVENTS_PER_SESSION)
        except JsonStorageError:
            continue
        scanned += 1
        for entry in page["events"]:
            event = entry["event"]
            if event.get("type") not in _EVENT_TYPES:
                continue
            text = _event_text(event)
            lowered = text.lower()
            matched = sum(1 for word in words if word in lowered)
            if matched == 0:
                continue
            hits.append({
                "session": name,
                "title": session["title"],
                "seq": entry["seq"],
                "turn": entry["turn"],
                "ts": entry["ts"],
                "type": event.get("type"),
                "score": round(matched / len(words), 3),
                "excerpt": _excerpt(redact_text(text), words),
            })
    hits.sort(key=lambda hit: (hit["score"], hit["ts"] or 0), reverse=True)
    return {"query": query, "hits": hits[:limit], "total_hits": len(hits), "sessions_scanned": scanned}


def _event_text(event: dict[str, Any]) -> str:
    parts = []
    for key in _TEXT_KEYS:
        value = event.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    return "\n".join(parts)


def _excerpt(text: str, words: list[str]) -> str:
    lowered = text.lower()
    position = min((lowered.find(word) for word in words if word in lowered), default=0)
    start = max(0, position - MAX_EXCERPT // 3)
    excerpt = text[start:start + MAX_EXCERPT].replace("\n", " ")
    return ("…" if start else "") + excerpt + ("…" if start + MAX_EXCERPT < len(text) else "")
