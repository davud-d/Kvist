# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
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


def get_logs_dir(self):
    return self.executor.workspace / self.config.logs_dirname


def get_state_path(self):
    return self.executor.workspace / self.config.state_filename


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
    state_path = get_state_path(self)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint = {
        "goal": user_goal,
        "step": step,
        "progress": self._state.progress,
        "summary": self._state.summary,
        "files_read": self._state.files_read,
        "files_changed": self._state.files_changed,
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
    state_path.write_text(
        json.dumps(checkpoint, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def load_checkpoint(self) -> tuple[str, int] | None:
    state_path = get_state_path(self)
    if not state_path.exists():
        return None
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return None

    if not isinstance(data, dict) or "goal" not in data:
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
    self._state.summary = data.get("summary", "")
    self._state.files_read = data.get("files_read", [])
    self._state.files_changed = data.get("files_changed", [])
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
    self._state.pending_instruction = data.get("pending_instruction", "")
    self._state.last_action_signature = data.get("last_action_signature", "")

    return data["goal"], data.get("step", 1)


def clear_checkpoint(self) -> None:
    state_path = get_state_path(self)
    if state_path.exists():
        state_path.unlink()