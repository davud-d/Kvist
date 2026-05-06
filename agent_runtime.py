# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from typing import Any

from agent_state import RunState
from executor import ExecutorError
from parser import CommandValidationError, parse_agent_command
from protocol import AgentCommand

_READ_ONLY_ACTIONS = {
    "read_file", "read_multiple_files", "list_dir",
    "list_tree", "find_files", "search_code",
}


def run_loop(self, user_goal: str, start_step: int) -> str:
    self._ui.start_live()

    final_message = ""
    try:
        for step in range(start_step, self.config.max_steps + 1):
            early_stop_message = self._build_early_stop_message(step)
            if early_stop_message:
                final_message = early_stop_message
                break

            prompt = self._build_runtime_prompt(user_goal, step)
            raw_text = self._call_action_model(prompt)

            try:
                command = parse_agent_command(raw_text)
            except CommandValidationError as error:
                self._register_parse_error(step, raw_text, str(error))
                self._ui.update_live(
                    user_message="clarifying step format and continuing",
                    status="fixing response format",
                    step=step,
                    progress=self._state.progress,
                    plan_step="format correction",
                    is_error=True,
                )
                continue

            language_error = self._command_language_error(command)
            if language_error:
                self._register_language_error(step, command, language_error)
                self._ui.update_live(
                    user_message=(
                        "Исправляю язык ответа модели и повторяю шаг"
                        if self._state.task_language == "ru"
                        else "Fixing the model response language and retrying the step"
                    ),
                    status="fixing response language",
                    step=step,
                    progress=self._state.progress,
                    plan_step="language correction",
                    is_error=True,
                )
                continue

            self._record_user_message(command.user_message)

            if self._is_repeat_blocked(command):
                self._ui.update_live(
                    user_message=command.user_message,
                    status="looking for another working step",
                    step=step,
                    progress=self._state.progress,
                    plan_step=command.plan_step,
                    is_error=True,
                )
                continue

            self._ui.print_step_header(step, command)

            if command.action.type == "web_search":
                web_error = self._prepare_web_search_command(user_goal, command)
                if web_error:
                    feedback = self._build_error_feedback("web_search", web_error)
                    self._register_feedback(step, command, feedback)
                    self._ui.update_live(
                        user_message=command.user_message,
                        status="web search limit reached",
                        step=step,
                        progress=self._state.progress,
                        plan_step=command.plan_step,
                        is_error=True,
                    )
                    continue

            if command.action.type == "fetch_url":
                fetch_error = self._prepare_fetch_url_command(command)
                if fetch_error:
                    feedback = self._build_error_feedback("fetch_url", fetch_error)
                    self._register_feedback(step, command, feedback)
                    self._ui.update_live(
                        user_message=command.user_message,
                        status="URL read limit reached",
                        step=step,
                        progress=self._state.progress,
                        plan_step=command.plan_step,
                        is_error=True,
                    )
                    continue

            self._ui.update_live(
                user_message=command.user_message,
                status="executing action",
                step=step,
                progress=command.progress,
                plan_step=command.plan_step,
            )

            if command.action.type == "final_answer":
                final_message = str(command.action.args["message"])
                self._register_final_answer(step, final_message)
                break

            feedback = self._execute_command(command)
            self._register_feedback(step, command, feedback)

            self._ui.update_live(
                user_message=command.user_message,
                status="continuing" if feedback.get("ok") else "error received",
                step=step,
                progress=self._state.progress,
                plan_step=command.plan_step,
                is_error=not bool(feedback.get("ok")),
            )

            self._save_checkpoint(user_goal, step)

        if not final_message:
            final_message = self._build_limit_reached_message()
    finally:
        self._ui.stop_live()
        self._persist_logs(user_goal)
        self._clear_checkpoint()

    if not self.config.verbose:
        self._ui.print_final(final_message, self._state.user_messages)

    return final_message


def reset_run_state(self) -> None:
    self._state = RunState()


def execute_command(self, command: AgentCommand) -> dict[str, Any]:
    try:
        result = self.executor.execute(command)
    except ExecutorError as error:
        return self._build_error_feedback(command.action.type, str(error))
    except Exception as error:
        raw_error = f"Unexpected executor error: {type(error).__name__}: {error}"
        return self._build_error_feedback(command.action.type, raw_error)

    if result.ok:
        return {
            "ok": True,
            "action_type": result.action_type,
            "data": result.data,
        }

    error_text = "Action failed"
    if isinstance(result.data, dict):
        error_text = str(result.data.get("error") or error_text)

    payload = self._build_error_feedback(result.action_type, error_text)
    if isinstance(result.data, dict):
        payload["data"] = result.data
    return payload


