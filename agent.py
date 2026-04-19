# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from rich import box
from rich.align import Align
from rich.console import Console, Group
from rich.live import Live
from rich.panel import Panel
from rich.rule import Rule
from rich.spinner import Spinner
from rich.text import Text

from executor import SafeExecutor, ExecutorError
from parser import CommandValidationError, parse_agent_command
from protocol import AgentCommand

_PROMPT_DIR = Path(__file__).resolve().parent

# Паттерны секретов для санации логов
_SECRET_RE = re.compile(
    r"sk-[a-zA-Z0-9]{20,}"
    r"|ghp_[a-zA-Z0-9]{36,}"
    r"|gho_[a-zA-Z0-9]{36,}"
    r"|github_pat_[a-zA-Z0-9_]{20,}"
    r"|AKIA[0-9A-Z]{16}"
    r"|xox[baprs]-[a-zA-Z0-9\-]{10,}",
    re.ASCII,
)

SYSTEM_PROMPT = (_PROMPT_DIR / "system_prompt.txt").read_text(encoding="utf-8").strip()

PLANNER_PROMPT = (_PROMPT_DIR / "planner_prompt.txt").read_text(encoding="utf-8").strip()


@dataclass(slots=True)
class AgentConfig:
    fast_model: str = "gpt-5-4-mini"
    smart_model: str = "gpt-5.4"
    planner_model: str = "gpt-4.1"
    max_steps: int = 20
    stagnation_limit: int = 6
    max_consecutive_errors: int = 6
    repeat_action_limit: int = 4
    summary_recent_events: int = 8
    recent_feedback_limit: int = 4
    detailed_feedback_limit: int = 3
    prompt_result_char_limit: int = 5000
    prompt_value_char_limit: int = 1600
    cache_enabled: bool = True
    persist_logs: bool = True
    logs_dirname: str = "agent_logs"
    model_temperature: float = 0.1
    verbose: bool = False


@dataclass(slots=True)
class PlanInfo:
    goal: str
    complexity: str
    plan: list[str]
    success_criteria: list[str]
    risks: list[str]


@dataclass(slots=True)
class RunState:
    plan_info: PlanInfo | None = None
    progress: int = 0
    summary: str = ""
    files_read: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    recent_feedback: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    recent_result_details: list[str] = field(default_factory=list)
    error_history: list[str] = field(default_factory=list)
    error_patterns: dict[str, int] = field(default_factory=dict)
    seen_actions: dict[str, int] = field(default_factory=dict)
    completed_plan_steps: set[str] = field(default_factory=set)
    stalled_reasons: list[str] = field(default_factory=list)
    step_logs: list[dict[str, Any]] = field(default_factory=list)
    user_messages: list[str] = field(default_factory=list)
    dependency_versions: dict[str, str] = field(default_factory=dict)
    web_search_history: list[dict[str, Any]] = field(default_factory=list)
    web_sources: list[str] = field(default_factory=list)
    web_facts: list[str] = field(default_factory=list)
    applied_facts: list[dict[str, str]] = field(default_factory=list)
    consecutive_errors: int = 0
    stagnation_count: int = 0
    parse_errors: int = 0
    pending_instruction: str = ""
    last_action_signature: str = ""
    last_progress: int = 0
    last_web_query: str = ""
    last_web_thought: str = ""


