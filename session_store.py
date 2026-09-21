# -*- coding: utf-8 -*-
"""Headless, LLM-free session lifecycle operations for frontends.

Frontends must never read or write ``.kvist`` session files directly. This
module is the supported core boundary for the lifecycle operations that need
no model call: status, list, history, create, new, launch, switch, delete, and reset. Operations that run
the agent (submitting a goal, resuming a checkpoint) go through the event
frontend (``event_stdio``) instead, which reports the active session and
checkpoint presence in its hello event.

Semantics match the terminal UI exactly: ``create`` creates AND activates a
named empty session (like ``/session`` → new), ``new`` does the same with a
name Kvist allocates (``/new``), ``launch`` starts an app in its own fresh
session, ``switch`` activates an existing session and fails on a missing or corrupt target,
``delete`` refuses the active session, and ``reset`` empties the active
session in place.
A headless process has no in-memory conversation to save, so ``switch`` only
moves the active marker — the outgoing session was already persisted by the
process that owned it.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_logging import (
    create_session,
    delete_session,
    get_journal_path,
    is_valid_session_name,
    list_sessions,
    load_active_session,
    find_resumable_session,
    peek_checkpoint,
    read_session_document,
    reset_session,
    rotate_to_new_session,
    save_active_session,
)
from durable_json import JsonStorageCorruptError, JsonStorageError
from session_events import (
    DEFAULT_HISTORY_LIMIT,
    MAX_HISTORY_LIMIT,
    journal_size,
    read_history,
)

SESSION_OPS = (
    "status",
    "list",
    "history",
    "create",
    "switch",
    "delete",
    "reset",
    "new",
    "launch",
)


@dataclass(slots=True)
class _WorkspaceHandle:
    workspace: Path


class SessionStore:
    """Duck-typed host for ``agent_logging``'s session functions.

    Those functions read ``self.executor.workspace`` and the session
    attributes; this class provides exactly that surface without an LLM
    client, executor sandbox, or UI.
    """

    def __init__(self, workspace: str | Path) -> None:
        resolved = Path(workspace).expanduser().resolve(strict=True)
        self.executor = _WorkspaceHandle(resolved)
        self._storage_warnings: list[str] = []
        self.current_session = load_active_session(self)

    def _session_items(
        self,
        sessions: list[str],
        *,
        with_histories: bool = True,
    ) -> list[dict[str, Any]]:
        items: list[dict[str, Any]] = []
        for name in sessions:
            try:
                document = read_session_document(self, name)
            except JsonStorageCorruptError:
                self._storage_warnings.append(
                    f"Session {name!r} is damaged and was hidden from the session list."
                )
                continue

            session_history = document.get("session_history")
            if not isinstance(session_history, list):
                session_history = []
            chat_history = document.get("chat_history")
            if not isinstance(chat_history, list):
                chat_history = []

            title = ""
            for entry in session_history:
                if isinstance(entry, dict) and isinstance(entry.get("goal"), str):
                    title = entry["goal"].strip()
                    if title:
                        break
            if not title:
                for message in chat_history:
                    if (
                        isinstance(message, dict)
                        and message.get("role") == "user"
                        and isinstance(message.get("content"), str)
                    ):
                        title = message["content"].strip()
                        if title:
                            break
            checkpoint = document.get("checkpoint")
            if (
                not title
                and isinstance(checkpoint, dict)
                and isinstance(checkpoint.get("goal"), str)
            ):
                title = checkpoint["goal"].strip()

            updated_at = document.get("updated_at")
            item = {
                "name": name,
                "title": title or name,
                "updated_at": (
                    float(updated_at)
                    if isinstance(updated_at, (int, float))
                    else 0.0
                ),
                "has_checkpoint": isinstance(checkpoint, dict),
                "journal_bytes": self._journal_bytes(name),
            }
            if with_histories:
                item["session_history"] = session_history
                item["chat_history"] = chat_history
            items.append(item)
        return sorted(items, key=lambda item: item["updated_at"], reverse=True)

    def _journal_bytes(self, name: str) -> int:
        try:
            return journal_size(get_journal_path(self, name))
        except JsonStorageError:
            self._storage_warnings.append(
                f"The event journal of session {name!r} is unreadable."
            )
            return 0

    def _overview(self, *, with_histories: bool) -> dict[str, Any]:
        sessions = list_sessions(self)
        return {
            "active_session": self.current_session,
            "sessions": sessions,
            "session_items": self._session_items(
                sessions, with_histories=with_histories
            ),
            "checkpoint": peek_checkpoint(self),
            "resumable_session": find_resumable_session(self),
            "warnings": list(self._storage_warnings),
        }

    def status(self) -> dict[str, Any]:
        return self._overview(with_histories=True)

    def listing(self) -> dict[str, Any]:
        """``status`` without the per-session histories: cheap for a session picker."""
        return self._overview(with_histories=False)

    def history(
        self,
        name: str,
        limit: int = DEFAULT_HISTORY_LIMIT,
        before: int | None = None,
    ) -> dict[str, Any]:
        if not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if not 1 <= limit <= MAX_HISTORY_LIMIT:
            raise ValueError(f"History limit must be between 1 and {MAX_HISTORY_LIMIT}")
        if before is not None and before < 1:
            raise ValueError("History cursor must be a positive sequence number")
        if not read_session_document(self, name):
            raise ValueError(f"Unknown session: {name!r}")
        page = read_history(
            get_journal_path(self, name),
            self.executor.workspace,
            limit=limit,
            before=before,
        )
        return {"session": name, **page}

    def create(self, name: str) -> dict[str, Any]:
        if not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if not create_session(self, name):
            raise ValueError(f"Session {name!r} already exists")
        save_active_session(self, name)
        self.current_session = name
        return self.status()

    def switch(self, name: str) -> dict[str, Any]:
        if not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if name != self.current_session:
            document = read_session_document(self, name)
            if not document:
                raise ValueError(f"Unknown session: {name!r}")
            save_active_session(self, name)
            self.current_session = name
        return self.status()

    def delete(self, name: str) -> dict[str, Any]:
        if not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if not delete_session(self, name):
            raise ValueError(f"Unknown session: {name!r}")
        return self.status()

    def reset(self) -> dict[str, Any]:
        reset_session(self)
        return self.status()

    def new(self) -> dict[str, Any]:
        rotate_to_new_session(self)
        return self.status()

    def launch(self) -> dict[str, Any]:
        """Start an app run in its own session (the launch contract).

        An interrupted run stays reachable through ``resumable_session`` in the
        returned status, and ``--frontend stdio --resume`` walks to it, so a
        launch never has to inherit the previous conversation to keep it.
        """
        rotate_to_new_session(self)
        return self.status()


def run_session_op(
    workspace: str | Path,
    op: str,
    name: str | None = None,
    *,
    limit: int | None = None,
    before: int | None = None,
) -> dict[str, Any]:
    """Execute one session operation and return its JSON payload."""
    if op not in SESSION_OPS:
        raise ValueError(f"Unknown session operation: {op!r}")
    store = SessionStore(workspace)
    if op in ("status", "reset", "new", "launch"):
        payload = getattr(store, op)()
    elif op == "list":
        payload = store.listing()
    elif not name:
        raise ValueError(f"Session operation {op!r} requires a session name")
    elif op == "history":
        payload = store.history(
            name, DEFAULT_HISTORY_LIMIT if limit is None else limit, before
        )
    else:
        payload = getattr(store, op)(name)
    return {"ok": True, **payload}
