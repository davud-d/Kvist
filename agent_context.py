# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any


def detect_request_language(text: str) -> str:
    cyrillic = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    return "ru" if cyrillic > latin else "en"


_RUNTIME_TEXT = {
    "ru": {
        "step": "Шаг",
        "user_task": "Задача пользователя",
        "plan_goal": "Цель по плану",
        "complexity": "Сложность",
        "current_progress": "Текущий прогресс",
        "plan": "План",
        "completed_steps": "Выполненные пункты плана",
        "success_criteria": "Критерии успеха",
        "dependency_versions": "Известные версии зависимостей",
        "web_facts": "Короткие внешние факты",
        "compressed_state": "Сжатое состояние",
        "read_files": "Прочитанные файлы",
        "changed_files": "Изменённые файлы",
        "recent_observations": "Последние полезные наблюдения",
        "recent_results": "Последние результаты действий",
        "recent_details": "Подробные данные последних действий",
        "repeated_errors": "Повторяющиеся ошибки",
        "additional_instruction": "Дополнительная инструкция",
        "none": "нет",
        "no_context": "Пока нет накопленного контекста.",
        "truth_instruction": (
            "Опирайся на подробные данные как на источник истины. "
            "Выбери следующее действие так, чтобы продвинуть задачу и не повторять тупиковые шаги."
        ),
    },
    "en": {
        "step": "Step",
        "user_task": "User task",
        "plan_goal": "Plan goal",
        "complexity": "Complexity",
        "current_progress": "Current progress",
        "plan": "Plan",
        "completed_steps": "Completed plan steps",
        "success_criteria": "Success criteria",
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
    },
}


def build_runtime_prompt(self, user_goal: str, step: int) -> str:
    assert self._state.plan_info is not None
    language = detect_request_language(user_goal)
    text = _RUNTIME_TEXT[language]

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
    pending_instruction = self._state.pending_instruction or text["none"]
    summary = self._build_summary(language)
    dependency_versions = self._format_dependency_versions_for_prompt(language)
    web_facts = self._format_web_facts_for_prompt(language)

    return (
        f"{text['step']}: {step}\n"
        f"{text['user_task']}:\n{user_goal}\n\n"
        f"{text['plan_goal']}:\n{self._state.plan_info.goal}\n\n"
        f"{text['complexity']}: {self._state.plan_info.complexity}\n"
        f"{text['current_progress']}: {self._state.progress}%\n\n"
        f"{text['plan']}:\n{plan_text}\n\n"
        f"{text['completed_steps']}: {completed_steps}\n\n"
        f"{text['success_criteria']}:\n{success_text}\n\n"
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
        f"{text['truth_instruction']}"
    )


def format_dependency_versions_for_prompt(self, language: str = "en") -> str:
    if not self._state.dependency_versions:
        if language == "ru":
            return "версия не найдена в requirements.txt, pyproject.toml, poetry.lock, uv.lock"
        return "version not found in requirements.txt, pyproject.toml, poetry.lock, uv.lock"

    lines = []
    items = sorted(self._state.dependency_versions.items())
    for package, version in items[:20]:
        lines.append(f"- {package}: {version}")
    if len(items) > 20:
        if language == "ru":
            lines.append(f"- ... ещё {len(items) - 20}")
        else:
            lines.append(f"- ... {len(items) - 20} more")
    return "\n".join(lines)


def format_web_facts_for_prompt(self, language: str = "en") -> str:
    if not self._state.web_facts:
        return "нет" if language == "ru" else "none"
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
        if language == "ru":
            parts.append(
                f"Плановых шагов выполнено: {len(self._state.completed_plan_steps)}/{len(self._state.plan_info.plan)}"
            )
        else:
            parts.append(
                f"Completed plan steps: {len(self._state.completed_plan_steps)}/{len(self._state.plan_info.plan)}"
            )

    if self._state.files_read:
        label = "Прочитано" if language == "ru" else "Read"
        parts.append(f"{label}: " + ", ".join(self._state.files_read[-6:]))

    if self._state.files_changed:
        label = "Изменено" if language == "ru" else "Changed"
        parts.append(f"{label}: " + ", ".join(self._state.files_changed[-6:]))

    if self._state.dependency_versions:
        if language == "ru":
            parts.append(f"Версий зависимостей найдено: {len(self._state.dependency_versions)}")
        else:
            parts.append(f"Dependency versions found: {len(self._state.dependency_versions)}")

    if self._state.web_facts:
        label = "Web-факты" if language == "ru" else "Web facts"
        parts.append(f"{label}: " + " | ".join(self._state.web_facts[-2:]))

    if self._state.recent_events:
        label = "Наблюдения" if language == "ru" else "Observations"
        parts.append(f"{label}: " + " | ".join(self._state.recent_events[-4:]))

    if self._state.error_history:
        label = "Ошибки" if language == "ru" else "Errors"
        parts.append(f"{label}: " + " | ".join(self._state.error_history[-3:]))

    if parts:
        return "\n".join(parts)
    return "Контекст ещё не накоплен." if language == "ru" else "Context has not accumulated yet."