def build_error_feedback(
    self,
    action_type: str,
    raw_error: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "action_type": action_type,
        "error": raw_error,
        "raw_error": raw_error,
        "normalized_error": self._normalize_error_for_model(raw_error),
    }


def command_language_error(self, command: AgentCommand) -> str | None:
    language = self._state.task_language
    checks = [
        ("thought", command.thought),
        ("user_message", command.user_message),
    ]
    if command.action.type == "final_answer":
        checks.append(("final_answer.message", str(command.action.args.get("message", ""))))

    for field_name, value in checks:
        if not _matches_task_language(value, language):
            if language == "ru":
                return f"Поле {field_name} написано не на русском языке."
            return f"Field {field_name} is not written in English."
    return None


def register_language_error(
    self,
    step: int,
    command: AgentCommand,
    error_text: str,
) -> None:
    self._state.parse_errors += 1
    self._state.consecutive_errors += 1
    self._record_error(error_text)
    if self._state.task_language == "ru":
        self._state.pending_instruction = (
            f"{error_text} Предыдущий шаг отклонён и не исполнялся. "
            "Повтори тот же смысл шага, но напиши публичные JSON-поля thought, "
            "user_message и final_answer.args.message только на русском языке."
        )
    else:
        self._state.pending_instruction = (
            f"{error_text} The previous step was rejected and was not executed. "
            "Retry the same intended step, but write public JSON fields thought, "
            "user_message, and final_answer.args.message only in English."
        )
    self._state.step_logs.append(
        {
            "step": step,
            "status": "language_error",
            "error": error_text,
            "command": {
                "thought": command.thought,
                "plan_step": command.plan_step,
                "progress": command.progress,
                "user_message": command.user_message,
                "action": {
                    "type": command.action.type,
                    "args": command.action.args,
                },
            },
        }
    )
    self._ui.debug_print(f"\n[Step {step}] Language error: {error_text}")


def normalize_error_for_model(self, raw_error: str) -> str:
    error = str(raw_error).strip() or "Unknown error"
    if self._state.task_language != "ru":
        return error

    mappings = (
        ("File not found: ", "Файл не найден: "),
        ("Not a file: ", "Путь не является файлом: "),
        ("Path not found: ", "Путь не найден: "),
        ("Not a directory: ", "Путь не является директорией: "),
        ("Permission denied: ", "Отказано в доступе: "),
        ("Unsupported action: ", "Неподдерживаемое действие: "),
        ("Unexpected executor error: ", "Неожиданная ошибка executor: "),
        ("Network error when requesting ", "Сетевая ошибка при запросе "),
        ("Timeout when requesting ", "Таймаут при запросе "),
    )
    for prefix, translated in mappings:
        if error.startswith(prefix):
            return translated + error[len(prefix):]

    exact = {
        "Action failed": "Действие не выполнено.",
        "Unknown error": "Неизвестная ошибка.",
        "Invalid URL": "Некорректный URL.",
        "Empty query for web_search": "Пустой запрос для web_search.",
        "old_text fragment not found in file": "Фрагмент old_text не найден в файле.",
        "Path must be a string": "Путь должен быть строкой.",
        "run_shell.command must be a non-empty array of strings": (
            "run_shell.command должен быть непустым списком строк."
        ),
    }
    if error in exact:
        return exact[error]
    if error.startswith("HTTP "):
        return "HTTP-ошибка при выполнении запроса."
    return "Действие не выполнено. Подробности сохранены в raw_error."


def feedback_error_for_model(self, feedback: dict[str, Any]) -> str:
    normalized = feedback.get("normalized_error")
    if isinstance(normalized, str) and normalized.strip():
        return normalized.strip()
    raw = (
        feedback.get("raw_error")
        or feedback.get("error")
        or feedback.get("data", {}).get("stderr")
        or "Unknown error"
    )
    return self._normalize_error_for_model(str(raw))


def register_parse_error(self, step: int, raw_text: str, error_text: str) -> None:
    self._state.parse_errors += 1
    self._state.consecutive_errors += 1
    if self._state.task_language == "ru":
        message = f"Невалидный ответ модели: {error_text}"
    else:
        message = "Invalid model response."
    self._record_error(message)
    if self._state.task_language == "ru":
        self._state.pending_instruction = (
            "Предыдущий ответ был невалидным JSON. "
            f"Ошибка: {error_text}. Верни ровно один JSON-объект по схеме."
        )
    else:
        self._state.pending_instruction = (
            "The previous response was invalid JSON. "
            "Return exactly one JSON object matching the schema."
        )
    self._state.step_logs.append(
        {
            "step": step,
            "status": "parse_error",
            "error": error_text,
        }
    )
    self._ui.debug_print(f"\n[Step {step}] Format error: {error_text}")


