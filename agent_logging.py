# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
from typing import Any

from agent_state import PlanInfo

_SECRET_RE = re.compile(
    r"sk-[a-zA-Z0-9]{20,}"
    r"|ghp_[a-zA-Z0-9]{36,}"
    r"|gho_[a-zA-Z0-9]{36,}"
    r"|github_pat_[a-zA-Z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[a-zA-Z0-9\-]{10,}",
    re.ASCII,
)
_SESSION_VERSION = 1
_SESSION_NAME_RE = re.compile(r"[a-z0-9][a-z0-9_-]{0,63}\Z", re.ASCII)


def get_logs_dir(self):
    return self.executor.workspace / self.config.logs_dirname


def is_valid_session_name(session_name: object) -> bool:
    return (
        isinstance(session_name, str)
        and _SESSION_NAME_RE.fullmatch(session_name) is not None
    )


def get_state_path(self, session_name: str = "default"):
    if not is_valid_session_name(session_name):
        raise ValueError(f"Invalid session name: {session_name!r}")
    return self.executor.workspace / ".kvist" / "sessions" / f"{session_name}.json"


def get_active_session_path(self):
    return self.executor.workspace / ".kvist" / "active_session.json"


def get_sessions_dir(self):
    return self.executor.workspace / ".kvist" / "sessions"


