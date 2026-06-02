# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any

from system_context import current_time_context


_RUNTIME_TEXT = {
    "en": {
        "step": "Step",
        "task_language": "Task language",
        "task_language_name": "English",
        "user_task": "User task",
        "plan_goal": "Plan goal",
        "complexity": "Complexity",
        "execution_model": "Execution model",
        "current_progress": "Current progress",
        "plan": "Plan",
        "completed_steps": "Completed plan steps",
        "success_criteria": "Success criteria",
        "required_artifacts": "Required artifacts",
        "missing_artifacts": "Still missing",
        "verification_evidence": "Verified checks",
        "completion_blockers": "Completion blockers",
        "allowed_next_actions": "Allowed next actions now",
        "forbidden_next_actions": "Forbidden next actions now",
        "dependency_versions": "Known dependency versions",
        "web_facts": "Short external facts",
        "compressed_state": "Compressed state",
        "read_files": "Read files",
        "changed_files": "Changed files",
        "recent_observations": "Recent useful observations",
        "recent_results": "Recent action results",
        "recent_details": "Detailed recent action data",
        "repeated_errors": "Repeated errors",
        "additional_instruction": "Additional instruction",
        "none": "none",
        "no_context": "No accumulated context yet.",
        "truth_instruction": (
            "Use detailed data as the source of truth. Choose the next action so it moves "
            "the task forward and does not repeat dead-end steps."
        ),
        "completion_instruction": (
            "If completion blockers exist, final_answer is forbidden. "
            "If verification finds a gap, the next step must be a corrective action, not a summary of the problem."
        ),
        "language_instruction": (
            "Write public JSON fields thought, user_message, and final_answer.args.message only in English. "
            "If normalized_error is present, use it for the next step; do not copy raw_error into public text."
        ),
    },
}


def build_runtime_prompt(self, user_goal: str, step: int) -> str:
    assert self._state.plan_info is not None
    text = _RUNTIME_TEXT["en"]

    plan_text = "\n".join(
        f"{index + 1}. {item}"
        for index, item in enumerate(self._state.plan_info.plan)
    )
    success_text = "\n".join(
        f"- {item}"
        for item in self._state.plan_info.success_criteria
    )
    recent_feedback = "\n".join(
        self._state.recent_feedback[-self.config.recent_feedback_limit :]
    )
    recent_events = "\n".join(
        self._state.recent_events[-self.config.summary_recent_events :]
    )
    detailed_results = "\n\n".join(
        self._state.recent_result_details[-self.config.detailed_feedback_limit :]
    )
    repeated_errors = "\n".join(
        f"- {error} x{count}"
        for error, count in list(self._state.error_patterns.items())[-5:]
    )
    files_read = ", ".join(self._state.files_read[-8:]) or text["none"]
    files_changed = ", ".join(self._state.files_changed[-8:]) or text["none"]
    completed_steps = ", ".join(sorted(self._state.completed_plan_steps)) or text["none"]
    required_artifacts = ", ".join(self._state.required_artifacts[-12:]) or text["none"]
    missing_artifacts = ", ".join(self._state.missing_artifacts[-12:]) or text["none"]
    verification_evidence = "\n".join(f"- {item}" for item in self._state.verification_evidence[-6:]) or text["none"]
    completion_blockers = "\n".join(f"- {item}" for item in self._state.completion_blockers[-6:]) or text["none"]
    pending_instruction = self._state.pending_instruction or text["none"]
    allowed_next_actions = ", ".join(self._state.required_next_actions) or text["none"]
    forbidden_next_actions = text["none"]
    if self._state.required_next_actions:
        forbidden = sorted({
            "final_answer",
            "read_file",
            "read_multiple_files",
            "write_file",
            "append_file",
            "replace_in_file",
            "list_dir",
            "list_tree",
            "find_files",
            "search_code",
            "git_status",
            "git_diff",
            "run_shell",
            "web_search",
            "fetch_url",
        } - set(self._state.required_next_actions))
        forbidden_next_actions = ", ".join(forbidden) or text["none"]
    summary = self._build_summary("en")
    dependency_versions = self._format_dependency_versions_for_prompt("en")
    web_facts = self._format_web_facts_for_prompt("en")

    current_time = current_time_context()["current_time"]
    system_block = (
        f"System environment:\n"
        f"- OS: {self._state.system_os}\n"
        f"- Shell: {self._state.system_shell}\n"
        f"- Current datetime: {current_time}\n\n"
    )

    return (
        system_block +
        f"{text['step']}: {step}\n"
        f"{text['task_language']}: {text['task_language_name']}\n\n"
        f"{text['user_task']}:\n{user_goal}\n\n"
        f"{text['plan_goal']}:\n{self._state.plan_info.goal}\n\n"
        f"{text['complexity']}: {self._state.plan_info.complexity}\n"
        f"{text['execution_model']}: {self._state.selected_action_role or 'n/a'} / {self._state.selected_action_model or 'n/a'}\n"
        f"{text['current_progress']}: {self._state.progress}%\n\n"
        f"{text['plan']}:\n{plan_text}\n\n"
        f"{text['completed_steps']}: {completed_steps}\n\n"
        f"{text['success_criteria']}:\n{success_text}\n\n"
        f"{text['required_artifacts']}: {required_artifacts}\n"
        f"{text['missing_artifacts']}: {missing_artifacts}\n\n"
        f"{text['verification_evidence']}:\n{verification_evidence}\n\n"
        f"{text['completion_blockers']}:\n{completion_blockers}\n\n"
        f"{text['allowed_next_actions']}: {allowed_next_actions}\n"
        f"{text['forbidden_next_actions']}: {forbidden_next_actions}\n\n"
        f"{text['dependency_versions']}:\n{dependency_versions}\n\n"
        f"{text['web_facts']}:\n{web_facts}\n\n"
        f"{text['compressed_state']}:\n{summary or text['no_context']}\n\n"
        f"{text['read_files']}: {files_read}\n"
        f"{text['changed_files']}: {files_changed}\n\n"
        f"{text['recent_observations']}:\n{recent_events or text['none']}\n\n"
        f"{text['recent_results']}:\n{recent_feedback or text['none']}\n\n"
        f"{text['recent_details']}:\n{detailed_results or text['none']}\n\n"
        f"{text['repeated_errors']}:\n{repeated_errors or text['none']}\n\n"
        f"{text['additional_instruction']}:\n{pending_instruction}\n\n"
        f"{text['language_instruction']}\n\n"
        f"{text['completion_instruction']}\n\n"
        f"{text['truth_instruction']}"
    )


