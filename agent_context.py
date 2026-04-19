# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any


def build_runtime_prompt(self, user_goal: str, step: int) -> str:
    assert self._state.plan_info is not None

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
    files_read = ", ".join(self._state.files_read[-8:]) or "нет"
    files_changed = ", ".join(self._state.files_changed[-8:]) or "нет"
    completed_steps = ", ".join(sorted(self._state.completed_plan_steps)) or "нет"
    pending_instruction = self._state.pending_instruction or "нет"
    summary = self._state.summary or "Пока нет накопленного контекста."
    dependency_versions = self._format_dependency_versions_for_prompt()
    web_facts = self._format_web_facts_for_prompt()

    return (
        f"Шаг: {step}\n"
        f"Задача пользователя:\n{user_goal}\n\n"
        f"Цель по плану:\n{self._state.plan_info.goal}\n\n"
        f"Сложность: {self._state.plan_info.complexity}\n"
        f"Текущий прогресс: {self._state.progress}%\n\n"
        f"План:\n{plan_text}\n\n"
        f"Уже завершённые шаги плана: {completed_steps}\n\n"
        f"Критерии успеха:\n{success_text}\n\n"
        f"Известные версии зависимостей:\n{dependency_versions}\n\n"
        f"Краткие внешние факты:\n{web_facts}\n\n"
        f"Сжатое состояние:\n{summary}\n\n"
        f"Прочитанные файлы: {files_read}\n"
        f"Изменённые файлы: {files_changed}\n\n"
        f"Последние полезные наблюдения:\n{recent_events or 'нет'}\n\n"
        f"Последние результаты действий:\n{recent_feedback or 'нет'}\n\n"
        f"Подробные данные недавних действий:\n{detailed_results or 'нет'}\n\n"
        f"Повторяющиеся ошибки:\n{repeated_errors or 'нет'}\n\n"
        f"Дополнительное указание:\n{pending_instruction}\n\n"
        "Опирайся на подробные данные как на источник истины. Выбери следующее действие так, чтобы продвинуть задачу и не повторять тупиковые шаги."
    )


def format_dependency_versions_for_prompt(self) -> str:
    if not self._state.dependency_versions:
        return "версия не найдена в requirements.txt, pyproject.toml, poetry.lock, uv.lock"

    lines = []
    items = sorted(self._state.dependency_versions.items())
    for package, version in items[:20]:
        lines.append(f"- {package}: {version}")
    if len(items) > 20:
        lines.append(f"- ... ещё {len(items) - 20}")
    return "\n".join(lines)


def format_web_facts_for_prompt(self) -> str:
    if not self._state.web_facts:
        return "нет"
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
            compacted.append(f"[TRUNCATED_LIST: показаны первые {limit} из {len(value)}]")
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
    return text[:limit] + f"\n[TRUNCATED_FOR_PROMPT: показаны первые {limit} символов из {len(text)}]"


def build_summary(self) -> str:
    parts = []

    if self._state.plan_info:
        parts.append(
            f"Плановых шагов выполнено: {len(self._state.completed_plan_steps)}/{len(self._state.plan_info.plan)}"
        )

    if self._state.files_read:
        parts.append("Прочитано: " + ", ".join(self._state.files_read[-6:]))

    if self._state.files_changed:
        parts.append("Изменено: " + ", ".join(self._state.files_changed[-6:]))

    if self._state.dependency_versions:
        parts.append(f"Версий зависимостей найдено: {len(self._state.dependency_versions)}")

    if self._state.web_facts:
        parts.append("Web-факты: " + " | ".join(self._state.web_facts[-2:]))

    if self._state.recent_events:
        parts.append("Наблюдения: " + " | ".join(self._state.recent_events[-4:]))

    if self._state.error_history:
        parts.append("Ошибки: " + " | ".join(self._state.error_history[-3:]))

    return "\n".join(parts) if parts else "Контекст ещё не накоплен."