def _read_json_object(path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = path.with_suffix(path.suffix + ".tmp")
    temp_path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    temp_path.replace(path)


def _session_checkpoint(data: dict[str, Any]) -> dict[str, Any] | None:
    checkpoint = data.get("checkpoint")
    if isinstance(checkpoint, dict) and "goal" in checkpoint:
        return checkpoint
    # Backward compatibility with the original flat checkpoint format.
    if "goal" in data:
        return data
    return None


def _serializable_chat_history(self) -> list[dict[str, Any]]:
    history = getattr(self, "_chat_history", [])
    if not isinstance(history, list):
        return []
    return [
        {key: value for key, value in message.items() if key in ("role", "content")}
        for message in history
        if isinstance(message, dict)
    ]


def _session_document(
    self,
    checkpoint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "version": _SESSION_VERSION,
        "session": getattr(self, "current_session", "default"),
        "updated_at": time.time(),
        "session_history": getattr(self, "_session_history", []),
        "chat_history": _serializable_chat_history(self),
        "response_language": getattr(self, "_last_response_language", None),
        "checkpoint": checkpoint,
    }


def save_active_session(self, name: str | None = None) -> None:
    session = name or getattr(self, "current_session", "default")
    if not is_valid_session_name(session):
        raise ValueError(f"Invalid session name: {session!r}")
    _write_json_object(
        get_active_session_path(self),
        {"session": session, "updated_at": time.time()},
    )


def load_active_session(self) -> str:
    marker = _read_json_object(get_active_session_path(self))
    saved_name = marker.get("session")
    if is_valid_session_name(saved_name):
        if saved_name == "default" or get_state_path(self, saved_name).is_file():
            return saved_name

    sessions_dir = get_sessions_dir(self)
    if not sessions_dir.exists():
        return "default"
    candidates = [
        path
        for path in sessions_dir.glob("*.json")
        if path.is_file() and is_valid_session_name(path.stem)
    ]
    if not candidates:
        return "default"
    try:
        return max(candidates, key=lambda path: path.stat().st_mtime_ns).stem
    except OSError:
        return "default"


def list_sessions(self) -> list[str]:
    sessions_dir = get_sessions_dir(self)
    if not sessions_dir.exists():
        return []
    return sorted(
        p.stem for p in sessions_dir.glob("*.json")
        if p.is_file() and p.stat().st_size > 0 and is_valid_session_name(p.stem)
    )


def delete_session(self, name: str) -> bool:
    path = get_state_path(self, name)
    if path.exists():
        path.unlink()
        marker = _read_json_object(get_active_session_path(self))
        if marker.get("session") == name:
            save_active_session(self, "default")
        return True
    return False


def save_session(self) -> None:
    session = getattr(self, "current_session", "default")
    state_path = get_state_path(self, session)
    existing = _read_json_object(state_path)
    _write_json_object(
        state_path,
        _session_document(self, checkpoint=_session_checkpoint(existing)),
    )
    save_active_session(self, session)


def load_session(self) -> bool:
    session = getattr(self, "current_session", "default")
    data = _read_json_object(get_state_path(self, session))
    if not data:
        self._session_history = []
        if hasattr(self, "_chat_history"):
            del self._chat_history
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
    elif hasattr(self, "_chat_history"):
        del self._chat_history
    return True


def sanitize_for_log(self, value: Any, max_str_len: int = 300) -> Any:
    if isinstance(value, str):
        sanitized = _SECRET_RE.sub("[REDACTED]", value)
        if len(sanitized) > max_str_len:
            return sanitized[:max_str_len] + "...[TRUNCATED]"
        return sanitized
    if isinstance(value, dict):
        return {k: self._sanitize_for_log(v, max_str_len) for k, v in value.items()}
    if isinstance(value, list):
        return [self._sanitize_for_log(item, max_str_len) for item in value]
    if isinstance(value, set):
        return [self._sanitize_for_log(item, max_str_len) for item in sorted(value)]
    return value


def persist_logs(self, user_goal: str) -> None:
    if not self.config.persist_logs:
        return

    logs_dir = get_logs_dir(self)
    logs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "goal": user_goal,
        "current_agent_role": self._state.current_agent_role,
        "task_language": self._state.task_language,
        "response_language": self._state.response_language,
        "create_goal": self._state.create_goal,
        "selected_action_role": self._state.selected_action_role,
        "selected_action_model": self._state.selected_action_model,
        "plan": {
            "goal": self._state.plan_info.goal if self._state.plan_info else "",
            "complexity": self._state.plan_info.complexity if self._state.plan_info else "",
            "steps": self._state.plan_info.plan if self._state.plan_info else [],
            "success_criteria": self._state.plan_info.success_criteria if self._state.plan_info else [],
            "risks": self._state.plan_info.risks if self._state.plan_info else [],
        },
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
        "steps": self._sanitize_for_log(self._state.step_logs),
    }
    next_index = len(list(logs_dir.glob("run_*.json"))) + 1
    log_path = logs_dir / f"run_{next_index:03d}.json"
    log_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def save_checkpoint(self, user_goal: str, step: int) -> None:
    session = getattr(self, "current_session", "default")
    state_path = get_state_path(self, session)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "goal": user_goal,
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
        "completion_blockers": self._state.completion_blockers,
        "required_next_actions": self._state.required_next_actions,
        "recent_feedback": self._state.recent_feedback,
        "recent_events": self._state.recent_events,
        "error_history": self._state.error_history,
        "error_patterns": self._state.error_patterns,
        "seen_actions": self._state.seen_actions,
        "completed_plan_steps": list(self._state.completed_plan_steps),
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
    }
    if self._state.plan_info:
        checkpoint["plan_info"] = {
            "goal": self._state.plan_info.goal,
            "complexity": self._state.plan_info.complexity,
            "plan": self._state.plan_info.plan,
            "success_criteria": self._state.plan_info.success_criteria,
            "risks": self._state.plan_info.risks,
        }
    _write_json_object(
        state_path,
        _session_document(self, checkpoint=checkpoint),
    )
    save_active_session(self, session)


def load_checkpoint(self) -> tuple[str, int] | None:
    session = getattr(self, "current_session", "default")
    session_data = _read_json_object(get_state_path(self, session))
    if not session_data:
        return None

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
        return None

    plan_data = data.get("plan_info")
    if isinstance(plan_data, dict):
        self._state.plan_info = PlanInfo(
            goal=plan_data.get("goal", ""),
            complexity=plan_data.get("complexity", "simple"),
            plan=plan_data.get("plan", []),
            success_criteria=plan_data.get("success_criteria", []),
            risks=plan_data.get("risks", []),
        )

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
    self._state.completion_blockers = data.get("completion_blockers", [])
    self._state.required_next_actions = data.get("required_next_actions", [])
    self._state.recent_feedback = data.get("recent_feedback", [])
    self._state.recent_events = data.get("recent_events", [])
    self._state.error_history = data.get("error_history", [])
    self._state.error_patterns = data.get("error_patterns", {})
    self._state.seen_actions = data.get("seen_actions", {})
    self._state.completed_plan_steps = set(data.get("completed_plan_steps", []))
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
    _write_json_object(state_path, _session_document(self, checkpoint=None))
    save_active_session(self, session)
