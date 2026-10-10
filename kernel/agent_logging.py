# -*- coding: utf-8 -*-
from __future__ import annotations

import re
import time
from typing import Any

from kernel.durable_json import (
    JsonStorageCorruptError,
    JsonStorageError,
    create_json_object,
    delete_json_object,
    exclusive_storage_lock,
    list_json_object_entries,
    read_json_object,
    update_json_object,
    validate_storage_directory,
    validate_storage_path,
    write_json_object,
)
from kernel import end_session
from kernel.redaction import redact_value
from kernel.session_events import JOURNAL_SUFFIX, delete_journal, journal_size

_SESSION_VERSION = 1
_SESSION_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z", re.ASCII)


def get_logs_dir(self):
    return validate_storage_directory(
        self.executor.workspace / self.config.logs_dirname,
        self.executor.workspace,
    )


def is_valid_session_name(session_name: object) -> bool:
    return (
        isinstance(session_name, str)
        and _SESSION_NAME_RE.fullmatch(session_name) is not None
    )


def get_state_path(self, session_name: str = "default"):
    if not is_valid_session_name(session_name):
        raise ValueError(f"Invalid session name: {session_name!r}")
    return validate_storage_path(
        self.executor.workspace / ".kvist" / "sessions" / f"{session_name}.json",
        self.executor.workspace,
    )


def get_journal_path(self, session_name: str):
    """The session's event journal; its lifecycle is the session's own."""
    if not is_valid_session_name(session_name):
        raise ValueError(f"Invalid session name: {session_name!r}")
    return validate_storage_path(
        self.executor.workspace
        / ".kvist"
        / "sessions"
        / f"{session_name}{JOURNAL_SUFFIX}",
        self.executor.workspace,
    )


def get_active_session_path(self):
    return validate_storage_path(
        self.executor.workspace / ".kvist" / "active_session.json",
        self.executor.workspace,
    )


def get_sessions_dir(self):
    return validate_storage_directory(
        self.executor.workspace / ".kvist" / "sessions",
        self.executor.workspace,
    )


def _read_json_object(path, boundary=None) -> dict[str, Any]:
    root = boundary or path.parent.resolve(strict=True)
    return read_json_object(path, boundary=root)


def _write_json_object(path, data: dict[str, Any], boundary=None) -> None:
    root = boundary or path.parent.resolve(strict=True)
    write_json_object(path, data, boundary=root)


def _validated_session_document(
    data: dict[str, Any],
    session: str,
) -> dict[str, Any]:
    version = data.get("version")
    if version is None:
        return data
    if type(version) is not int or version != _SESSION_VERSION:
        detail = (
            f" {version}"
            if type(version) is int
            else " with an invalid type"
        )
        raise JsonStorageCorruptError(
            f"Unsupported session version{detail} for session {session!r}"
        )
    saved_session = data.get("session")
    if saved_session is not None and saved_session != session:
        raise JsonStorageCorruptError(
            f"Session file identity does not match session {session!r}"
        )
    return data


_STYLE_FAMILIES = ("agents", "chat")


def session_styles(data: dict[str, Any]) -> dict[str, str]:
    """The styles a session was opened with, falling back to the current pick.

    One session, one style: a plan and the steps that follow it must never come
    from different beliefs, so the choice is stamped when the session starts and
    every run inside it resolves the same files. A session written before styles
    existed carries none; it adopts the current preference once, when it is
    loaded, and keeps it from then on.
    """
    from kernel.user_config import get_saved_style

    stored = data.get("styles")
    resolved: dict[str, str] = {}
    for family in _STYLE_FAMILIES:
        value = stored.get(family) if isinstance(stored, dict) else None
        resolved[family] = (
            value if isinstance(value, str) and value.strip() else get_saved_style(family)
        )
    return resolved


def _session_checkpoint(data: dict[str, Any]) -> dict[str, Any] | None:
    checkpoint = data.get("checkpoint")
    if isinstance(checkpoint, dict) and "goal" in checkpoint:
        return checkpoint
    # Backward compatibility with the original flat checkpoint format.
    if "goal" in data:
        return data
    return None


_PERSISTED_MESSAGE_KEYS = ("role", "content", "reasoning_content")


