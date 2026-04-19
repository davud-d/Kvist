# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from hashlib import sha256
from pathlib import Path
from typing import Any

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam
from rich.console import Console

try:
    import tiktoken
except ImportError:
    tiktoken = None  # type: ignore[assignment]

from executor import SafeExecutor, ExecutorError
from parser import CommandValidationError, parse_agent_command
from protocol import AgentCommand
from ui import AgentUI
from deps import detect_dependency_versions

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
    max_retries: int = 3
    retry_base_delay: float = 1.0
    max_context_tokens: int = 120000
    stream: bool = True
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
        self._ui = AgentUI(console=self.console, verbose=self.config.verbose)
        self._encoding = self._init_tiktoken_encoding()

    def run(self, user_goal: str) -> str:
        self._reset_run_state()
        self._state.plan_info = self._create_plan(user_goal)
        self._state.dependency_versions = detect_dependency_versions(self.executor.workspace)
        self._ui.print_plan(self._state.plan_info)
        return self._run_loop(user_goal, start_step=1)

    def resume(self) -> str | None:
        self._reset_run_state()
        result = self._load_checkpoint()
        if result is None:
            return None
        user_goal, last_step = result
        self._ui.debug_print(
            f"Восстановлено из checkpoint: цель='{user_goal}', шаг={last_step}, прогресс={self._state.progress}%"
        )
        return self._run_loop(user_goal, start_step=last_step + 1)

    def _run_loop(self, user_goal: str, start_step: int) -> str:
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
        trimmed_prompt = self._trim_prompt_to_token_limit(system_prompt, user_prompt)
        messages: list[ChatCompletionMessageParam] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": trimmed_prompt},
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

            for attempt in range(1, self.config.max_retries + 1):
                try:
                    if self.config.stream:
                        content = self._call_model_streaming(current_model, messages)
                    else:
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
                    if attempt < self.config.max_retries:
                        delay = self.config.retry_base_delay * (2 ** (attempt - 1))
                        self._ui.debug_print(
                            f"Ошибка API ({current_model}, попытка {attempt}/{self.config.max_retries}): "
                            f"{error}. Повтор через {delay:.1f}с"
                        )
                        time.sleep(delay)

        raise RuntimeError(f"Не удалось получить ответ модели: {last_error}")

    def _call_model_streaming(
        self,
        model: str,
        messages: list[ChatCompletionMessageParam],
    ) -> str:
        stream = self.client.chat.completions.create(
            model=model,
            messages=messages,
            temperature=self.config.model_temperature,
            stream=True,
        )
        chunks: list[str] = []
        thought_shown = False
        for chunk in stream:
            delta = chunk.choices[0].delta if chunk.choices else None
            if delta and delta.content:
                chunks.append(delta.content)
                # Показываем мысль агента в реальном времени (verbose-режим)
                if self.config.verbose and not thought_shown:
                    partial = "".join(chunks)
                    # Показать thought как только увидим его начало
                    if '"thought"' in partial and len(partial) > 80:
                        thought_shown = True
                        print(f"  [streaming] {partial[:120]}...", flush=True)
        return "".join(chunks)

    @staticmethod
    def _init_tiktoken_encoding():
        if tiktoken is None:
            return None
        try:
            return tiktoken.encoding_for_model("gpt-4o")
        except Exception:
            try:
                return tiktoken.get_encoding("cl100k_base")
            except Exception:
                return None

    def _count_tokens(self, text: str) -> int:
        if self._encoding is None:
            return len(text) // 4
        return len(self._encoding.encode(text, disallowed_special=()))

    def _trim_prompt_to_token_limit(self, system_prompt: str, user_prompt: str) -> str:
        system_tokens = self._count_tokens(system_prompt)
        available = self.config.max_context_tokens - system_tokens - 500
        if available <= 0:
            available = 4000

        prompt_tokens = self._count_tokens(user_prompt)
        if prompt_tokens <= available:
            return user_prompt

        ratio = available / prompt_tokens
        target_chars = int(len(user_prompt) * ratio * 0.95)
        trimmed = user_prompt[:target_chars]
        trimmed += "\n\n[КОНТЕКСТ ОБРЕЗАН: превышен лимит токенов. Действуй на основе доступных данных.]"
        self._ui.debug_print(
            f"Промпт обрезан: {prompt_tokens} токенов → ~{available} (лимит {self.config.max_context_tokens})"
        )
        return trimmed

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
        self._ui.debug_print(f"\n[Шаг {step}] Ошибка формата: {error_text}")

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

        self._ui.debug_print(self._render_feedback(summary_line, feedback))

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
        self._ui.debug_print(f"Результат: финальный ответ на шаге {step}")

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
            self._ui.debug_print(f"Блокировка повтора: {command.action.type} {command.action.args}")
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

    def _record_user_message(self, message: str) -> None:
        clean = message.strip()
        if not clean:
            return
        self._state.user_messages.append(clean)
        del self._state.user_messages[:-80]

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

    def _save_checkpoint(self, user_goal: str, step: int) -> None:
        if not self.config.persist_logs:
            return
        logs_dir = self.executor.workspace / self.config.logs_dirname
        logs_dir.mkdir(parents=True, exist_ok=True)
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
        ckpt_path = logs_dir / "checkpoint.json"
        ckpt_path.write_text(
            json.dumps(checkpoint, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_checkpoint(self) -> tuple[str, int] | None:
        logs_dir = self.executor.workspace / self.config.logs_dirname
        ckpt_path = logs_dir / "checkpoint.json"
        if not ckpt_path.exists():
            return None
        try:
            data = json.loads(ckpt_path.read_text(encoding="utf-8"))
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

    def _clear_checkpoint(self) -> None:
        logs_dir = self.executor.workspace / self.config.logs_dirname
        ckpt_path = logs_dir / "checkpoint.json"
        if ckpt_path.exists():
            ckpt_path.unlink()