def register_feedback(
    self,
    step: int,
    command: AgentCommand,
    feedback: dict[str, Any],
) -> None:
    previous_progress = self._state.progress
    ok = bool(feedback.get("ok"))
    summary_line = self._feedback_to_line(command, feedback)
    detail_line = self._feedback_to_detail(command, feedback)
    signature = self._action_signature(command)
    repeat_count = self._state.seen_actions.get(signature, 1)

    if ok:
        self._state.consecutive_errors = 0
        self._state.pending_instruction = ""
        self._update_state_from_success(command, feedback)
    else:
        self._state.consecutive_errors += 1
        error_text = self._feedback_error_for_model(feedback)
        self._record_error(error_text)
        if self._state.task_language == "ru":
            self._state.pending_instruction = (
                "Предыдущее действие не выполнено. "
                f"normalized_error: {error_text}. Не повторяй тот же шаг без новой идеи."
            )
        else:
            self._state.pending_instruction = (
                "The previous action failed. "
                f"normalized_error: {error_text}. Do not repeat the same step without a new idea."
            )

    self._update_progress(command, ok)
    if not ok:
        self._state.stagnation_count += 1
    elif self._state.progress > previous_progress:
        self._state.stagnation_count = 0
    elif repeat_count > 1 and command.action.type not in _READ_ONLY_ACTIONS:
        self._state.stagnation_count += 1
    else:
        self._state.stagnation_count = max(0, self._state.stagnation_count - 1)

    self._state.recent_feedback.append(summary_line)
    self._state.recent_feedback = self._state.recent_feedback[-self.config.recent_feedback_limit :]
    self._state.recent_events.append(summary_line)
    self._state.recent_events = self._state.recent_events[-self.config.summary_recent_events :]
    self._state.recent_result_details.append(detail_line)
    self._state.recent_result_details = self._state.recent_result_details[-self.config.detailed_feedback_limit :]
    self._state.summary = self._build_summary()

    self._state.step_logs.append(
        {
            "step": step,
            "status": "ok" if ok else "error",
            "progress": self._state.progress,
            "command": {
                "thought": command.thought,
                "plan_step": command.plan_step,
                "progress": command.progress,
                "user_message": command.user_message,
                "action": {
                    "type": command.action.type,
                    "args": command.action.args,
                },
            },
            "feedback": feedback,
        }
    )

    self._ui.debug_print(self._render_feedback(summary_line, feedback))


def update_state_from_success(
    self,
    command: AgentCommand,
    feedback: dict[str, Any],
) -> None:
    action_type = command.action.type
    data = feedback.get("data", {})

    if command.plan_step in (self._state.plan_info.plan if self._state.plan_info else []):
        self._state.completed_plan_steps.add(command.plan_step)

    if action_type == "read_file":
        path = str(data.get("path", "")).strip()
        self._push_unique(self._state.files_read, path)

    elif action_type == "read_multiple_files":
        for file_info in data.get("files", []):
            path = str(file_info.get("path", "")).strip()
            self._push_unique(self._state.files_read, path)

    elif action_type in {"write_file", "append_file", "replace_in_file"}:
        path = str(data.get("path", "")).strip()
        self._push_unique(self._state.files_changed, path)
        if self._state.web_facts:
            self._state.applied_facts.append({
                "path": path,
                "fact": self._state.web_facts[-1],
            })
            del self._state.applied_facts[:-20]

    elif action_type == "search_code":
        results = data.get("results", [])
        if results:
            top = results[0]
            observation = (
                f"Found code for query '{data.get('query', '')}' in {top.get('path')}:{top.get('line')}"
            )
            self._push_bounded(
                self._state.recent_events,
                observation,
                self.config.summary_recent_events,
            )

    elif action_type == "find_files":
        matches = data.get("matches", [])
        if matches:
            observation = (
                f"Found {len(matches)} files matching pattern '{data.get('pattern', '')}'"
            )
            self._push_bounded(
                self._state.recent_events,
                observation,
                self.config.summary_recent_events,
            )

    elif action_type == "web_search":
        provider = str(data.get("provider", "")).strip()
        query = str(data.get("query", "")).strip()
        results = data.get("results", [])
        sources: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            url = str(item.get("url", "")).strip()
            if url:
                sources.append(url)
                self._push_unique(self._state.web_sources, url, limit=20)

        summary = self._summarize_search_results(results)
        if summary:
            self._push_unique(
                self._state.web_facts,
                f"web_search ({provider}): {summary}",
                limit=20,
            )

        self._state.web_search_history.append(
            {
                "provider": provider,
                "query": query,
                "sources": sources,
                "summary": summary,
            }
        )
        del self._state.web_search_history[:-20]

    elif action_type == "fetch_url":
        url = str(data.get("url", "")).strip()
        title = str(data.get("title", "")).strip()
        content = str(data.get("content", ""))
        if url:
            self._push_unique(self._state.web_sources, url, limit=20)
        fact = self._extract_fact_from_content(url=url, title=title, content=content)
        if fact:
            self._push_unique(self._state.web_facts, fact, limit=20)