def _serializable_chat_history(self) -> list[dict[str, Any]]:
    history = getattr(self, "_chat_history", [])
    if not isinstance(history, list):
        return []
    # ``reasoning_content`` is kept alongside role/content: DeepSeek thinking
    # mode returns HTTP 400 for a resumed history that carries an assistant
    # ``content`` without the ``reasoning_content`` it was produced with.
    return [
        {
            key: value
            for key, value in message.items()
            if key in _PERSISTED_MESSAGE_KEYS
        }
        for message in history
        if isinstance(message, dict)
    ]


def _session_document(
    self,
    checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    document = {
        "version": _SESSION_VERSION,
        "session": getattr(self, "current_session", "default"),
        "updated_at": time.time(),
        "session_history": getattr(self, "_session_history", []),
        "chat_history": _serializable_chat_history(self),
        "response_language": getattr(self, "_last_response_language", None),
        "styles": getattr(self, "_session_styles", None) or session_styles({}),
        "checkpoint": checkpoint,
        "title": getattr(self, "_session_title", None),
        "title_manual": bool(getattr(self, "_title_manual", False)),
        **end_session.document_fields(self),
    }
    sanitized = redact_value(document)
    assert isinstance(sanitized, dict)
    return sanitized


def _empty_session_document(session: str) -> dict[str, Any]:
    return {
        "version": _SESSION_VERSION,
        "session": session,
        "updated_at": time.time(),
        "session_history": [],
        "chat_history": [],
        "response_language": None,
        "styles": session_styles({}),
        "checkpoint": None,
        "title": None,
        "title_manual": False,
    }


# Prefix of the names Kvist allocates on its own (app launch, ``/new``). Only
# such a session may be dropped automatically when it is left empty; a session
# the user named is always kept.
_AUTO_SESSION_PREFIX = "chat-"


def generate_session_name(self) -> str:
    """Allocate an unused, timestamped name for an auto-created session."""
    base = _AUTO_SESSION_PREFIX + time.strftime("%Y%m%d-%H%M%S", time.localtime())
    taken = set(list_sessions(self))
    if base not in taken:
        return base
    for suffix in range(2, 1000):
        candidate = f"{base}-{suffix}"
        if candidate not in taken:
            return candidate
    raise ValueError("Could not allocate a new session name")


def session_is_empty(self, session: str) -> bool:
    """True when a session holds no history, no chat, and no checkpoint.

    A damaged session counts as non-empty: it may still hold the user's work,
    and destroying what cannot be read is never the safe answer.
    """
    try:
        document = read_session_document(self, session)
        recorded = journal_size(get_journal_path(self, session)) > 0
    except JsonStorageError:
        return False
    if recorded:
        return False
    if not document:
        return True
    return not (
        document.get("session_history")
        or document.get("chat_history")
        or document.get("checkpoint")
    )


def discard_session_if_empty(self, session: str) -> bool:
    """Drop an auto-created session that was left without any content."""
    if not session.startswith(_AUTO_SESSION_PREFIX):
        return False
    if session == getattr(self, "current_session", "default"):
        return False
    if not session_is_empty(self, session):
        return False
    return delete_session(self, session)


def find_resumable_session(self) -> str | None:
    """Name the session holding the most recent resumable checkpoint.

    A launch opens its own session, so an interrupted run's checkpoint is
    usually not in the active one any more. Scanning is what keeps ``/resume``
    reachable without pinning a launch to the previous conversation. Damaged
    sessions are skipped rather than reported as resumable, and so are sessions
    end_session closed: nobody continues those.
    """
    newest: tuple[float, str] | None = None
    for session in list_sessions(self):
        try:
            document = read_session_document(self, session)
        except JsonStorageCorruptError:
            continue
        if not document or _session_checkpoint(document) is None or document.get("closed") is True:
            continue
        updated_at = document.get("updated_at")
        stamp = float(updated_at) if isinstance(updated_at, (int, float)) else 0.0
        if newest is None or stamp > newest[0]:
            newest = (stamp, session)
    return newest[1] if newest else None


def rotate_to_new_session(self) -> str:
    """Create, activate, and return a fresh auto-named session.

    Storage only: the caller owns whatever in-memory state belongs to the
    outgoing session. The outgoing session is dropped when Kvist created it
    automatically and it was never used, so repeated launches cannot litter
    the session list with empty entries.
    """
    previous = getattr(self, "current_session", "default")
    session = generate_session_name(self)
    if not create_session(self, session):
        raise ValueError(f"Session {session!r} already exists")
    save_active_session(self, session)
    self.current_session = session
    discard_session_if_empty(self, previous)
    return session


def create_session(self, session: str) -> bool:
    if not is_valid_session_name(session):
        raise ValueError(f"Invalid session name: {session!r}")
    created = create_json_object(
        get_state_path(self, session),
        _empty_session_document(session),
        boundary=self.executor.workspace,
    )
    if created:
        # A journal left behind by a session of the same name must not be
        # replayed into this one.
        delete_journal(get_journal_path(self, session), self.executor.workspace)
    return created


def save_active_session(self, name: str | None = None) -> None:
    session = name or getattr(self, "current_session", "default")
    if not is_valid_session_name(session):
        raise ValueError(f"Invalid session name: {session!r}")
    _write_json_object(
        get_active_session_path(self),
        {"session": session, "updated_at": time.time()},
        boundary=self.executor.workspace,
    )


def load_active_session(self) -> str:
    try:
        marker = _read_json_object(
            get_active_session_path(self),
            boundary=self.executor.workspace,
        )
    except JsonStorageCorruptError:
        warnings = getattr(self, "_storage_warnings", None)
        if not isinstance(warnings, list):
            warnings = []
            self._storage_warnings = warnings
        warnings.append(
            "The active-session marker was damaged; Kvist selected the "
            "most recent usable session."
        )
        marker = {}
    saved_name = marker.get("session")
    if is_valid_session_name(saved_name):
        if saved_name == "default" or get_state_path(self, saved_name).is_file():
            return saved_name

    sessions_dir = get_sessions_dir(self)
    if not sessions_dir.exists():
        return "default"
    candidates = [
        (path, info)
        for path, info in list_json_object_entries(
            sessions_dir,
            boundary=self.executor.workspace,
        )
        if is_valid_session_name(path.stem)
    ]
    if not candidates:
        return "default"
    ordered = sorted(
        candidates,
        key=lambda candidate: candidate[1].st_mtime_ns,
        reverse=True,
    )
    for candidate, _info in ordered:
        try:
            data = _read_json_object(
                candidate,
                boundary=self.executor.workspace,
            )
            if data:
                _validated_session_document(data, candidate.stem)
        except JsonStorageCorruptError:
            continue
        return candidate.stem
    return "default"


def list_sessions(self) -> list[str]:
    sessions_dir = get_sessions_dir(self)
    if not sessions_dir.exists():
        return []
    sessions: list[str] = []
    for path, info in list_json_object_entries(
        sessions_dir,
        boundary=self.executor.workspace,
    ):
        if (
            info.st_size > 0
            and is_valid_session_name(path.stem)
        ):
            sessions.append(path.stem)
    return sorted(sessions)


def delete_session(self, name: str) -> bool:
    if name == getattr(self, "current_session", "default"):
        raise ValueError(f"Cannot delete the active session {name!r}")
    path = get_state_path(self, name)
    removed = delete_json_object(path, boundary=self.executor.workspace)
    delete_journal(get_journal_path(self, name), self.executor.workspace)
    if removed:
        # Re-assert the marker so a stale marker pointing at the deleted
        # session can never resurrect it on the next start.
        save_active_session(self)
    return removed


def begin_run(self, user_goal: str, run_id: str) -> None:
    """Durably activate a NEW run before its first step.

    Writes a ``phase="starting"`` checkpoint that atomically replaces any prior
    resumable checkpoint in a single durable write — so a crash before the
    first step leaves NEW (restartable, no external side effect yet)
    recoverable and OLD unreachable, never a window where neither exists.
    """
    session = getattr(self, "current_session", "default")
    checkpoint = {
        "goal": user_goal,
        "run_id": run_id,
        "phase": "starting",
        "step": 0,
        "pending_action": None,
        "current_agent_role": self._state.current_agent_role,
        "selected_action_role": self._state.selected_action_role,
        "selected_action_model": self._state.selected_action_model,
    }
    _write_json_object(
        get_state_path(self, session),
        _session_document(self, checkpoint=checkpoint),
        boundary=self.executor.workspace,
    )
    save_active_session(self, session)


def reset_session(self) -> None:
    """Durably reset the active session to an empty document (the /new
    contract): history, chat, and checkpoint are gone; the name is kept.

    A session end_session closed is refused: emptying it would reopen it.
    """
    session = getattr(self, "current_session", "default")
    if read_session_document(self, session).get("closed") is True:
        raise ValueError(f"Session {session!r} is closed and cannot be reset; start a new one.")
    # The journal goes first: a crash in between must leave the conversation
    # without its display log, never a reset session that still shows its old feed.
    delete_journal(get_journal_path(self, session), self.executor.workspace)
    _write_json_object(
        get_state_path(self, session),
        _empty_session_document(session),
        boundary=self.executor.workspace,
    )
    save_active_session(self, session)


def peek_checkpoint(
    self,
    session: str | None = None,
) -> dict[str, Any] | None:
    """Report the resumable checkpoint of a session without loading it into
    the run state. Returns ``{"goal", "step", "progress"}`` or ``None``."""
    resolved = session or getattr(self, "current_session", "default")
    data = _read_json_object(
        get_state_path(self, resolved),
        boundary=self.executor.workspace,
    )
    if not data:
        return None
    checkpoint = _session_checkpoint(_validated_session_document(data, resolved))
    if checkpoint is None:
        return None
    return {
        "goal": str(checkpoint.get("goal", "")),
        "step": checkpoint.get("step", 1),
        "progress": checkpoint.get("progress", 0),
    }


def _has_session_content(self) -> bool:
    """True when the in-memory session holds something worth persisting."""
    return bool(getattr(self, "_session_history", None)) or bool(
        _serializable_chat_history(self)
    )


def save_session(self) -> None:
    session = getattr(self, "current_session", "default")
    state_path = get_state_path(self, session)
    # An untouched session is never materialised: starting Kvist and leaving
    # without saying anything must not add an entry to the session list.
    if not _has_session_content(self) and not state_path.exists():
        return
    update_json_object(
        state_path,
        lambda existing: _session_document(
            self,
            checkpoint=_session_checkpoint(existing),
        ),
        boundary=self.executor.workspace,
    )
    save_active_session(self, session)


def store_session_title(self, session: str, title: str) -> None:
    """Persist a generated title for *session* directly on disk.

    Targets the session by name rather than ``self.current_session``: title
    generation runs on a background thread, and the user may have switched
    (or even deleted) sessions by the time it finishes. A title is never
    written over one the session already has (generated or manual) — set by
    ``update``, so this is safe to call even if two generations somehow race.
    A missing session (deleted meanwhile) is silently skipped: there is
    nothing left to title.
    """
    state_path = get_state_path(self, session)
    try:
        if not _read_json_object(state_path, boundary=self.executor.workspace):
            return  # Session was deleted (or never saved) before this landed.
    except JsonStorageError:
        return

    def update(existing: dict[str, Any]) -> dict[str, Any]:
        if not existing:
            return existing
        if existing.get("title_manual") or (
            isinstance(existing.get("title"), str) and existing["title"].strip()
        ):
            return existing
        existing["title"] = title
        return existing

    try:
        update_json_object(state_path, update, boundary=self.executor.workspace)
    except JsonStorageError:
        return
    if session == getattr(self, "current_session", None) and not getattr(
        self, "_title_manual", False
    ):
        self._session_title = title


def rename_session(self, title: str) -> None:
    """Set the active session's title by hand; automatic generation never
    overwrites a manually-set title again.

    Naming a session is deliberate content in its own right, so this writes
    even for an otherwise-untouched session — unlike ``save_session``, which
    skips materializing one that holds nothing yet.
    """
    self._session_title = title
    self._title_manual = True
    session = getattr(self, "current_session", "default")
    update_json_object(
        get_state_path(self, session),
        lambda existing: _session_document(
            self, checkpoint=_session_checkpoint(existing)
        ),
        boundary=self.executor.workspace,
        empty_as_missing=True,
    )
    save_active_session(self, session)


def session_style(self, style_type: str) -> str:
    """The style this session runs under, whatever the global preference says."""
    styles = getattr(self, "_session_styles", None)
    if isinstance(styles, dict) and isinstance(styles.get(style_type), str):
        return styles[style_type]
    return session_styles({})[style_type]


def adopt_session_style(self, agent_style: str) -> bool:
    """Apply an agent style choice, and the chat style that goes with it, to the active session.

    One session, one style. A session that has produced nothing yet can adopt
    the new choice: no plan, no step and no answer came out of the old one. A
    session that already holds work keeps the styles its history was made with,
    and the choice starts with the next session. Returns True when the active
    session adopted it.
    """
    from kernel.user_config import chat_style_for

    styles = getattr(self, "_session_styles", None)
    if not isinstance(styles, dict):
        styles = session_styles({})
        self._session_styles = styles
    if _has_session_content(self):
        return False
    styles["agents"] = agent_style
    styles["chat"] = chat_style_for(agent_style)
    save_session(self)
    return True


def read_session_document(
    self,
    session: str | None = None,
) -> dict[str, Any]:
    resolved_session = session or getattr(self, "current_session", "default")
    state_path = get_state_path(self, resolved_session)
    data = _read_json_object(
        state_path,
        boundary=self.executor.workspace,
    )
    if not data:
        return {}
    validated = _validated_session_document(data, resolved_session)
    sanitized = redact_value(validated)
    assert isinstance(sanitized, dict)
    if sanitized != validated:
        _write_json_object(
            state_path,
            sanitized,
            boundary=self.executor.workspace,
        )
    return sanitized


def restore_session_document(self, data: dict[str, Any]) -> bool:
    self._session_history = []
    self._last_response_language = None
    if hasattr(self, "_chat_history"):
        del self._chat_history
    # Stamped before the empty-document exit: a session that starts here owns a
    # style from its first step, not from its first save.
    self._session_styles = session_styles(data)
    title = data.get("title") if isinstance(data, dict) else None
    self._session_title = title if isinstance(title, str) and title.strip() else None
    self._title_manual = bool(data.get("title_manual")) if isinstance(data, dict) else False
    end_session.restore(self, data if isinstance(data, dict) else {})
    if not data:
        return False

    saved_history = data.get("session_history")
    self._session_history = saved_history if isinstance(saved_history, list) else []

    saved_language = data.get("response_language")
    if saved_language in ("ru", "en"):
        self._last_response_language = saved_language

    saved_chat = data.get("chat_history")
    if isinstance(saved_chat, list) and saved_chat:
        self._chat_history = [
            message
            for message in saved_chat
            if isinstance(message, dict)
            and message.get("role") in ("system", "user", "assistant")
            and isinstance(message.get("content"), str)
        ]
    return True


def load_session(self) -> bool:
    return restore_session_document(self, read_session_document(self))


def sanitize_for_log(self, value: Any, max_str_len: int = 300) -> Any:
    return redact_value(value, max_str_len=max_str_len)


def persist_logs(self, user_goal: str) -> None:
    if not self.config.persist_logs:
        return

    logs_dir = get_logs_dir(self)
    payload = {
        "goal": user_goal,
        "current_agent_role": self._state.current_agent_role,
        "task_language": self._state.task_language,
        "response_language": self._state.response_language,
        "create_goal": self._state.create_goal,
        "selected_action_role": self._state.selected_action_role,
        "selected_action_model": self._state.selected_action_model,
        "plan_phase": self._state.plan_phase,
        "approved_plan": self._state.approved_plan,
        "task_updates": self._state.task_updates,
        "status": self._state.run_status,
        "progress": self._state.progress,
        "summary": self._state.summary,
        "files_read": self._state.files_read,
        "files_changed": self._state.files_changed,
        "required_artifacts": self._state.required_artifacts,
        "missing_artifacts": self._state.missing_artifacts,
        "verification_evidence": self._state.verification_evidence,
        "completion_blockers": self._state.completion_blockers,
        "errors": self._state.error_history,
        "user_messages": self._state.user_messages,
        "web_research": {
            "dependency_versions": self._state.dependency_versions,
            "searches": self._state.web_search_history,
            "sources": self._state.web_sources,
            "facts": self._state.web_facts,
            "applied_facts": self._state.applied_facts,
        },
        "steps": self._state.step_logs,
    }
    sanitized_payload = self._sanitize_for_log(payload)
    assert isinstance(sanitized_payload, dict)
    with exclusive_storage_lock(
        logs_dir / "run-allocation",
        boundary=self.executor.workspace,
    ):
        indices = []
        for candidate in logs_dir.glob("run_*.json"):
            suffix = candidate.stem.removeprefix("run_")
            if suffix.isdigit():
                indices.append(int(suffix))
        next_index = max(indices, default=0) + 1
        log_path = logs_dir / f"run_{next_index:03d}.json"
        _write_json_object(
            log_path,
            sanitized_payload,
            boundary=self.executor.workspace,
        )


def save_checkpoint(
    self,
    user_goal: str,
    step: int,
    pending_action: dict[str, Any] | None = None,
) -> None:
    session = getattr(self, "current_session", "default")
    state_path = get_state_path(self, session)
    checkpoint = {
        "goal": user_goal,
        # Identifies the run this checkpoint belongs to. A new run supersedes the
        # previous one before execution, so a stale checkpoint can never be
        # reconciled against a different run's pending action.
        "run_id": getattr(self, "_run_id", None),
        # Lifecycle phase: an executing checkpoint carries real step state; a
        # "starting" checkpoint (written by begin_run) represents an accepted
        # run that has not taken a step, so resume starts it from step 1 rather
        # than reviving an older run.
        "phase": "executing",
        "plan_phase": self._state.plan_phase,
        "approved_plan": self._state.approved_plan,
        "task_updates": self._state.task_updates,
        # A mutating action that is about to run is recorded here BEFORE it
        # executes, so a crash in the mutation window is reconciled on resume
        # instead of blindly replayed. Cleared (None) on the post-action save.
        "pending_action": pending_action,
        "current_agent_role": self._state.current_agent_role,
        "task_language": self._state.task_language,
        "response_language": self._state.response_language,
        "create_goal": self._state.create_goal,
        "selected_action_role": self._state.selected_action_role,
        "selected_action_model": self._state.selected_action_model,
        "step": step,
        "progress": self._state.progress,
        "summary": self._state.summary,
        "files_read": self._state.files_read,
        "files_changed": self._state.files_changed,
        "required_artifacts": self._state.required_artifacts,
        "missing_artifacts": self._state.missing_artifacts,
        "verification_evidence": self._state.verification_evidence,
        "pending_verification_paths": self._state.pending_verification_paths,
        "destructive_diff": self._state.destructive_diff,
        "completion_blockers": self._state.completion_blockers,
        "required_next_actions": self._state.required_next_actions,
        "recent_feedback": self._state.recent_feedback,
        "recent_events": self._state.recent_events,
        "recent_result_details": self._state.recent_result_details,
        "recent_result_signatures": self._state.recent_result_signatures,
        "error_history": self._state.error_history,
        "error_patterns": self._state.error_patterns,
        "seen_actions": self._state.seen_actions,
        "run_status": self._state.run_status,
        "hard_denial_rules": self._state.hard_denial_rules,
        "recovery_signatures": list(self._state.recovery_signatures),
        "recovery_pending": self._state.recovery_pending,
        "user_messages": self._state.user_messages,
        "dependency_versions": self._state.dependency_versions,
        "web_facts": self._state.web_facts,
        "web_sources": self._state.web_sources,
        "consecutive_errors": self._state.consecutive_errors,
        "stagnation_count": self._state.stagnation_count,
        "parse_errors": self._state.parse_errors,
        "read_only_streak": self._state.read_only_streak,
        "pending_instruction": self._state.pending_instruction,
        "last_action_signature": self._state.last_action_signature,
        "corrective_read_budget": self._state.corrective_read_budget,
        "stalled_reasons": self._state.stalled_reasons,
        "last_progress": self._state.last_progress,
        "last_web_query": self._state.last_web_query,
        "last_web_thought": self._state.last_web_thought,
        # JSON forces dict keys to strings; load_checkpoint casts step keys
        # back to int so sorted()/max() over rolling_summaries stay numeric.
        "rolling_summaries": {
            str(step_num): text
            for step_num, text in self._state.rolling_summaries.items()
        },
    }
    _write_json_object(
        state_path,
        _session_document(self, checkpoint=checkpoint),
        boundary=self.executor.workspace,
    )
    save_active_session(self, session)


def load_checkpoint(self) -> tuple[str, int] | None:
    session = getattr(self, "current_session", "default")
    session_data = _read_json_object(
        get_state_path(self, session),
        boundary=self.executor.workspace,
    )
    if not session_data:
        return None
    session_data = _validated_session_document(session_data, session)

    # Histories are session data, not checkpoint data, and must survive
    # completed tasks as well as interrupted ones.
    saved_history = session_data.get("session_history")
    if isinstance(saved_history, list):
        self._session_history = saved_history
    saved_chat = session_data.get("chat_history")
    if isinstance(saved_chat, list) and saved_chat:
        self._chat_history = saved_chat

    data = _session_checkpoint(session_data)
    if data is None:
        self._pending_action = None
        return None

    pending = data.get("pending_action")
    self._pending_action = pending if isinstance(pending, dict) else None
    self._run_id = data.get("run_id")
    self._resumed_phase = data.get("phase", "executing")

    self._state.plan_phase = str(data.get("plan_phase") or "")
    self._state.approved_plan = str(data.get("approved_plan") or "")
    self._state.task_updates = list(data.get("task_updates") or [])
    self._state.progress = data.get("progress", 0)
    self._state.current_agent_role = str(data.get("current_agent_role") or "coder")
    self._state.task_language = str(data.get("task_language") or "")
    saved_response_language = data.get("response_language")
    self._state.response_language = (
        saved_response_language if saved_response_language in ("ru", "en") else ""
    )
    self._state.create_goal = bool(data.get("create_goal", False))
    self._state.selected_action_role = str(data.get("selected_action_role") or "")
    self._state.selected_action_model = str(data.get("selected_action_model") or "")
    self._state.summary = data.get("summary", "")
    self._state.files_read = data.get("files_read", [])
    self._state.files_changed = data.get("files_changed", [])
    self._state.required_artifacts = data.get("required_artifacts", [])
    self._state.missing_artifacts = data.get("missing_artifacts", [])
    self._state.verification_evidence = data.get("verification_evidence", [])
    self._state.pending_verification_paths = data.get("pending_verification_paths", [])
    self._state.destructive_diff = data.get("destructive_diff", "")
    self._state.completion_blockers = data.get("completion_blockers", [])
    self._state.required_next_actions = data.get("required_next_actions", [])
    self._state.recent_feedback = data.get("recent_feedback", [])
    self._state.recent_events = data.get("recent_events", [])
    self._state.recent_result_details = data.get("recent_result_details", [])
    self._state.recent_result_signatures = data.get("recent_result_signatures", [])
    self._state.error_history = data.get("error_history", [])
    self._state.error_patterns = data.get("error_patterns", {})
    self._state.seen_actions = data.get("seen_actions", {})
    self._state.run_status = str(data.get("run_status") or "in_progress")
    self._state.hard_denial_rules = data.get("hard_denial_rules", [])
    self._state.recovery_signatures = set(data.get("recovery_signatures", []))
    self._state.recovery_pending = bool(data.get("recovery_pending", False))
    self._state.user_messages = data.get("user_messages", [])
    self._state.dependency_versions = data.get("dependency_versions", {})
    self._state.web_facts = data.get("web_facts", [])
    self._state.web_sources = data.get("web_sources", [])
    self._state.consecutive_errors = data.get("consecutive_errors", 0)
    self._state.stagnation_count = data.get("stagnation_count", 0)
    self._state.parse_errors = data.get("parse_errors", 0)
    self._state.read_only_streak = data.get("read_only_streak", 0)
    self._state.pending_instruction = data.get("pending_instruction", "")
    self._state.last_action_signature = data.get("last_action_signature", "")
    self._state.corrective_read_budget = data.get("corrective_read_budget", 0)
    self._state.stalled_reasons = data.get("stalled_reasons", [])
    self._state.last_progress = data.get("last_progress", 0)
    self._state.last_web_query = data.get("last_web_query", "")
    self._state.last_web_thought = data.get("last_web_thought", "")
    saved_summaries = data.get("rolling_summaries", {})
    if isinstance(saved_summaries, dict):
        self._state.rolling_summaries = {
            int(step_num): text
            for step_num, text in saved_summaries.items()
            if str(step_num).lstrip("-").isdigit()
        }

    # The legacy flat format stored histories inside the checkpoint.
    if "checkpoint" not in session_data:
        legacy_history = data.get("session_history")
        if isinstance(legacy_history, list):
            self._session_history = legacy_history
        legacy_chat = data.get("chat_history")
        if isinstance(legacy_chat, list) and legacy_chat:
            self._chat_history = legacy_chat

    return data["goal"], data.get("step", 1)


def clear_checkpoint(self) -> None:
    session = getattr(self, "current_session", "default")
    state_path = get_state_path(self, session)
    _write_json_object(
        state_path,
        _session_document(self, checkpoint=None),
        boundary=self.executor.workspace,
    )
    save_active_session(self, session)