def format_dependency_versions_for_prompt(self, language: str = "en") -> str:
    if not self._state.dependency_versions:
        return "version not found in requirements.txt, pyproject.toml, poetry.lock, uv.lock"

    lines = []
    items = sorted(self._state.dependency_versions.items())
    for package, version in items[:20]:
        lines.append(f"- {package}: {version}")
    if len(items) > 20:
        lines.append(f"- ... {len(items) - 20} more")
    return "\n".join(lines)


def format_web_facts_for_prompt(self, language: str = "en") -> str:
    if not self._state.web_facts:
        return "none"
    return "\n".join(f"- {item}" for item in self._state.web_facts[-6:])


def compact_for_prompt(self, value: Any, depth: int = 0) -> Any:
    if depth >= 4:
        return "[TRUNCATED_DEPTH]"

    if isinstance(value, str):
        return self._truncate_for_prompt(value, self.config.prompt_value_char_limit)

    if isinstance(value, list):
        limit = 12
        compacted = [self._compact_for_prompt(item, depth + 1) for item in value[:limit]]
        if len(value) > limit:
            compacted.append(f"[TRUNCATED_LIST: showing first {limit} of {len(value)}]")
        return compacted

    if isinstance(value, dict):
        return {
            key: self._compact_for_prompt(item, depth + 1)
            for key, item in value.items()
        }

    return value


def truncate_for_prompt(self, text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[TRUNCATED_FOR_PROMPT: showing first {limit} characters of {len(text)}]"


def build_summary(self, language: str = "en") -> str:
    parts = []

    if self._state.plan_info:
        parts.append(
            f"Completed plan steps: {len(self._state.completed_plan_steps)}/{len(self._state.plan_info.plan)}"
        )

    if self._state.files_read:
        parts.append(f"Read: " + ", ".join(self._state.files_read[-6:]))

    if self._state.files_changed:
        parts.append(f"Changed: " + ", ".join(self._state.files_changed[-6:]))

    if self._state.required_artifacts:
        parts.append(f"Required artifacts: {len(self._state.required_artifacts)}")

    if self._state.missing_artifacts:
        parts.append(f"Missing: " + ", ".join(self._state.missing_artifacts[-6:]))

    if self._state.verification_evidence:
        parts.append(f"Verification: " + " | ".join(self._state.verification_evidence[-3:]))

    if self._state.dependency_versions:
        parts.append(f"Dependency versions found: {len(self._state.dependency_versions)}")

    if self._state.web_facts:
        parts.append(f"Web facts: " + " | ".join(self._state.web_facts[-2:]))

    if self._state.recent_events:
        parts.append(f"Observations: " + " | ".join(self._state.recent_events[-4:]))

    if self._state.error_history:
        parts.append(f"Errors: " + " | ".join(self._state.error_history[-3:]))

    if self._state.completion_blockers:
        parts.append(f"Blockers: " + " | ".join(self._state.completion_blockers[-3:]))

    if parts:
        return "\n".join(parts)
    return "Context has not accumulated yet."