def update_progress(self, command: AgentCommand, ok: bool) -> None:
    plan_progress = self._plan_progress()
    target_progress = max(self._state.progress, plan_progress)

    if ok:
        if command.action.type in {"write_file", "append_file", "replace_in_file"}:
            target_progress = max(target_progress, command.progress)
        else:
            target_progress = max(target_progress, min(command.progress, 99))

    self._state.last_progress = self._state.progress
    self._state.progress = max(0, min(target_progress, 99))


def register_final_answer(self, step: int, message: str) -> None:
    self._state.progress = 100
    self._state.step_logs.append(
        {
            "step": step,
            "status": "final_answer",
            "progress": 100,
            "message": message,
        }
    )
    self._ui.debug_print(f"Result: final answer at step {step}")


def plan_progress(self) -> int:
    if not self._state.plan_info or not self._state.plan_info.plan:
        return self._state.progress
    done = len(self._state.completed_plan_steps)
    total = len(self._state.plan_info.plan)
    return round(done / total * 100)


def record_error(self, error_text: str) -> None:
    self._state.error_history.append(error_text)
    self._state.error_history = self._state.error_history[-5:]
    self._state.error_patterns[error_text] = self._state.error_patterns.get(error_text, 0) + 1


def feedback_to_line(self, command: AgentCommand, feedback: dict[str, Any]) -> str:
    action_type = command.action.type
    if feedback.get("ok"):
        data = feedback.get("data", {})
        if action_type == "read_file":
            path = data.get("path", "")
            return f"ok: read file {path}"
        if action_type == "read_multiple_files":
            count = len(data.get("files", []))
            return f"ok: read {count} files"
        if action_type == "write_file":
            path = data.get("path", "")
            return f"ok: wrote file {path}"
        if action_type == "append_file":
            path = data.get("path", "")
            return f"ok: appended to file {path}"
        if action_type == "replace_in_file":
            path = data.get("path", "")
            count = data.get("occurrences_replaced", 0)
            return f"ok: replaced fragment in {path}, replacements {count}"
        if action_type == "list_dir":
            count = len(data.get("items", []))
            return f"ok: listed directory, items {count}"
        if action_type == "list_tree":
            truncated = bool(data.get("truncated"))
            suffix = ", tree truncated" if truncated else ""
            return f"ok: got file tree{suffix}"
        if action_type == "find_files":
            count = len(data.get("matches", []))
            return f"ok: found {count} files"
        if action_type == "search_code":
            count = len(data.get("results", []))
            return f"ok: found {count} code matches"
        if action_type == "git_status":
            return "ok: got git status"
        if action_type == "git_diff":
            return "ok: got git diff"
        if action_type == "run_shell":
            command_text = " ".join(data.get("command", []))
            return f"ok: executed shell command {command_text}"
        if action_type == "web_search":
            provider = data.get("provider", "unknown")
            count = len(data.get("results", []))
            return f"ok: web_search via {provider}, found {count} sources"
        if action_type == "fetch_url":
            url = data.get("url", "")
            truncated = bool(data.get("truncated"))
            suffix = ", content truncated" if truncated else ""
            return f"ok: read source {url}{suffix}"
        return f"ok: executed action {action_type}"

    error_text = self._feedback_error_for_model(feedback)
    return f"error: {action_type}: {error_text}"


def feedback_to_detail(self, command: AgentCommand, feedback: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "action_type": command.action.type,
        "ok": bool(feedback.get("ok")),
    }

    if feedback.get("ok"):
        payload["data"] = self._compact_for_prompt(feedback.get("data", {}))
    else:
        payload["normalized_error"] = self._feedback_error_for_model(feedback)

    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    return self._truncate_for_prompt(serialized, self.config.prompt_result_char_limit)