class LocalAgent:
    def __init__(
        self,
        client: OpenAI,
        executor: SafeExecutor,
        config: AgentConfig | None = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.config = config or AgentConfig()
        self._cache: dict[str, str] = {}
        self._state = RunState()
        self.console = Console()
        self._live: Live | None = None

    def run(self, user_goal: str) -> str:
        self._reset_run_state()
        self._state.plan_info = self._create_plan(user_goal)
        self._state.dependency_versions = self._detect_dependency_versions()
        self._print_plan(self._state.plan_info)
        self._start_live_ui()

        final_message = ""
        try:
            for step in range(1, self.config.max_steps + 1):
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
                    self._update_live_ui(
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
                    self._update_live_ui(
                        user_message=command.user_message,
                        status="ищу другой рабочий шаг",
                        step=step,
                        progress=self._state.progress,
                        plan_step=command.plan_step,
                        is_error=True,
                    )
                    continue

                self._print_step_header(step, command)

                if command.action.type == "web_search":
                    web_error = self._prepare_web_search_command(user_goal, command)
                    if web_error:
                        feedback = {
                            "ok": False,
                            "action_type": "web_search",
                            "error": web_error,
                        }
                        self._register_feedback(step, command, feedback)
                        self._update_live_ui(
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
                        self._update_live_ui(
                            user_message=command.user_message,
                            status="сработало ограничение чтения URL",
                            step=step,
                            progress=self._state.progress,
                            plan_step=command.plan_step,
                            is_error=True,
                        )
                        continue

                self._update_live_ui(
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

                self._update_live_ui(
                    user_message=command.user_message,
                    status="шаг завершен" if feedback.get("ok") else "получена ошибка",
                    step=step,
                    progress=self._state.progress,
                    plan_step=command.plan_step,
                    is_error=not bool(feedback.get("ok")),
                )

            if not final_message:
                final_message = self._build_limit_reached_message()
        finally:
            self._stop_live_ui()
            self._persist_logs(user_goal)

        if not self.config.verbose:
            self._print_final_ui(final_message)

        return final_message

    def _reset_run_state(self) -> None:
        self._state = RunState()

    def _create_plan(self, user_goal: str) -> PlanInfo:
        prompt = (
            "Задача пользователя:\n"
            f"{user_goal}\n\n"
            "Собери короткий план, критерии успеха и оцени сложность."
        )

        try:
            raw = self._call_model(
                model=self.config.planner_model,
                system_prompt=PLANNER_PROMPT,
                user_prompt=prompt,
            )
            data = self._parse_json_dict(raw)
            plan = data.get("plan")
            success_criteria = data.get("success_criteria")
            risks = data.get("risks")
            complexity = str(data.get("complexity", "simple")).strip().lower()

            if not isinstance(plan, list) or not plan:
                raise ValueError("planner вернул пустой plan")
            if not isinstance(success_criteria, list) or not success_criteria:
                raise ValueError("planner вернул пустой success_criteria")
            if not isinstance(risks, list):
                risks = []
            if complexity not in {"simple", "complex"}:
                complexity = "complex" if self._is_complex_goal(user_goal) else "simple"

            return PlanInfo(
                goal=str(data.get("goal", user_goal)).strip() or user_goal,
                complexity=complexity,
                plan=[str(item).strip() for item in plan if str(item).strip()],
                success_criteria=[
                    str(item).strip()
                    for item in success_criteria
                    if str(item).strip()
                ],
                risks=[str(item).strip() for item in risks if str(item).strip()],
            )
        except Exception:
            return self._fallback_plan(user_goal)

    def _fallback_plan(self, user_goal: str) -> PlanInfo:
        complexity = "complex" if self._is_complex_goal(user_goal) else "simple"
        return PlanInfo(
            goal=user_goal,
            complexity=complexity,
            plan=[
                "изучить релевантные файлы и структуру",
                "внести минимальные точные изменения",
                "проверить результат и ошибки",
                "дать итог по выполненной задаче",
            ],
            success_criteria=[
                "ключевые изменения внесены",
                "ошибки не проигнорированы",
                "результат проверен доступным способом",
            ],
            risks=[
                "модель может зациклиться на одном действии",
                "часть окружения может быть недоступна",
            ],
        )

    def _build_runtime_prompt(self, user_goal: str, step: int) -> str:
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

    def _call_action_model(self, prompt: str) -> str:
        preferred_model = (
            self.config.smart_model if self._prefer_smart_model() else self.config.fast_model
        )
        return self._call_model(
            model=preferred_model,
            system_prompt=SYSTEM_PROMPT,
            user_prompt=prompt,
        )

    def _call_model(self, model: str, system_prompt: str, user_prompt: str) -> str:
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ]

        models_to_try = [model]
        if model != self.config.fast_model:
            models_to_try.append(self.config.fast_model)

        last_error: Exception | None = None
        for current_model in models_to_try:
            cache_key = sha256(
                json.dumps(
                    {
                        "model": current_model,
                        "messages": messages,
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                ).encode("utf-8")
            ).hexdigest()

            if self.config.cache_enabled and cache_key in self._cache:
                return self._cache[cache_key]

            try:
                response = self.client.chat.completions.create(
                    model=current_model,
                    messages=messages,
                    temperature=self.config.model_temperature,
                )
                content = response.choices[0].message.content
                if not content:
                    raise RuntimeError("Модель вернула пустой ответ")
                if self.config.cache_enabled:
                    self._cache[cache_key] = content
                return content
            except Exception as error:
                last_error = error

        raise RuntimeError(f"Не удалось получить ответ модели: {last_error}")

    def _execute_command(self, command: AgentCommand) -> dict[str, Any]:
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

    def _register_parse_error(self, step: int, raw_text: str, error_text: str) -> None:
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
        self._debug_print(f"\n[Шаг {step}] Ошибка формата: {error_text}")

    def _register_feedback(
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

        _READ_ONLY_ACTIONS = {
            "read_file", "read_multiple_files", "list_dir",
            "list_tree", "find_files", "search_code",
        }
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

        self._debug_print(self._render_feedback(summary_line, feedback))

    def _update_state_from_success(
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

    def _update_progress(self, command: AgentCommand, ok: bool) -> None:
        plan_progress = self._plan_progress()
        target_progress = max(self._state.progress, plan_progress)

        if ok:
            if command.action.type in {"write_file", "append_file", "replace_in_file"}:
                target_progress = max(target_progress, command.progress)
            else:
                target_progress = max(target_progress, min(command.progress, 99))

        self._state.last_progress = self._state.progress
        self._state.progress = max(0, min(target_progress, 99))

    def _register_final_answer(self, step: int, message: str) -> None:
        self._state.progress = 100
        self._state.step_logs.append(
            {
                "step": step,
                "status": "final_answer",
                "progress": 100,
                "message": message,
            }
        )
        self._debug_print(f"Результат: финальный ответ на шаге {step}")

    def _plan_progress(self) -> int:
        if not self._state.plan_info or not self._state.plan_info.plan:
            return self._state.progress
        done = len(self._state.completed_plan_steps)
        total = len(self._state.plan_info.plan)
        return round(done / total * 100)

    def _record_error(self, error_text: str) -> None:
        self._state.error_history.append(error_text)
        self._state.error_history = self._state.error_history[-5:]
        self._state.error_patterns[error_text] = self._state.error_patterns.get(error_text, 0) + 1

    def _feedback_to_line(self, command: AgentCommand, feedback: dict[str, Any]) -> str:
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

    def _feedback_to_detail(self, command: AgentCommand, feedback: dict[str, Any]) -> str:
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

    def _compact_for_prompt(self, value: Any, depth: int = 0) -> Any:
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

    def _truncate_for_prompt(self, text: str, limit: int) -> str:
        if len(text) <= limit:
            return text
        return text[:limit] + f"\n[TRUNCATED_FOR_PROMPT: показаны первые {limit} символов из {len(text)}]"

    def _render_feedback(self, summary_line: str, feedback: dict[str, Any]) -> str:
        ok = bool(feedback.get("ok"))
        status = "OK" if ok else "ERROR"
        return (
            f"Результат: {status}. {summary_line}. "
            f"Прогресс: {self._state.progress}%. "
            f"Стагнация: {self._state.stagnation_count}."
        )

    def _build_summary(self) -> str:
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

    def _build_early_stop_message(self, step: int) -> str | None:
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

    def _build_limit_reached_message(self) -> str:
        errors = ", ".join(self._state.error_history[-3:]) or "нет"
        return (
            "Агент не завершил задачу за лимит шагов. "
            f"Текущий прогресс: {self._state.progress}%. "
            f"Последние ошибки: {errors}."
        )

    def _is_repeat_blocked(self, command: AgentCommand) -> bool:
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
            self._debug_print(f"Блокировка повтора: {command.action.type} {command.action.args}")
            return True

        self._state.seen_actions[signature] = seen_count + 1
        self._state.last_action_signature = signature
        return False

    def _action_signature(self, command: AgentCommand) -> str:
        return json.dumps(
            {
                "type": command.action.type,
                "args": command.action.args,
            },
            ensure_ascii=False,
            sort_keys=True,
        )

    def _print_plan(self, plan_info: PlanInfo) -> None:
        if not self.config.verbose:
            return
        print("\n[План]")
        print(f"Цель: {plan_info.goal}")
        print(f"Сложность: {plan_info.complexity}")
        for index, item in enumerate(plan_info.plan, start=1):
            print(f"{index}. {item}")
        if plan_info.success_criteria:
            print("Критерии успеха:")
            for item in plan_info.success_criteria:
                print(f"- {item}")

    def _print_step_header(self, step: int, command: AgentCommand) -> None:
        if not self.config.verbose:
            return
        print(f"\n[Шаг {step}]")
        print(f"Этап: {command.plan_step}")
        print(f"Мысль: {command.thought}")
        print(f"Сообщение: {command.user_message}")
        print(f"Действие: {command.action.type} {command.action.args}")
        print(f"Заявленный прогресс: {command.progress}%")

    def _prefer_smart_model(self) -> bool:
        if not self._state.plan_info:
            return True
        if self._state.plan_info.complexity == "complex":
            return True
        if self._state.consecutive_errors > 0:
            return True
        if self._state.parse_errors > 0:
            return True
        if self._state.stagnation_count > 0:
            return True
        return False

    def _is_complex_goal(self, user_goal: str) -> bool:
        lowered = user_goal.casefold()
        keywords = (
            "архитект",
            "рефактор",
            "production",
            "продакш",
            "система",
            "агент",
            "несколько файлов",
            "проект",
            "анализ",
            "память",
            "планирован",
            "улучш",
            "апгрейд",
        )
        return len(user_goal) > 180 or any(keyword in lowered for keyword in keywords)

    def _prepare_web_search_command(self, user_goal: str, command: AgentCommand) -> str | None:
        query = str(command.action.args.get("query", "")).strip()
        if not query:
            return "Пустой query для web_search"

        if not self._is_web_topic(user_goal=user_goal, query=query):
            return "web_search разрешен только для задач по внешним библиотекам и актуальным API/релизам"

        if "top_k" not in command.action.args and "max_results" not in command.action.args:
            command.action.args["top_k"] = 5
        if "freshness" not in command.action.args:
            command.action.args["freshness"] = "any"

        enhanced_query = self._append_known_version_to_query(query)
        command.action.args["query"] = enhanced_query

        normalized_query = self._normalize_message(enhanced_query)
        normalized_thought = self._normalize_message(command.thought)
        if (
            normalized_query == self._state.last_web_query
            and normalized_thought == self._state.last_web_thought
        ):
            return "Повтор одинакового web_search подряд без новой гипотезы запрещен"

        self._state.last_web_query = normalized_query
        self._state.last_web_thought = normalized_thought
        return None

    def _prepare_fetch_url_command(self, command: AgentCommand) -> str | None:
        url = str(command.action.args.get("url", "")).strip()
        if not url:
            return "Пустой url для fetch_url"
        if not re.match(r"^https?://", url, re.IGNORECASE):
            return "fetch_url поддерживает только http/https URL"
        if "max_chars" not in command.action.args:
            command.action.args["max_chars"] = 12000
        return None

    def _is_web_topic(self, user_goal: str, query: str) -> bool:
        text = f"{user_goal} {query}".casefold()
        keywords = (
            "external library",
            "sdk",
            "framework",
            "deprecated",
            "migration",
            "release notes",
            "changelog",
            "breaking change",
            "signature",
            "attribute",
            "import error",
            "official docs",
            "new version",
            "price",
            "pricing",
            "limit",
            "status",
            "документац",
            "внешн",
            "библиотек",
            "sdk",
            "фреймвор",
            "деприк",
            "deprecated",
            "migration",
            "миграц",
            "release",
            "changelog",
            "сигнатур",
            "import",
            "attribute",
            "breaking",
            "верси",
            "лимит",
            "цена",
            "статус",
        )

        if any(keyword in text for keyword in keywords):
            return True

        for package in self._state.dependency_versions:
            if self._contains_package_token(text, package):
                return True

        return False

    def _append_known_version_to_query(self, query: str) -> str:
        lowered_query = query.casefold()
        for package, raw_version in self._state.dependency_versions.items():
            if not self._contains_package_token(lowered_query, package):
                continue

            version_token = self._extract_version_token(raw_version)
            if not version_token:
                continue
            if version_token.casefold() in lowered_query:
                continue
            return f"{query} {version_token}"

        return query

    def _contains_package_token(self, text: str, package: str) -> bool:
        candidates = {
            package.casefold(),
            package.replace("-", "_").casefold(),
            package.replace("_", "-").casefold(),
        }
        for candidate in candidates:
            pattern = rf"(?<![a-z0-9]){re.escape(candidate)}(?![a-z0-9])"
            if re.search(pattern, text):
                return True
        return False

    def _extract_version_token(self, raw_spec: str) -> str:
        match = re.search(r"\d+(?:\.\d+){0,4}", raw_spec)
        return match.group(0) if match else ""

    def _detect_dependency_versions(self) -> dict[str, str]:
        versions: dict[str, str] = {}

        for parser in (
            self._parse_requirements_versions,
            self._parse_pyproject_versions,
            self._parse_poetry_lock_versions,
            self._parse_uv_lock_versions,
        ):
            try:
                for name, version in parser().items():
                    if name and version:
                        versions[name] = version
            except Exception:
                continue

        return versions

    def _parse_requirements_versions(self) -> dict[str, str]:
        path = self.executor.workspace / "requirements.txt"
        if not path.exists() or not path.is_file():
            return {}

        text = path.read_text(encoding="utf-8", errors="replace")
        versions: dict[str, str] = {}
        pattern = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([=<>!~]{1,2})\s*([^\s;]+)")
        for line in text.splitlines():
            clean = line.split("#", 1)[0].strip()
            if not clean:
                continue
            match = pattern.match(clean)
            if not match:
                continue
            name, operator, value = match.groups()
            versions[self._normalize_package_name(name)] = f"{operator}{value}"

        return versions

    def _parse_pyproject_versions(self) -> dict[str, str]:
        path = self.executor.workspace / "pyproject.toml"
        if not path.exists() or not path.is_file() or tomllib is None:
            return {}

        try:
            data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            return {}

        versions: dict[str, str] = {}

        project = data.get("project", {}) if isinstance(data, dict) else {}
        dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
        if isinstance(dependencies, list):
            for item in dependencies:
                if not isinstance(item, str):
                    continue
                name, spec = self._parse_dependency_spec(item)
                if name and spec:
                    versions[name] = spec

        optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
        if isinstance(optional, dict):
            for dep_list in optional.values():
                if not isinstance(dep_list, list):
                    continue
                for item in dep_list:
                    if not isinstance(item, str):
                        continue
                    name, spec = self._parse_dependency_spec(item)
                    if name and spec:
                        versions[name] = spec

        tool = data.get("tool", {}) if isinstance(data, dict) else {}
        poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
        poetry_dependencies = poetry.get("dependencies", {}) if isinstance(poetry, dict) else {}
        if isinstance(poetry_dependencies, dict):
            for raw_name, raw_spec in poetry_dependencies.items():
                name = self._normalize_package_name(str(raw_name))
                if name == "python":
                    continue
                if isinstance(raw_spec, str):
                    spec = raw_spec.strip()
                elif isinstance(raw_spec, dict):
                    spec = str(raw_spec.get("version", "")).strip()
                else:
                    spec = ""
                if name and spec:
                    versions[name] = spec

        return versions

    def _parse_poetry_lock_versions(self) -> dict[str, str]:
        path = self.executor.workspace / "poetry.lock"
        if not path.exists() or not path.is_file():
            return {}

        text = path.read_text(encoding="utf-8", errors="replace")
        versions: dict[str, str] = {}
        blocks = text.split("[[package]]")
        for block in blocks:
            name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
            version_match = re.search(r"version\s*=\s*\"([^\"]+)\"", block)
            if not name_match or not version_match:
                continue
            name = self._normalize_package_name(name_match.group(1))
            version = version_match.group(1).strip()
            if name and version:
                versions[name] = f"=={version}"

        return versions

    def _parse_uv_lock_versions(self) -> dict[str, str]:
        path = self.executor.workspace / "uv.lock"
        if not path.exists() or not path.is_file():
            return {}

        text = path.read_text(encoding="utf-8", errors="replace")
        versions: dict[str, str] = {}

        if tomllib is not None:
            try:
                data = tomllib.loads(text)
                packages = data.get("package") if isinstance(data, dict) else None
                if isinstance(packages, list):
                    for item in packages:
                        if not isinstance(item, dict):
                            continue
                        name = self._normalize_package_name(str(item.get("name", "")))
                        version = str(item.get("version", "")).strip()
                        if name and version:
                            versions[name] = f"=={version}"
                    if versions:
                        return versions
            except Exception:
                pass

        blocks = text.split("[[package]]")
        for block in blocks:
            name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
            version_match = re.search(r"version\s*=\s*\"([^\"]+)\"", block)
            if not name_match or not version_match:
                continue
            name = self._normalize_package_name(name_match.group(1))
            version = version_match.group(1).strip()
            if name and version:
                versions[name] = f"=={version}"

        return versions

    def _parse_dependency_spec(self, raw: str) -> tuple[str, str]:
        pattern = re.compile(
            r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([=<>!~]{1,2})?\s*([^,;\s]+)?"
        )
        match = pattern.match(raw.strip())
        if not match:
            return "", ""

        name, operator, value = match.groups()
        normalized_name = self._normalize_package_name(name)
        if operator and value:
            return normalized_name, f"{operator}{value}"
        return normalized_name, ""

    def _normalize_package_name(self, name: str) -> str:
        return name.strip().lower().replace("_", "-")

    def _format_dependency_versions_for_prompt(self) -> str:
        if not self._state.dependency_versions:
            return "версия не найдена в requirements.txt, pyproject.toml, poetry.lock, uv.lock"

        lines = []
        items = sorted(self._state.dependency_versions.items())
        for package, version in items[:20]:
            lines.append(f"- {package}: {version}")
        if len(items) > 20:
            lines.append(f"- ... ещё {len(items) - 20}")
        return "\n".join(lines)

    def _format_web_facts_for_prompt(self) -> str:
        if not self._state.web_facts:
            return "нет"
        return "\n".join(f"- {item}" for item in self._state.web_facts[-6:])

    def _extract_fact_from_content(self, url: str, title: str, content: str) -> str:
        lines = [
            re.sub(r"\s+", " ", line).strip()
            for line in content.splitlines()
            if line.strip()
        ]
        if not lines:
            return ""

        snippet = " ".join(lines[:2]).strip()
        snippet = snippet[:260]
        label = title or url
        if not label:
            label = "Источник"
        return f"{label}: {snippet}" if snippet else label

    def _summarize_search_results(self, results: Any) -> str:
        if not isinstance(results, list):
            return ""

        titles: list[str] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            if not title:
                continue
            titles.append(title)
            if len(titles) >= 3:
                break

        return "; ".join(titles)

    def _normalize_message(self, text: str) -> str:
        return re.sub(r"\s+", " ", text).strip().casefold()

    def _start_live_ui(self) -> None:
        if self.config.verbose:
            return

        initial = self._render_live_panel(
            user_message="я начинаю работу и собираю контекст",
            status="подготовка",
            step=0,
            progress=0,
            plan_step="инициализация",
            is_error=False,
        )
        self._live = Live(
            initial,
            console=self.console,
            refresh_per_second=8,
            transient=True,
        )
        self._live.start()

    def _stop_live_ui(self) -> None:
        if self._live is None:
            return
        self._live.stop()
        self._live = None

    def _update_live_ui(
        self,
        user_message: str,
        status: str,
        step: int,
        progress: int,
        plan_step: str,
        is_error: bool = False,
    ) -> None:
        if self.config.verbose or self._live is None:
            return

        panel = self._render_live_panel(
            user_message=user_message,
            status=status,
            step=step,
            progress=progress,
            plan_step=plan_step,
            is_error=is_error,
        )
        self._live.update(panel, refresh=True)

    def _render_live_panel(
        self,
        user_message: str,
        status: str,
        step: int,
        progress: int,
        plan_step: str,
        is_error: bool,
    ) -> Panel:
        if is_error:
            status_style = "bright_red"
        elif "заверш" in status:
            status_style = "bright_green"
        else:
            status_style = "bright_yellow"

        title_text = Text("Агент работает", style="bold bright_magenta")
        spinner = Spinner("dots", text=Text("выполняю задачу", style="bright_cyan"))
        message_text = Text(user_message or "я продолжаю выполнение", style="bold bright_cyan")
        status_text = Text(f"Статус: {status}", style=status_style)
        meta_text = Text(
            f"Шаг {step} • Прогресс {max(0, min(progress, 100))}% • {plan_step}",
            style="grey70",
        )

        content = Group(
            Align.center(title_text),
            spinner,
            Text(""),
            message_text,
            status_text,
            Text(""),
            meta_text,
        )

        return Panel(
            content,
            box=box.ROUNDED,
            border_style="bright_magenta",
            padding=(1, 2),
        )

    def _print_final_ui(self, final_message: str) -> None:
        history = self._unique_messages(self._state.user_messages)
        if history:
            history_text = "\n".join(f"{index}. {item}" for index, item in enumerate(history, start=1))
        else:
            history_text = "1. я выполнил задачу"

        thoughts_panel = Panel(
            Text(history_text, style="bright_cyan"),
            title="> мысли и действия",
            title_align="left",
            border_style="bright_magenta",
            box=box.ROUNDED,
            padding=(1, 2),
        )

        final_panel = Panel(
            Text(final_message, style="bright_green"),
            title="Финальный ответ",
            title_align="left",
            border_style="bright_green",
            box=box.ROUNDED,
            padding=(1, 2),
        )

        self.console.print(thoughts_panel)
        self.console.print(Rule(style="grey35"))
        self.console.print(final_panel)

    def _record_user_message(self, message: str) -> None:
        clean = message.strip()
        if not clean:
            return
        self._state.user_messages.append(clean)
        del self._state.user_messages[:-80]

    def _unique_messages(self, messages: list[str]) -> list[str]:
        unique: list[str] = []
        seen: set[str] = set()
        for item in messages:
            normalized = self._normalize_message(item)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            unique.append(item.strip())
        return unique

    def _parse_json_dict(self, text: str) -> dict[str, Any]:
        stripped = text.strip()
        candidates = [stripped]

        if stripped.startswith("```") and stripped.endswith("```"):
            lines = stripped.splitlines()
            inner = "\n".join(lines[1:-1]).strip()
            candidates.append(inner)

        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end != -1 and start < end:
            candidates.append(stripped[start : end + 1])

        for candidate in candidates:
            cleaned = candidate.replace("“", '"').replace("”", '"')
            try:
                parsed = json.loads(cleaned)
            except json.JSONDecodeError:
                cleaned = cleaned.replace(",}", "}").replace(",]", "]")
                parsed = json.loads(cleaned)
            if isinstance(parsed, dict):
                return parsed

        raise ValueError("Не удалось распарсить JSON-объект")

    def _push_unique(self, items: list[str], value: str, limit: int = 8) -> None:
        if not value:
            return
        if value in items:
            items.remove(value)
        items.append(value)
        del items[:-limit]

    def _push_bounded(self, items: list[str], value: str, limit: int) -> None:
        if not value:
            return
        items.append(value)
        del items[:-limit]

    def _debug_print(self, message: str) -> None:
        if self.config.verbose:
            print(message)

    def _sanitize_for_log(self, value: Any, max_str_len: int = 300) -> Any:
        """Рекурсивная санация данных для логов: редактирование секретов, truncation строк."""
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

    def _persist_logs(self, user_goal: str) -> None:
        if not self.config.persist_logs:
            return

        logs_dir = self.executor.workspace / self.config.logs_dirname
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
