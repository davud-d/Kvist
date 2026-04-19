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
                    user_message="я уточняю формат шага и продолжаю работу",
                    status="исправляю формат ответа",
                    step=step,
                    progress=self._state.progress,
                    plan_step="корректировка формата",
                    is_error=True,
                )
                continue

            self._record_user_message(command.user_message)

            if self._is_repeat_blocked(command):
                self._ui.update_live(
                    user_message=command.user_message,
                    status="ищу другой рабочий шаг",
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
                    feedback = {
                        "ok": False,
                        "action_type": "web_search",
                        "error": web_error,
                    }
                    self._register_feedback(step, command, feedback)
                    self._ui.update_live(
                        user_message=command.user_message,
                        status="сработало ограничение web-поиска",
                        step=step,
                        progress=self._state.progress,
                        plan_step=command.plan_step,
                        is_error=True,
                    )
                    continue

            if command.action.type == "fetch_url":
                fetch_error = self._prepare_fetch_url_command(command)
                if fetch_error:
                    feedback = {
                        "ok": False,
                        "action_type": "fetch_url",
                        "error": fetch_error,
                    }
                    self._register_feedback(step, command, feedback)
                    self._ui.update_live(
                        user_message=command.user_message,
                        status="сработало ограничение чтения URL",
                        step=step,
                        progress=self._state.progress,
                        plan_step=command.plan_step,
                        is_error=True,
                    )
                    continue

            self._ui.update_live(
                user_message=command.user_message,
                status="выполняю действие",
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
                status="шаг завершен" if feedback.get("ok") else "получена ошибка",
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
        return {
            "ok": False,
            "action_type": command.action.type,
            "error": str(error),
        }
    except Exception as error:
        return {
            "ok": False,
            "action_type": command.action.type,
            "error": f"Неожиданная ошибка executor: {error}",
        }

    if result.ok:
        return {
            "ok": True,
            "action_type": result.action_type,
            "data": result.data,
        }

    error_text = "Действие завершилось ошибкой"
    if isinstance(result.data, dict):
        error_text = str(result.data.get("error") or error_text)

    payload: dict[str, Any] = {
        "ok": False,
        "action_type": result.action_type,
        "error": error_text,
    }
    if isinstance(result.data, dict):
        payload["data"] = result.data
    return payload


def register_parse_error(self, step: int, raw_text: str, error_text: str) -> None:
    self._state.parse_errors += 1
    self._state.consecutive_errors += 1
    message = f"Невалидный ответ модели: {error_text}"
    self._record_error(message)
    self._state.pending_instruction = (
        "Прошлый ответ был невалидным JSON. "
        f"Ошибка: {error_text}. Верни строго один JSON-объект по схеме."
    )
    self._state.step_logs.append(
        {
            "step": step,
            "status": "parse_error",
            "error": error_text,
        }
    )
    self._ui.debug_print(f"\n[Шаг {step}] Ошибка формата: {error_text}")


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
        error_text = str(
            feedback.get("error")
            or feedback.get("data", {}).get("stderr")
            or "Неизвестная ошибка"
        )
        self._record_error(error_text)
        self._state.pending_instruction = (
            "Прошлое действие завершилось ошибкой. "
            f"Ошибка: {error_text}. Не повторяй тот же шаг без новой идеи."
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
                f"Найден код по запросу '{data.get('query', '')}' в {top.get('path')}:{top.get('line')}"
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
                f"Найдено файлов по шаблону '{data.get('pattern', '')}': {len(matches)}"
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
    self._ui.debug_print(f"Результат: финальный ответ на шаге {step}")


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
            return f"ok: прочитан файл {path}"
        if action_type == "read_multiple_files":
            count = len(data.get("files", []))
            return f"ok: прочитано файлов {count}"
        if action_type == "write_file":
            path = data.get("path", "")
            return f"ok: записан файл {path}"
        if action_type == "append_file":
            path = data.get("path", "")
            return f"ok: дополнен файл {path}"
        if action_type == "replace_in_file":
            path = data.get("path", "")
            count = data.get("occurrences_replaced", 0)
            return f"ok: заменён фрагмент в {path}, замен {count}"
        if action_type == "list_dir":
            count = len(data.get("items", []))
            return f"ok: просмотрена директория, элементов {count}"
        if action_type == "list_tree":
            truncated = bool(data.get("truncated"))
            suffix = ", дерево обрезано" if truncated else ""
            return f"ok: получено дерево файлов{suffix}"
        if action_type == "find_files":
            count = len(data.get("matches", []))
            return f"ok: найдено файлов {count}"
        if action_type == "search_code":
            count = len(data.get("results", []))
            return f"ok: найдено совпадений в коде {count}"
        if action_type == "git_status":
            return "ok: получен git status"
        if action_type == "git_diff":
            return "ok: получен git diff"
        if action_type == "run_shell":
            command_text = " ".join(data.get("command", []))
            return f"ok: выполнена shell-команда {command_text}"
        if action_type == "web_search":
            provider = data.get("provider", "unknown")
            count = len(data.get("results", []))
            return f"ok: web_search через {provider}, найдено источников {count}"
        if action_type == "fetch_url":
            url = data.get("url", "")
            truncated = bool(data.get("truncated"))
            suffix = ", контент обрезан" if truncated else ""
            return f"ok: прочитан источник {url}{suffix}"
        return f"ok: выполнено действие {action_type}"

    error_text = str(
        feedback.get("error")
        or feedback.get("data", {}).get("stderr")
        or "неизвестная ошибка"
    )
    return f"error: {action_type}: {error_text}"


def feedback_to_detail(self, command: AgentCommand, feedback: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "action_type": command.action.type,
        "ok": bool(feedback.get("ok")),
    }

    if feedback.get("ok"):
        payload["data"] = self._compact_for_prompt(feedback.get("data", {}))
    else:
        payload["error"] = str(
            feedback.get("error")
            or feedback.get("data", {}).get("stderr")
            or "неизвестная ошибка"
        )
        if feedback.get("data"):
            payload["data"] = self._compact_for_prompt(feedback.get("data", {}))

    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    return self._truncate_for_prompt(serialized, self.config.prompt_result_char_limit)


def render_feedback(self, summary_line: str, feedback: dict[str, Any]) -> str:
    ok = bool(feedback.get("ok"))
    status = "OK" if ok else "ERROR"
    return (
        f"Результат: {status}. {summary_line}. "
        f"Прогресс: {self._state.progress}%. "
        f"Стагнация: {self._state.stagnation_count}."
    )


def build_early_stop_message(self, step: int) -> str | None:
    if self._state.consecutive_errors >= self.config.max_consecutive_errors:
        return (
            "Агент остановлен. Слишком много ошибок подряд. "
            f"Последние ошибки: {', '.join(self._state.error_history[-3:])}"
        )

    if self._state.stagnation_count >= self.config.stagnation_limit:
        return (
            "Агент остановлен. Нет прогресса и есть признаки зацикливания. "
            f"Последние действия: {', '.join(self._state.recent_feedback[-3:])}"
        )

    if step > self.config.max_steps:
        return "Агент остановлен по лимиту шагов."

    return None


def build_limit_reached_message(self) -> str:
    errors = ", ".join(self._state.error_history[-3:]) or "нет"
    return (
        "Агент не завершил задачу за лимит шагов. "
        f"Текущий прогресс: {self._state.progress}%. "
        f"Последние ошибки: {errors}."
    )


def is_repeat_blocked(self, command: AgentCommand) -> bool:
    signature = self._action_signature(command)
    seen_count = self._state.seen_actions.get(signature, 0)
    if seen_count >= self.config.repeat_action_limit:
        message = (
            "Ты повторяешь уже бесполезное действие с теми же args. "
            "Выбери другой шаг: другой файл, другой поиск, или честно заверши задачу."
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
        self._ui.debug_print(f"Блокировка повтора: {command.action.type} {command.action.args}")
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