def render_feedback(self, summary_line: str, feedback: dict[str, Any]) -> str:
    ok = bool(feedback.get("ok"))
    status = "OK" if ok else "ERROR"
    raw_suffix = ""
    if not ok:
        raw_error = str(feedback.get("raw_error") or feedback.get("error") or "").strip()
        normalized_error = self._feedback_error_for_model(feedback)
        if raw_error and raw_error != normalized_error:
            raw_suffix = f" Raw error: {raw_error}."
    return (
        f"Result: {status}. {summary_line}. "
        f"Progress: {self._state.progress}%. "
        f"Stagnation: {self._state.stagnation_count}."
        f"{raw_suffix}"
    )


def build_early_stop_message(self, step: int) -> str | None:
    if self._state.consecutive_errors >= self.config.max_consecutive_errors:
        if self._state.task_language == "ru":
            return (
                "Агент остановлен: слишком много ошибок подряд. "
                f"Последние ошибки: {', '.join(self._state.error_history[-3:])}"
            )
        return (
            "Agent stopped. Too many consecutive errors. "
            f"Last errors: {', '.join(self._state.error_history[-3:])}"
        )

    if self._state.stagnation_count >= self.config.stagnation_limit:
        if self._state.task_language == "ru":
            return (
                "Агент остановлен: прогресс не меняется, возможен цикл. "
                f"Последние действия: {', '.join(self._state.recent_feedback[-3:])}"
            )
        return (
            "Agent stopped. No progress detected, possible loop. "
            f"Last actions: {', '.join(self._state.recent_feedback[-3:])}"
        )

    if step > self.config.max_steps:
        if self._state.task_language == "ru":
            return "Агент остановлен: достигнут лимит шагов."
        return "Agent stopped: step limit reached."

    return None


def build_limit_reached_message(self) -> str:
    errors = ", ".join(self._state.error_history[-3:]) or "none"
    if self._state.task_language == "ru":
        errors = ", ".join(self._state.error_history[-3:]) or "нет"
        return (
            "Агент не завершил задачу в пределах лимита шагов. "
            f"Текущий прогресс: {self._state.progress}%. "
            f"Последние ошибки: {errors}."
        )
    return (
        "Agent did not complete the task within the step limit. "
        f"Current progress: {self._state.progress}%. "
        f"Last errors: {errors}."
    )


def is_repeat_blocked(self, command: AgentCommand) -> bool:
    signature = self._action_signature(command)
    seen_count = self._state.seen_actions.get(signature, 0)
    if seen_count >= self.config.repeat_action_limit:
        if self._state.task_language == "ru":
            message = (
                "Ты повторяешь бесполезное действие с теми же аргументами. "
                "Выбери другой шаг: другой файл, другой поиск или честно заверши задачу."
            )
        else:
            message = (
                "You are repeating a useless action with the same args. "
                "Choose a different step: a different file, a different search, or honestly complete the task."
            )
        self._state.pending_instruction = message
        self._state.stagnation_count += 1
        self._state.step_logs.append(
            {
                "status": "repeat_blocked",
                "command": {
                    "plan_step": command.plan_step,
                    "progress": command.progress,
                    "user_message": command.user_message,
                    "action": {
                        "type": command.action.type,
                        "args": command.action.args,
                    },
                },
            }
        )
        self._ui.debug_print(f"Repeat blocked: {command.action.type} {command.action.args}")
        return True

    self._state.seen_actions[signature] = seen_count + 1
    self._state.last_action_signature = signature
    return False


def action_signature(self, command: AgentCommand) -> str:
    return json.dumps(
        {
            "type": command.action.type,
            "args": command.action.args,
        },
        ensure_ascii=False,
        sort_keys=True,
    )


def record_user_message(self, message: str) -> None:
    clean = message.strip()
    if not clean:
        return
    self._state.user_messages.append(clean)
    del self._state.user_messages[:-80]


def push_unique(self, items: list[str], value: str, limit: int = 8) -> None:
    if not value:
        return
    if value in items:
        items.remove(value)
    items.append(value)
    del items[:-limit]


def push_bounded(self, items: list[str], value: str, limit: int) -> None:
    if not value:
        return
    items.append(value)
    del items[:-limit]


def _matches_task_language(text: str, language: str) -> bool:
    if language not in {"ru", "en"}:
        return True
    cyrillic = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if language == "ru":
        return cyrillic > 0
    return cyrillic <= latin
