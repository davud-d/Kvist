# -*- coding: utf-8 -*-
from __future__ import annotations

import csv
import functools
import io
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from change_journal import build_diff_report

# Externally visible actions whose effect cannot be reconciled from workspace
# state after a crash (their stdout/exit status are lost). Resume never replays
# them; it stops and reports the state as ambiguous.
_NON_RECONCILABLE_SIDE_EFFECTS = frozenset({"run_shell", "delegate_task"})


def _build_pending_action(self, command: AgentCommand, step: int) -> dict[str, Any] | None:
    """Build the durable pending-action record for an externally visible action.

    Reconcilable file mutations carry pre/intended digests; non-reconcilable
    side effects carry only identity. Returns ``None`` for read-only actions,
    which have no side effect worth protecting and are safe to re-issue.
    """
    common = {
        "step": step,
        "run_id": getattr(self, "_run_id", None),
        "action_id": uuid.uuid4().hex,
    }
    preview = self.executor.preview_write(command)
    if preview is not None:
        # Descriptor sufficient to deterministically replay the *logical* success
        # transition (plan progression, progress, verification re-arm) on resume
        # without re-executing the write.
        return {
            **common,
            "action_kind": "file",
            "action_type": command.action.type,
            "plan_step": command.plan_step,
            **preview,
        }
    if command.action.type in _NON_RECONCILABLE_SIDE_EFFECTS:
        return {**common, "action_kind": "side_effect", "action_type": command.action.type}
    return None

from action_result import build_action_result
from agent_state import RunCancelled, RunState
from executor import UNTRUSTED_CONTENT_NOTICE, ExecutorError
from parser import CommandValidationError, parse_agent_commands
from protocol import AgentCommand
from redaction import defang_prompt_boundary_tags, redact_text, redact_value
from agent_planner import infer_task_kind
from role_router import has_change_verb, has_no_write_marker

_READ_ONLY_ACTIONS = {
    "read_file", "read_multiple_files", "list_dir",
    "list_tree", "find_files", "search_code",
}

_WRITE_ACTIONS = {"write_file", "append_file", "replace_in_file"}
_VERIFY_ONLY_ACTIONS = {"git_status", "git_diff", "run_shell", "list_tree", "find_files", "read_file"}

# Floor per declared phase, used only when there is no plan to count completed
# steps against (planning disabled). "done" is absent: 100 belongs to the
# terminal action, not to a phase label.
_PLAN_STEP_BANDS = {"inspect": 15, "plan": 30, "edit": 55, "verify": 85}

_FINISH_ACTIONS = {"final_answer", "report_to_user"}

# Distinct actions after a hard environment denial before that denial stops
# counting as an unresolved error. It never blocks finishing: a denial must not
# push the model into working around it.
_RECOVERY_ATTEMPTS_REQUIRED = 2

# Every completion blocker names this exit, so a task that cannot or should
# not be done ends truthfully instead of in forced busywork.
_HONEST_EXIT_HINT = (
    "If the work cannot or should not be done (target missing, premise false, "
    "user denied the change, environment blocks it), finish with final_answer or "
    'report_to_user using "outcome": "incomplete" and say why.'
)
_HONEST_EXIT_HINT_RU = (
    "Если работу нельзя или не нужно выполнять (цели нет, предпосылка ложна, "
    "пользователь отклонил правку, среда не даёт), заверши final_answer или "
    'report_to_user с "outcome": "incomplete" и объясни причину.'
)


def _is_hard_environment_denial(error_text: str) -> bool:
    lowered = str(error_text).casefold()
    return "hard security denial" in lowered or "hard deny" in lowered


def _hard_denial_rule_text(error_text: str) -> str:
    """The single rule sentence from a hard-denial error, without the prefix."""
    text = str(error_text).strip()
    marker = "hard security denial:"
    idx = text.casefold().find(marker)
    if idx != -1:
        text = text[idx + len(marker):].strip()
    return text.splitlines()[0].strip() if text else "environment security policy"


def _note_run_context(self, **facts: Any) -> None:
    """Forward per-step run facts to a UI that exposes the live-panel channel.

    Optional capability: UIs without ``set_run_context`` (headless, tests) are
    skipped, exactly like the ``print_usage`` usage channel.
    """
    notify = getattr(self._ui, "set_run_context", None)
    if notify is not None:
        notify(**facts)


def _emit_action_result(
    self, step: int, command: AgentCommand, feedback: dict[str, Any]
) -> None:
    """Report what an action did to UIs exposing ``print_action_result``.

    Optional capability, probed with ``getattr`` like ``print_usage``: the
    classic terminal has no such channel and is left untouched.
    """
    notify = getattr(self._ui, "print_action_result", None)
    if notify is None:
        return
    notify(build_action_result(step, command.action.type, command.action.args, feedback))


_BLOCKER_LABELS = {
    "destructive": ("массовое удаление — нужна сверка", "mass deletion — needs review"),
    "not_created": ("файл ещё не создан", "file not created yet"),
    "not_modified": ("правки ещё не внесены", "no changes made yet"),
    "plan_incomplete": ("план не завершён", "plan not finished"),
    "artifacts_missing": ("не хватает файлов", "required files missing"),
    "unverified": ("ждёт проверки правок", "changes not verified"),
}


def _extend_step_budget(self) -> bool:
    """Ask whether to keep going past the step budget.

    Only an interactive screen can answer. Without one — headless frontends,
    run_task.py, a piped terminal — there is nobody to ask, so the budget stays
    a hard stop exactly as before.
    """
    ask = getattr(self._ui, "ask_choice", None)
    if ask is None:
        return False

    extra = self.config.max_steps
    if self._state.response_language == "ru":
        title = f"Пройдено {self._state.step_budget} шагов, задача не завершена."
        options = [("continue", f"продолжить, ещё {extra}"), ("finish", "завершить отчётом")]
    else:
        title = f"{self._state.step_budget} steps used, the task is not finished."
        options = [("continue", f"keep going, {extra} more"), ("finish", "finish with a report")]

    if ask(title, options) != "continue":
        return False
    self._state.step_budget += extra
    return True


def _plan_position(self) -> dict[str, Any]:
    """Measured facts the live panel shows in place of a declared percentage.

    Position in the plan, the plan step still being worked on, why completion
    is being withheld, and how close the run is to the stagnation limit.
    """
    plan = self._state.plan_info.plan if self._state.plan_info else []
    remaining = _remaining_plan_steps(self)
    return {
        "plan_done": len(plan) - len(remaining),
        "plan_total": len(plan),
        "plan_current": remaining[0] if remaining else "",
        "blocker": _first_blocker_label(self),
        "stall": self._state.stagnation_count,
        "stall_limit": self.config.stagnation_limit,
        "max_steps": self._state.step_budget,
    }


def _first_blocker_label(self) -> str:
    """Short user-facing reason the run cannot finish yet, or empty.

    The blocker texts themselves are instructions addressed to the model, so
    the panel shows a label keyed off the blocker's kind instead.
    """
    for kind in self._state.completion_blocker_kinds:
        label = _BLOCKER_LABELS.get(kind)
        if label:
            return label[0] if self._state.response_language == "ru" else label[1]
    return ""


def _note_thought(self, thought: str) -> None:
    """Give the live panel the latest action's thought.

    It is the thinking column's fallback for providers that stream no
    reasoning. Optional channel, skipped by UIs without ``set_last_thought``.
    """
    notify = getattr(self._ui, "set_last_thought", None)
    if notify is not None:
        notify(thought)


def _run_pipeline_dwell(self, user_goal: str, step: int) -> None:
    """Prefetch the next step's action call while holding the current one on screen.

    Off-loads only the pure LLM call for ``step + 1`` and then sleeps a short,
    latency-matched dwell so the user reads the current step while the next is
    prepared — the perceived-latency pipeline. Fast direct providers (OpenAI,
    Anthropic, DeepSeek) skip it entirely: strictly sequential, clean speed.
    Also off in verbose mode and whenever request pacing is disabled (tests,
    ``api_request_interval=0``) — see ``agent_model._pipeline_allowed``.
    """
    from agent_model import (
        _launch_action_prefetch,
        _pipeline_allowed,
        _pipeline_dwell_seconds,
    )

    if not _pipeline_allowed(self):
        return
    next_step = step + 1
    if next_step > self._state.step_budget:
        return
    if self._state.plan_info is None or self._build_early_stop_message(next_step):
        return
    next_prompt = self._build_runtime_prompt(user_goal, next_step)
    _launch_action_prefetch(self, next_prompt)
    time.sleep(_pipeline_dwell_seconds(self))

# Read-only inspections still permitted while a corrective action is required.
_CORRECTIVE_READ_BUDGET = 3

# git_diff hunks removing at least this many lines, while adding proportionally
# far fewer, are treated as accidental destruction rather than a fix.
_DESTRUCTIVE_DIFF_MIN_REMOVED = 30
_DESTRUCTIVE_DIFF_KEEP_RATIO = 0.5

_REPORT_ARTIFACT_PATTERNS = {
    "report.txt",
    "final_report.txt",
    "task_report.txt",
    "task_completion_report.txt",
    ".test_report.txt",
    "summary.txt",
    "status.txt",
    "completion_report.txt",
}
_REPORT_ARTIFACT_EXTENSIONS = {".txt", ".md", ".json", ".log"}
_REPORT_ARTIFACT_KEYWORDS = (
    "report",
    "summary",
    "completion",
    "status",
    "verification",
    "verify",
    "results",
    "result",
    "отчет",
    "отчёт",
    "сводк",
    "резюм",
    "итог",
)
_SAFE_READ_ONLY_SHELLS = {"ls", "pwd", "cat", "head", "tail", "sed", "rg", "grep", "find"}
_SAFE_READ_ONLY_GIT_SUBCOMMANDS = {"status", "diff", "show", "log"}
_READ_ONLY_SHELL_DENYLIST = {
    "pytest",
    "compileall",
    "py_compile",
    "touch",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "git add",
    "git apply",
}
# sqlite3's own dot-commands that only inspect the database or the CLI's
# display settings; every other dot-command (.import, .read, .output,
# .shell/.system, ...) can write files or run host commands.
_SAFE_READ_ONLY_SQLITE_DOT_COMMANDS = {".schema", ".tables", ".mode", ".headers", ".quit"}
# PRAGMAs that only report schema metadata. Any other PRAGMA — including
# these same names used as `PRAGMA x = value` — can change database state.
_SAFE_READ_ONLY_SQLITE_PRAGMAS = {"table_info", "index_list", "foreign_key_list", "table_list"}
_UNSAFE_SQLITE_STATEMENT_RE = re.compile(
    r"^(insert|update|delete|drop|alter|create|replace|vacuum|reindex|attach)\b"
)

_ARTIFACT_RE = re.compile(r"(?<!\w)([A-Za-z0-9_./-]+(?:\.[A-Za-z0-9_-]+|/))(?!\w)")
# "check if/whether X" is a question about existing state, answered by reading;
# a bare "check" stays a verification step that only a change can precede.
_PLAN_INSPECT_RE = re.compile(r"inspect|read|review|analy|check (?:if|whether)|find|tree|search|провер|прочит|просмотр|найти|структур|поиск")
_PLAN_DELEGATE_RE = re.compile(r"delegat|hand ?off|hand (?:it |the \w+ )?over|делегир|передат")
_PLAN_EDIT_RE = re.compile(r"create|write|build|implement|integrat|develop|design|enhanc|fix|edit|change|add|setup|configure|scaffold|созда|запис|измен|обнов|добав|исправ")
_PLAN_VERIFY_RE = re.compile(r"verify|check|test|run|compile|import|syntax|launch|провер|запуст|компил|синтакс|импорт")
_PLAN_REPORT_RE = re.compile(r"report|summary|summari|document|final|отчет|отчёт|сводк|резюм|итог|вывест|отображ|показ|результат|представ|вывод")
_VERIFY_SHELL_RE = re.compile(r"py_compile|compileall|pytest|python(?:3)?\s+-m\s+|python(?:3)?\s+-c|python(?:3)?\s+\S+\.py(?:\s|$)|uv\s+run|node\s+--check")
_SEMANTIC_TOKEN_RE = re.compile(r"[A-Za-z0-9]+")
_PLAN_STRUCTURE_INSPECT_RE = re.compile(r"structure|directory|workspace|tree|структур|директор|дерев|корне")
_PLAN_EXISTS_RE = re.compile(r"^(if\s+.+\s+exists\b|если\s+.+\s+существ)")
_PLAN_NOT_EXISTS_RE = re.compile(r"^(if\s+.+\s+(does not exist|is missing)\b|если\s+.+\s+не\s+существ)")

_PLAN_STOPWORDS = {
    "with", "from", "into", "over", "under", "about", "this", "that", "then", "than",
}

_HIGH_SIGNAL_TERMS = {
    "crm", "ux", "ui", "api", "sdk", "sql", "db", "bot",
    "telegr", "telegram", "admin", "payme", "reply",
}

_SYSTEMD_UNIT_NOT_FOUND_RE = re.compile(
    r"Unit\s+\S+\.service\s+could\s+not\s+be\s+found",
    re.IGNORECASE,
)

_SERVICE_INSPECTION_GOAL_RE = re.compile(
    r"(?:статус|status|покажи|show|провер|check|посмотри|look)\s+.{0,30}(?:сервис|service|systemd)",
    re.IGNORECASE,
)


def _build_report_context(
    self, user_goal: str, summary: str, result: str, issues: str, outcome: str
) -> str:
    """Build a structured JSON context block for the chat reporter."""
    state = self._state
    context = {
        "user_goal": user_goal,
        "outcome": outcome,
        "response_language": state.response_language or "en",
        "role": state.current_agent_role or "coder",
        "summary": summary,
        "result": result,
        "issues": issues,
        "files_created": [f for f in state.files_changed if f not in state.files_read] or state.files_changed,
        "files_changed": state.files_changed,
        "files_read": state.files_read,
        "commands_run": [e for e in state.recent_events[-20:] if "run_shell" in e or "executed" in e],
        "plan_steps": state.plan_info.plan if state.plan_info else [],
        "errors": state.error_history[-5:] if state.error_history else [],
    }
    safe_context = redact_value(context)
    assert isinstance(safe_context, dict)
    return json.dumps(safe_context, ensure_ascii=False, indent=2)


def _render_report(self, context: str) -> str:
    """Call the chat model to render a report from agent context. Streams markdown to console."""
    from agent_model import (
        _emit_usage,
        _load_role_prompt,
        safe_extract_completion_content,
        append_reasoning_delta,
        safe_extract_reasoning_delta,
        safe_extract_stream_delta,
        _create_json_completion,
    )

    report_prompt = _load_role_prompt("report")
    stream_model = self.config.fast_model
    messages = [
        {"role": "system", "content": report_prompt},
        {"role": "user", "content": context},
    ]

    # The report is free-form markdown prose, so JSON mode must stay off — with
    # it on, a JSON-mode provider returns the report wrapped in a JSON object and
    # the raw JSON reaches the console instead of a rendered panel.
    if not getattr(self.config, "stream", True):
        response = _create_json_completion(
            self, stream_model, messages, stream=False, json_mode=False
        )
        raw = safe_extract_completion_content(
            response, self.config.provider, stream_model
        )
        self._ui.print_user_markdown(raw)
        _emit_usage(
            self,
            model=stream_model,
            input_tokens=self._count_tokens(report_prompt) + self._count_tokens(context),
            output_tokens=self._count_tokens(raw),
            kind="report",
        )
        return raw

    stream = _create_json_completion(
        self, stream_model, messages, stream=True, json_mode=False
    )
    chunks: list[str] = []
    reasoning_chunks: list[str] = []
    last_update = __import__("time").monotonic()
    last_reasoning_update = last_update

    with self._ui.live_markdown() as update_markdown:
        reasoning_update = getattr(update_markdown, "reasoning", None)
        for chunk in stream:
            text = ""
            delta = safe_extract_stream_delta(chunk)
            if delta is not None:
                text = getattr(delta, "content", "") if not isinstance(delta, dict) else delta.get("content", "")
            elif isinstance(chunk, dict):
                if "choices" in chunk and chunk["choices"]:
                    text = chunk["choices"][0].get("delta", {}).get("content", "")
                else:
                    text = chunk.get("content", "")
            elif hasattr(chunk, "content"):
                text = chunk.content
            elif isinstance(chunk, str):
                text = chunk

            reasoning_text = safe_extract_reasoning_delta(delta)
            if reasoning_text and reasoning_update is not None and not chunks:
                append_reasoning_delta(reasoning_chunks, reasoning_text)
                now = __import__("time").monotonic()
                if now - last_reasoning_update > 0.1:
                    reasoning_update("".join(reasoning_chunks))
                    last_reasoning_update = now

            if text:
                # The first answer token seals thinking: flush buffered
                # reasoning so a fast stream never drops it.
                if not chunks and reasoning_update is not None and reasoning_chunks:
                    reasoning_update("".join(reasoning_chunks))
                chunks.append(text)
                now = __import__("time").monotonic()
                if now - last_update > 0.1:
                    update_markdown("".join(chunks))
                    last_update = now

        if reasoning_update is not None and reasoning_chunks and not chunks:
            reasoning_update("".join(reasoning_chunks))
        update_markdown("".join(chunks))

    # Real tiktoken usage of the report call; thinking tokens count as output.
    _emit_usage(
        self,
        model=stream_model,
        input_tokens=self._count_tokens(report_prompt) + self._count_tokens(context),
        output_tokens=self._count_tokens("".join(chunks))
        + self._count_tokens("".join(reasoning_chunks)),
        kind="report",
    )

    return "".join(chunks)


def finalize_run(
    self,
    user_goal: str,
    task_completed: bool,
    primary_error: BaseException | None,
) -> None:
    """Attempt every lifecycle cleanup without replacing a primary failure."""
    from agent_model import _join_pending_prefetch

    cleanup_steps: list[tuple[str, Any]] = [
        ("join pending prefetch", lambda: _join_pending_prefetch(self)),
        ("seal run status", lambda: seal_run_status(self)),
        ("stop live UI", self._ui.stop_live),
        ("persist run log", lambda: self._persist_logs(user_goal)),
        ("flush performance log", self._perf_logger.flush),
    ]
    if task_completed:
        cleanup_steps.append(("clear completed checkpoint", self._clear_checkpoint))

    failures: list[tuple[str, Exception]] = []
    for label, cleanup in cleanup_steps:
        try:
            cleanup()
        except Exception as error:
            failures.append((label, error))

    if not failures:
        return

    details = "; ".join(
        f"{label}: {redact_text(str(error).strip() or type(error).__name__)}"
        for label, error in failures
    )
    message = f"Runtime cleanup failed after all steps were attempted: {details}"
    if primary_error is not None:
        primary_error.add_note(message)
        return
    raise RuntimeError(message) from None


@dataclass(slots=True, frozen=True)
class _ActionOutcome:
    """What running one action means for the rest of its batch and the run.

    ``proceed`` is set only by an action that executed and succeeded: a batch
    stops at the first action that did not, so a later action never runs on
    the assumption that an earlier one worked.
    """

    proceed: bool = False
    finished: bool = False
    final_message: str = ""
    report_rendered: bool = False


_BLOCKED = _ActionOutcome()


def run_loop(self, user_goal: str, start_step: int) -> str:
    self._ui.start_live()

    final_message = ""
    create_goal = _is_create_goal(user_goal)
    modify_goal = _is_modify_goal(user_goal)
    verify_only_goal = _is_verify_only_goal(user_goal)
    # Only an authoritative source may forbid writes: the reader role and an
    # explicit "do not modify" request. A keyword guess must never hard-block a
    # write — "переименуй ... и проверь, что всё работает" is a rename task, and
    # blocking its writes left it with nothing it was allowed to do. Clearing
    # create/modify avoids the runtime demanding a write it will then refuse.
    read_only_goal = self._state.current_agent_role == "reader" or has_no_write_marker(user_goal)
    if read_only_goal:
        create_goal = False
        modify_goal = False
    self._state.user_goal = user_goal
    self._state.create_goal = create_goal
    self._state.modify_goal = modify_goal
    self._state.read_only_goal = read_only_goal
    self._state.verify_only_goal = verify_only_goal
    self._state.report_goal = _resolve_report_goal(self, user_goal)
    # "check if main.py exists" names a file it must not be required to create.
    if not self._state.required_artifacts and not (read_only_goal or verify_only_goal):
        self._state.required_artifacts = _anchor_bare_artifacts(
            Path(self.executor.workspace),
            _extract_required_artifacts(user_goal, self._state.plan_info),
        )
    self._refresh_completion_requirements()
    self._state.step_budget = max(self._state.step_budget, self.config.max_steps)
    report_rendered = False
    task_completed = False
    step = start_step
    try:
        while True:
            if step > self._state.step_budget and not _extend_step_budget(self):
                final_message = self._build_limit_reached_message()
                break

            if self._cancel_event.is_set():
                final_message = _stop_by_user(self)
                break

            early_stop_message = self._build_early_stop_message(step)
            if early_stop_message:
                final_message = early_stop_message
                break

            prompt = self._build_runtime_prompt(user_goal, step)
            self._current_step = step
            _note_run_context(self, action="generating JSON")
            loop_pre = time.monotonic()
            try:
                raw_text = self._call_action_model(prompt)
            except RunCancelled:
                # Cancelled mid-generation: the stream was closed and no action
                # ran, so exit through the same safe-stop finalization as a
                # between-steps cancel — report rendered, session/checkpoint
                # written, no half-applied work.
                final_message = _stop_by_user(self)
                break
            except Exception as error:
                error_text = redact_text(
                    str(error).strip() or type(error).__name__
                )
                self._state.consecutive_errors += 1
                self._record_error(error_text)
                self._state.step_logs.append(
                    {
                        "step": step,
                        "status": "model_error",
                        "error": error_text,
                    }
                )
                self._ui.update_live(
                    user_message="The model returned an error; retrying or stopping safely",
                    status="model error",
                    step=step,
                    progress=self._state.progress,
                    plan_step="model request",
                    is_error=True,
                )
                if _is_terminal_model_error(error_text):
                    normalized = self._normalize_error_for_model(error_text)
                    final_message = f"Agent stopped: the model is unavailable. {normalized}"
                    break
                loop_post = time.monotonic()
                model_duration = getattr(self, "_last_model_duration", loop_post - loop_pre)
                lag_ms = ((loop_post - loop_pre) - model_duration) * 1000
                self._perf_logger.log_event_loop_lag(
                    lag_ms=lag_ms,
                    step=step,
                    context="model_call_error",
                )
                step += 1
                # Cooldown to avoid burning through all steps on transient API failures.
                # Skip if the model layer already exhausted its own retries (avoid double-sleep).
                if getattr(self, "_retries_exhausted", False):
                    continue
                cooldown = self.config.retry_base_delay * (2 ** min(self._state.consecutive_errors, 4))
                if any(m in error_text.casefold() for m in ("rate limit", "too many requests", "high demand", "503", "timeout")):
                    cooldown = 5.0
                time.sleep(cooldown)
                continue

            try:
                commands = parse_agent_commands(raw_text)
            except CommandValidationError as error:
                self._register_parse_error(
                    step,
                    raw_text,
                    redact_text(str(error)),
                )
                self._ui.update_live(
                    user_message="clarifying step format and continuing",
                    status="fixing response format",
                    step=step,
                    progress=self._state.progress,
                    plan_step="format correction",
                    is_error=True,
                )
                step += 1
                continue

            outcome, step = _run_action_batch(self, user_goal, step, commands)
            if outcome.finished:
                final_message = outcome.final_message
                report_rendered = outcome.report_rendered
                task_completed = True
                break

            # Only pipeline off a clean, successful step: after an error the
            # model tier and pending instruction are volatile, so predicting the
            # next prompt is unreliable (a mismatch just wastes the prefetch).
            if outcome.proceed:
                _run_pipeline_dwell(self, user_goal, step - 1)

        if not final_message:
            final_message = self._build_limit_reached_message()
    except KeyboardInterrupt:
        # Ctrl+C or Escape in the terminal, which lands wherever the run
        # happens to be. It ends the turn, not the process: the finally below
        # still seals the session, checkpoint and logs, and the user gets the
        # same report a cancel between steps produces. Anything the
        # interrupted action left half-written is a partial commit the journal
        # already records as unconfirmed for /resume to reconcile.
        final_message = _stop_by_user(self)
    finally:
        self._finalize_run(
            user_goal,
            task_completed=task_completed,
            primary_error=sys.exception(),
        )

    if not self.config.verbose and not report_rendered:
        self._ui.print_final(final_message, self._state.user_messages)

    return final_message


def _stop_by_user(self) -> str:
    self._state.stopped_by_user = True
    return self._build_cancellation_message()


def _run_action_batch(
    self, user_goal: str, step: int, commands: list[AgentCommand]
) -> tuple[_ActionOutcome, int]:
    """Run one response's actions in order; return the last outcome and the next step.

    Every action is its own step, so step logs, checkpoints, the pending-action
    record and the step budget keep meaning exactly what they did before
    batching — only the number of model calls goes down.
    """
    _note_thought(self, commands[0].thought)
    outcome = _BLOCKED
    for index, command in enumerate(commands):
        if index and step > self._state.step_budget:
            _note_skipped_actions(self, commands[index:])
            return outcome, step
        outcome = _run_one_action(self, user_goal, step, command)
        step += 1
        if outcome.finished:
            return outcome, step
        if not outcome.proceed:
            _note_skipped_actions(self, commands[index + 1 :])
            return outcome, step
    return outcome, step


def _note_skipped_actions(self, skipped: list[AgentCommand]) -> None:
    """Tell the model which actions of its batch never ran."""
    if not skipped:
        return
    names = ", ".join(command.action.type for command in skipped)
    note = (
        f"The batch stopped early: {len(skipped)} later action(s) were NOT executed ({names}). "
        "Decide the next actions from the results above; do not assume the skipped ones ran."
    )
    self._state.pending_instruction = f"{self._state.pending_instruction} {note}".strip()
    self._push_unique(self._state.recent_events, note, self.config.summary_recent_events)
    self._state.step_logs.append(
        {
            "status": "batch_actions_skipped",
            "skipped": [
                {"type": command.action.type, "args": command.action.args}
                for command in skipped
            ],
        }
    )


def _run_one_action(
    self, user_goal: str, step: int, command: AgentCommand
) -> _ActionOutcome:
    self._current_step = step
    # Safety refusal takes priority over every other guard (language,
    # repeat-block, report-artifact, read-only, completion-readiness):
    # an illegal/malicious request must stop immediately, unconditionally,
    # not queue behind corrective retries that assume the task is legitimate.
    if command.action.type == "refuse":
        return _stop_for_safety_refusal(self, step, command)

    if _reject_wrong_language(self, step, command):
        return _BLOCKED

    self._record_user_message(command.user_message)

    if _reject_before_execution(self, step, command):
        return _BLOCKED

    self._ui.print_step_header(
        step, command, getattr(self, "_last_reasoning_seconds", 0.0)
    )

    if _reject_exhausted_web_budget(self, user_goal, step, command):
        return _BLOCKED

    _note_run_context(
        self,
        action=command.action.description or command.action.type,
        files_changed=len(self._state.files_changed),
        model_tier=self._state.selected_action_role or "fast",
        **_plan_position(self),
    )
    self._ui.update_live(
        user_message=command.user_message,
        status="executing action",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
    )

    if command.action.type == "final_answer":
        return _finish_with_final_answer(self, step, command)
    if command.action.type == "report_to_user":
        return _finish_with_report(self, user_goal, step, command)
    return _execute_action(self, user_goal, step, command)


def _stop_for_safety_refusal(self, step: int, command: AgentCommand) -> _ActionOutcome:
    reason = str(command.action.args["reason"])
    self._register_safety_refusal(step, reason)
    final_message = self._build_safety_refusal_message()
    self._ui.update_live(
        user_message=final_message,
        status="stopped by safety filters",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )
    return _ActionOutcome(finished=True, final_message=final_message)


def _reject_wrong_language(self, step: int, command: AgentCommand) -> bool:
    language_error = self._command_language_error(command)
    if not language_error:
        return False
    self._register_language_error(step, command, language_error)
    self._ui.update_live(
        user_message="Fixing the model response language and retrying the step",
        status="fixing response language",
        step=step,
        progress=self._state.progress,
        plan_step="language correction",
        is_error=True,
    )
    return True


def _reject_before_execution(self, step: int, command: AgentCommand) -> bool:
    # Order matters: _is_repeat_blocked records the action as seen, so it must
    # not run for an action the follow-up guard already turned away.
    return (
        _reject_missing_followup(self, step, command)
        or _reject_repeated_action(self, step, command)
        or _reject_report_artifact(self, step, command)
        or _reject_read_only_violation(self, step, command)
    )


def _reject_missing_followup(self, step: int, command: AgentCommand) -> bool:
    followup_error = self._required_followup_error(command)
    if not followup_error:
        return False
    blocked_count = self._register_blocked_action(command)
    # blocked_actions is in-memory only; this breadcrumb rides in
    # recent_events (persisted + prompted) so a resumed run still
    # knows its last action was refused.
    self._push_unique(
        self._state.recent_events,
        f"Action '{command.action.type}' was blocked; corrective action required.",
        self.config.summary_recent_events,
    )
    self._state.stagnation_count += 1
    self._state.pending_instruction = followup_error
    if blocked_count >= 2:
        self._state.pending_instruction = _build_strict_next_action_instruction(
            self._state.task_language,
            self._state.required_next_actions,
            self._state.missing_artifacts,
        )
    self._state.step_logs.append(
        {
            "step": step,
            "status": "blocked_for_corrective_action",
            "blocked_count": blocked_count,
            "reason": followup_error,
            "required_next_actions": self._state.required_next_actions,
            "command": {
                "thought": command.thought,
                "plan_step": command.plan_step,
                "user_message": command.user_message,
                "action": {
                    "type": command.action.type,
                    "args": command.action.args,
                },
            },
        }
    )
    self._ui.update_live(
        user_message=command.user_message,
        status="corrective action required",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )
    return True


def _reject_repeated_action(self, step: int, command: AgentCommand) -> bool:
    if not self._is_repeat_blocked(command):
        return False
    if self._state.create_goal and not self._state.files_changed:
        self._set_required_next_actions(_WRITE_ACTIONS, _build_create_guidance(self._state.task_language))
    self._ui.update_live(
        user_message=command.user_message,
        status="looking for another working step",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )
    return True


def _reject_report_artifact(self, step: int, command: AgentCommand) -> bool:
    # Block report-like artifact creation at the UI/report layer only.
    # Never auto-complete here: the model may try to write task_report.txt
    # before doing any real work, and that must stay a corrective error.
    if not self._is_report_artifact_blocked(command):
        return False
    self._refresh_completion_requirements(force_enforcement=True)
    if self._state.completion_ready:
        feedback = {
            "ok": False,
            "action_type": command.action.type,
            "error": "Task is already complete. Do not write report artifacts. Use final_answer or report_to_user instead.",
        }
        live_status = "completion enforced"
        live_message = "Task is complete, requiring final_answer"
    else:
        feedback = {
            "ok": False,
            "action_type": command.action.type,
            "error": "Report artifacts are not part of the requested work. Do not create task_report.txt/summary.txt files. Continue the actual task and report through final_answer or report_to_user only at the end.",
        }
        live_status = "report artifact blocked"
        live_message = "Report artifact blocked; continue the actual task"
    self._register_feedback(step, command, feedback)
    self._ui.update_live(
        user_message=live_message,
        status=live_status,
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )
    return True


def _reject_read_only_violation(self, step: int, command: AgentCommand) -> bool:
    if not self._is_read_only_violation(command):
        return False
    feedback = {
        "ok": False,
        "action_type": command.action.type,
        "error": "This is a read-only/verify task. File modifications are not allowed. Use read-only actions (read_file, find_files, list_dir, git_status) and then final_answer or report_to_user.",
    }
    self._register_feedback(step, command, feedback)
    self._ui.update_live(
        user_message="Read-only task violation blocked",
        status="corrective action required",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )
    return True


def _reject_exhausted_web_budget(
    self, user_goal: str, step: int, command: AgentCommand
) -> bool:
    if command.action.type == "web_search":
        web_error = self._prepare_web_search_command(user_goal, command)
        live_status = "web search limit reached"
    elif command.action.type == "fetch_url":
        web_error = self._prepare_fetch_url_command(command)
        live_status = "URL read limit reached"
    else:
        return False
    if not web_error:
        return False
    feedback = self._build_error_feedback(command.action.type, web_error)
    self._register_feedback(step, command, feedback)
    _emit_action_result(self, step, command, feedback)
    self._ui.update_live(
        user_message=command.user_message,
        status=live_status,
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )
    return True


def _is_incomplete_finish(command: AgentCommand) -> bool:
    return command.action.args.get("outcome") == "incomplete"


def _finish_with_final_answer(self, step: int, command: AgentCommand) -> _ActionOutcome:
    if _is_incomplete_finish(command):
        # An honest "not done" claims nothing, so there is nothing for the
        # completion or substance gates to verify.
        final_message = str(command.action.args["message"])
        _register_incomplete_finish(self, step, final_message)
        return _ActionOutcome(finished=True, final_message=final_message)
    self._refresh_completion_requirements(force_enforcement=True)
    if self._state.completion_blockers:
        error_text = _join_blockers(self._state.completion_blockers)
        self._state.consecutive_errors += 1
        self._record_error(error_text)
        self._state.step_logs.append(
            {
                "step": step,
                "status": "blocked_final_answer",
                "reason": error_text,
                "command": {
                    "thought": command.thought,
                    "plan_step": command.plan_step,
                    "user_message": command.user_message,
                    "action": {
                        "type": command.action.type,
                        "args": command.action.args,
                    },
                },
            }
        )
        self._ui.update_live(
            user_message="Switching to a corrective action instead of stopping too early",
            status="final answer blocked",
            step=step,
            progress=self._state.progress,
            plan_step=command.plan_step,
            is_error=True,
        )
        return _BLOCKED
    if _flag_empty_data_cells(self, step, command):
        return _BLOCKED
    if _flag_removed_security_check(self, step, command):
        return _BLOCKED
    final_message = str(command.action.args["message"])
    if self._state.report_goal and not _is_substantive_answer(final_message):
        substance_retries = self._state.seen_actions.get("report_substance_retries", 0)
        if substance_retries < 1:
            self._state.seen_actions["report_substance_retries"] = substance_retries + 1
            self._register_feedback(
                step,
                command,
                {
                    "ok": False,
                    "action_type": "final_answer",
                    "error": _build_report_substance_instruction(self._state.task_language),
                },
            )
            self._set_required_next_actions(
                ["final_answer", "report_to_user"],
                _build_report_substance_instruction(self._state.task_language),
            )
            return _BLOCKED
        # The model would not answer substantively — report the miss
        # honestly instead of surfacing a content-free final answer.
        final_message = self._build_report_failure_message()
    self._register_final_answer(step, final_message)
    return _ActionOutcome(finished=True, final_message=final_message)


def _finish_with_report(
    self, user_goal: str, step: int, command: AgentCommand
) -> _ActionOutcome:
    incomplete = _is_incomplete_finish(command)
    if not incomplete:
        self._refresh_completion_requirements(force_enforcement=True)
    if not incomplete and self._state.completion_blockers:
        error_text = _join_blockers(self._state.completion_blockers, with_hint=False)
        self._state.consecutive_errors += 1
        self._record_error(error_text)
        self._state.pending_instruction = (
            f"Task is not complete. Blockers: {error_text} "
            "Fix the remaining issues before reporting. "
            f"{_HONEST_EXIT_HINT}"
        )
        self._ui.update_live(
            user_message="Report blocked — task not complete",
            status="report blocked",
            step=step,
            progress=self._state.progress,
            plan_step=command.plan_step,
            is_error=True,
        )
        return _BLOCKED
    if not incomplete and _flag_empty_data_cells(self, step, command):
        return _BLOCKED
    if not incomplete and _flag_removed_security_check(self, step, command):
        return _BLOCKED
    # Collect context and hand off to chat reporter
    summary = str(command.action.args.get("summary", ""))
    tech_result = str(command.action.args.get("result", ""))
    issues = str(command.action.args.get("issues", ""))
    outcome = "incomplete" if incomplete else "done"
    context = _build_report_context(self, user_goal, summary, tech_result, issues, outcome)
    report_text = _render_report(self, context)
    final_message = report_text or tech_result or summary
    if incomplete:
        _register_incomplete_finish(self, step, final_message)
    else:
        self._register_final_answer(step, final_message)
    return _ActionOutcome(finished=True, final_message=final_message, report_rendered=True)


def _register_incomplete_finish(self, step: int, message: str) -> None:
    self._state.finished_incomplete = True
    self._register_final_answer(step, message)


def _execute_action(
    self, user_goal: str, step: int, command: AgentCommand
) -> _ActionOutcome:
    # Crash-consistent side effects: durably record the pending action
    # BEFORE it executes so a process death in the action window is
    # reconciled on resume rather than blindly replayed. File mutations
    # are reconcilable from content digests; run_shell/delegate_task are
    # not (their effect cannot be proven after a crash) and resume treats
    # them as ambiguous. The post-step checkpoint clears the record.
    pending_action = _build_pending_action(self, command, step)
    if pending_action is not None:
        # Bind the executor's journal transaction to the SAME identity
        # recorded in the checkpoint, so a pending journal write can only
        # be promoted by this exact action.
        self.executor.begin_action_transaction(
            pending_action.get("run_id"), pending_action["action_id"]
        )
        self._save_checkpoint(user_goal, step, pending_action=pending_action)
    watch_output = getattr(self._ui, "print_action_output", None)
    if watch_output is not None and command.action.type == "run_shell":
        self.executor.set_output_sink(functools.partial(watch_output, step))
    try:
        feedback = self._execute_command(command)
    finally:
        self.executor.set_output_sink(None)
        if pending_action is not None:
            self.executor.end_action_transaction()
    self._register_feedback(step, command, feedback)
    _emit_action_result(self, step, command, feedback)
    ok = bool(feedback.get("ok"))

    # Store rolling summary on interval steps (every Nth step)
    if (
        step % self.config.rolling_summary_interval == 0
        and command.step_summary
    ):
        self._state.rolling_summaries[step] = command.step_summary

    # Only the model may declare the task done, and it may still want to
    # verify, so readiness is a hint rather than a gate.
    if self._state.completion_ready and ok:
        self._state.pending_instruction = (
            f"{self._state.pending_instruction} {_build_completion_hint(self)}".strip()
        )

    if (
        self._state.create_goal
        and not self._state.files_changed
        and ok
        and command.action.type in _READ_ONLY_ACTIONS
        and self._state.read_only_streak >= 2
    ):
        self._set_required_next_actions(_WRITE_ACTIONS, _build_create_guidance(self._state.task_language))

    self._ui.update_live(
        user_message=command.user_message,
        status="continuing" if ok else "error received",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=not ok,
    )

    # Checkpoint every step: mutating actions land on disk immediately,
    # so a coarser interval leaves resume state trailing the real
    # workspace. The atomic write is negligible next to the LLM call.
    self._save_checkpoint(user_goal, step)
    return _ActionOutcome(proceed=ok)


def reset_run_state(self) -> None:
    from agent_model import _join_pending_prefetch

    _join_pending_prefetch(self)
    self._state = RunState()


def execute_command(self, command: AgentCommand) -> dict[str, Any]:
    try:
        result = self.executor.execute(command)
    except ExecutorError as error:
        return self._build_error_feedback(
            command.action.type,
            redact_text(str(error)),
        )
    except Exception as error:
        raw_error = redact_text(
            f"Unexpected executor error: {type(error).__name__}: {error}"
        )
        return self._build_error_feedback(command.action.type, raw_error)

    if result.ok:
        safe_data = redact_value(result.data)
        return {
            "ok": True,
            "action_type": result.action_type,
            "data": safe_data,
        }

    error_text = "Action failed"
    if isinstance(result.data, dict):
        error_text = redact_text(str(result.data.get("error") or error_text))
        # Prefer stderr/stdout output from the actual command over generic error
        if error_text == "Action failed":
            stderr = str(result.data.get("stderr", "")).strip()
            stdout = str(result.data.get("stdout", "")).strip()
            if stderr:
                error_text = redact_text(stderr)
            elif stdout:
                error_text = redact_text(stdout)

    payload = self._build_error_feedback(result.action_type, error_text)
    if isinstance(result.data, dict):
        safe_data = redact_value(result.data)
        assert isinstance(safe_data, dict)
        payload["data"] = safe_data
    return payload


def build_error_feedback(
    self,
    action_type: str,
    raw_error: str,
) -> dict[str, Any]:
    safe_error = redact_text(str(raw_error))
    return {
        "ok": False,
        "action_type": action_type,
        "error": safe_error,
        "raw_error": safe_error,
        "normalized_error": self._normalize_error_for_model(safe_error),
    }


def command_language_error(self, command: AgentCommand) -> str | None:
    # Only the terminal user-facing text is policed. JSON keys, action types,
    # thought, and intermediate technical fields are protocol-level English
    # and are not language-checked; report_to_user output is rendered by the
    # reporter, which receives response_language itself.
    if command.action.type != "final_answer":
        return None
    language = self._state.response_language
    message = str(command.action.args.get("message", ""))
    if not _matches_response_language(message, language):
        language_name = "Russian" if language == "ru" else "English"
        return (
            f"Field final_answer.args.message is not written in {language_name} "
            f"(response_language: {language})."
        )
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
    language = self._state.response_language
    language_name = "Russian" if language == "ru" else "English"
    self._state.pending_instruction = (
        f"{error_text} The previous step was rejected and was not executed. "
        f"Retry the same intended step, but write final_answer.args.message in "
        f"{language_name} (response_language: {language}). "
        "JSON action keys and action types stay English."
    )
    self._state.step_logs.append(
        {
            "step": step,
            "status": "language_error",
            "error": error_text,
            "command": {
                "thought": command.thought,
                "plan_step": command.plan_step,
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
    error = redact_text(str(raw_error).strip()) or "Unknown error"
    lowered = error.casefold()
    
    is_overloaded = any(m in lowered for m in ("all providers exhausted", "rate limit", "too many requests", "high demand", "503"))
    
    if is_overloaded:
        return "Provider rate limit or high demand reached. Try again later or switch the provider."
    if any(m in lowered for m in ("insufficient balance", "insufficient credit", "payment required", "error code: 402")):
        return "Provider account has insufficient balance or credits (HTTP 402). Top up the account or switch the provider."
    if "invalid api key" in lowered:
        return "Provider API key is invalid. Check the configured key for the selected backend."
    if "no healthy backend available for model" in lowered:
        return "The backend required by the selected model is unavailable or misconfigured. Choose another model or fix the backend key."
    if "is not found for api version" in lowered or "not supported for generatecontent" in lowered:
        return "The selected model is not supported by this provider backend. Pick a model that matches the backend."
    return error


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
    message = "Invalid model response."
    self._record_error(message)
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
        if command.action.type in _READ_ONLY_ACTIONS:
            self._state.read_only_streak += 1
        else:
            self._state.read_only_streak = 0
    else:
        error_text = self._feedback_error_for_model(feedback)

        # ── Terminal condition: systemctl service not found ──
        terminal_not_found = (
            self._state.current_agent_role == "devops"
            and command.action.type == "run_shell"
            and _is_service_not_found_error(error_text)
        )

        if not terminal_not_found:
            self._state.consecutive_errors += 1
        if command.action.type in _WRITE_ACTIONS:
            self._state.read_only_streak = 0
        self._record_error(error_text)

        # Track security policy violations (.env access attempts)
        raw_error = str(feedback.get("raw_error") or feedback.get("error") or "")
        if "SECURITY PROTOCOL" in raw_error or "SECURITY POLICY" in raw_error or ".env" in raw_error:
            self._push_unique(
                self._state.security_policy_violations,
                raw_error[:200],
                limit=10,
            )
            # Ensure .env errors are permanently blocked: override pending instruction
            self._state.pending_instruction = (
                "SECURITY POLICY: Access to .env files is permanently forbidden. "
                "Do not retry this action. Choose a completely different approach "
                "that does not involve .env files."
            )
        elif terminal_not_found:
            service_name = _extract_service_name_from_not_found(error_text)
            # Treat not-found as valid verification — the command DID answer the question
            self._state.consecutive_errors = 0
            self._push_unique(
                self._state.verification_evidence,
                f"systemctl_status: {service_name} not found",
                limit=12,
            )
            # Mark related plan inspection steps as completed
            self._update_completed_plan_steps(command, feedback.get("data", {}))
            self._state.pending_instruction = (
                f"TERMINAL CONDITION: systemctl reports that {service_name} service "
                "is not found on this system. This IS the answer — the service unit "
                "does not exist. Do NOT run grep, list-unit-files, is-active, "
                "or any other systemctl command. Do NOT repeat status. "
                "Give final_answer or report_to_user with an honest message: "
                f"'Service {service_name} not found: the systemd unit does not exist on this system.'"
            )
        elif (
            command.action.type == "replace_in_file"
            # executor raises this without a trailing period; comparing against
            # the punctuated form silently disabled this recovery path.
            and error_text.rstrip(". ") == "old_text fragment not found in file"
        ):
            failed_path = str(command.action.args.get("path", "")).strip()
            if failed_path:
                self._set_required_next_actions(
                    {"read_file", "replace_in_file", "append_file", "write_file"},
                    _build_read_before_patch_instruction(self._state.task_language, failed_path),
                )
        else:
            self._state.pending_instruction = (
                "The previous action failed. "
                f"normalized_error: {error_text}. Do not repeat the same step without a new idea."
            )

    self._update_progress(command, ok)
    if not ok and not (
        self._state.current_agent_role == "devops"
        and command.action.type == "run_shell"
        and _is_service_not_found_error(self._feedback_error_for_model(feedback))
    ):
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
    self._update_recovery_tracking(
        command, ok, "" if ok else self._feedback_error_for_model(feedback)
    )
    self._refresh_completion_requirements()
    self._state.summary = self._build_summary()

    self._state.step_logs.append(
        {
            "step": step,
            "status": "ok" if ok else "error",
            "progress": self._state.progress,
            "command": {
                "thought": command.thought,
                "plan_step": command.plan_step,
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


def mark_file_write_applied(self, path: str) -> None:
    """The single runtime transition for "a file write to ``path`` succeeded".

    Used by both normal execution (``update_state_from_success``) and crash
    reconciliation (``_reconcile_pending_action``) so the two paths can never
    disagree about what a successful write means to agent state — in particular
    that the changed path re-enters ``pending_verification_paths`` and must be
    verified again before the task can complete.
    """
    if not path:
        return
    self._push_unique(self._state.files_changed, path)
    self._push_unique(self._state.pending_verification_paths, path)


def _empty_data_cells(path: str, content: str) -> list[str]:
    suffix = Path(path).suffix.casefold()
    if suffix not in {".csv", ".tsv"}:
        return []
    rows = list(csv.reader(io.StringIO(content), delimiter="\t" if suffix == ".tsv" else ","))
    if not rows:
        return []
    header = rows[0]
    cells: list[str] = []
    for line, row in enumerate(rows[1:], start=2):
        for index, value in enumerate(row):
            if value.strip():
                continue
            named = index < len(header) and header[index].strip()
            column = header[index].strip() if named else f"#{index + 1}"
            cells.append(f"{path} line {line}, column {column}")
    return cells


def _note_empty_data_cells(self, path: str, content: str) -> None:
    for cell in _empty_data_cells(path, content):
        if cell not in self._state.empty_data_cells:
            self._state.empty_data_cells.append(cell)
    del self._state.empty_data_cells[20:]


def _flag_empty_data_cells(self, step: int, command: AgentCommand) -> bool:
    """Ask once, before finishing, how blank cells in the data read were handled.

    A blank cell is missing data, not zero; the model had it in front of it and
    a prompt rule alone does not reliably make it say so.
    """
    if not self._state.empty_data_cells or self._state.empty_cells_flagged:
        return False
    self._state.empty_cells_flagged = True
    shown = "; ".join(self._state.empty_data_cells[:8])
    instruction = (
        f"Before finishing: the data you read has empty cells ({shown}). "
        "An empty cell is missing data, not zero. If your answer depends on any of "
        "them, say which cells were empty and how you handled them (skipped, or "
        "refused to compute); otherwise send the same answer again."
    )
    self._register_feedback(
        step, command, {"ok": False, "action_type": command.action.type, "error": instruction}
    )
    self._set_required_next_actions(["final_answer", "report_to_user"], instruction)
    return True


_SECURITY_CHECK_RE = re.compile(
    r"pip[- ]audit|npm audit|yarn audit|snyk|trivy|bandit|semgrep|codeql|gitleaks"
    r"|dependency-check|safety check|security[ -]scan",
    re.IGNORECASE,
)


def _removed_security_checks(workspace: Path) -> list[str]:
    removed = []
    for line in build_diff_report(workspace).splitlines():
        if line.startswith("-") and not line.startswith("---") and _SECURITY_CHECK_RE.search(line):
            removed.append(line[1:].strip())
    return removed


def _flag_removed_security_check(self, step: int, command: AgentCommand) -> bool:
    """Ask once, before finishing, why a security check the run deleted was failing.

    Deleting a failing scan turns the pipeline green without touching the
    finding; a prompt rule alone does not reliably make the model look first.
    """
    if self._state.security_removal_flagged:
        return False
    removed = _removed_security_checks(Path(self.executor.workspace))
    if not removed:
        return False
    self._state.security_removal_flagged = True
    instruction = (
        f"Before finishing: your changes removed a security check ({removed[0][:80]}). "
        "Find out why it was failing (its last output, the pins or code it examines). "
        "If it shows a real finding, fix the cause and restore the check. If you keep it "
        "removed, the final answer must say plainly that the check is off and which "
        "finding stays unaddressed."
    )
    self._register_feedback(
        step, command, {"ok": False, "action_type": command.action.type, "error": instruction}
    )
    self._set_required_next_actions(["final_answer", "report_to_user"], instruction)
    return True


def update_state_from_success(
    self,
    command: AgentCommand,
    feedback: dict[str, Any],
) -> None:
    action_type = command.action.type
    data = feedback.get("data", {})
    self._update_completed_plan_steps(command, data)

    if action_type == "read_file":
        path = str(data.get("path", "")).strip()
        self._push_unique(self._state.files_read, path)
        _note_empty_data_cells(self, path, str(data.get("content", "")))

    elif action_type == "read_multiple_files":
        for file_info in data.get("files", []):
            path = str(file_info.get("path", "")).strip()
            self._push_unique(self._state.files_read, path)
            _note_empty_data_cells(self, path, str(file_info.get("content", "")))

    elif action_type in {"write_file", "append_file", "replace_in_file"}:
        path = str(data.get("path", "")).strip()
        self._mark_file_write_applied(path)
        if self._state.web_facts:
            self._state.applied_facts.append({
                "path": path,
                "fact": self._state.web_facts[-1],
            })
            del self._state.applied_facts[:-20]

    elif action_type == "run_shell":
        _note_moved_files(self, command.action.args.get("command", []))

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

    elif action_type == "delegate_task":
        target_role = str(data.get("target_role", "")).strip()
        task = str(data.get("task", "")).strip()
        results = data.get("results", [])
        succeeded = sum(1 for r in results if r.get("ok"))
        observation = (
            f"Delegated to {target_role}: '{task[:80]}' — "
            f"{succeeded}/{len(results)} commands succeeded"
        )
        self._push_bounded(
            self._state.recent_events,
            observation,
            self.config.summary_recent_events,
        )

    self._update_verification_evidence(command, data)
    self._refresh_completion_requirements()


def update_progress(self, command: AgentCommand, ok: bool) -> None:
    """Advance progress from evidence only, never from a model-declared number.

    Completed plan steps are the real measure; the plan_step band is the
    fallback for runs with planning turned off, where there is no plan to
    count against. Both are monotonic, so progress never walks backwards.
    """
    target_progress = max(self._state.progress, self._plan_progress())

    if ok:
        target_progress = max(target_progress, _PLAN_STEP_BANDS.get(command.plan_step, 0))

    self._state.last_progress = self._state.progress
    self._state.progress = max(0, min(target_progress, 99))


def register_final_answer(self, step: int, message: str) -> None:
    incomplete = self._state.finished_incomplete
    if not incomplete:
        for item in _report_plan_steps(self):
            self._state.completed_plan_steps.add(item)
    self._refresh_completion_requirements()
    self._state.progress = _blocked_progress(self) if incomplete else 100
    self._state.run_status = "blocked" if incomplete else "completed"
    self._state.step_logs.append(
        {
            "step": step,
            "status": "final_answer",
            "outcome": "incomplete" if incomplete else "done",
            "progress": self._state.progress,
            "message": message,
        }
    )
    self._ui.debug_print(f"Result: final answer at step {step}")


def register_safety_refusal(self, step: int, reason: str) -> None:
    self._state.step_logs.append(
        {
            "step": step,
            "status": "safety_refusal",
            "reason": redact_text(reason),
        }
    )
    self._ui.debug_print(f"Result: safety refusal at step {step}: {reason}")


def plan_progress(self) -> int:
    if not self._state.plan_info or not self._state.plan_info.plan:
        return self._state.progress
    total = len(self._state.plan_info.plan)
    return round(_effective_completed_plan_steps(self) / total * 100)


def record_error(self, error_text: str) -> None:
    error_text = redact_text(str(error_text))
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
            if data.get("fallback_used"):
                return "ok: git status fallback used"
            return "ok: got git status"
        if action_type == "git_diff":
            if data.get("fallback_used"):
                return "ok: git diff fallback used"
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
        if action_type == "delegate_task":
            target_role = data.get("target_role", "unknown")
            results_count = len(data.get("results", []))
            if not results_count:
                return f"ok: handoff to {target_role} recorded only; nothing was executed or changed"
            return f"ok: delegated to {target_role}, {results_count} commands executed"
        return f"ok: executed action {action_type}"

    error_text = self._feedback_error_for_model(feedback)
    return f"error: {action_type}: {error_text}"


def feedback_to_detail(self, command: AgentCommand, feedback: dict[str, Any]) -> str:
    payload: dict[str, Any] = {
        "action_type": command.action.type,
        "ok": bool(feedback.get("ok")),
    }

    raw_data = feedback.get("data", {}) if feedback.get("ok") else None
    if feedback.get("ok"):
        safe_data = redact_value(raw_data)
        if command.action.type in {"run_shell", "delegate_task"}:
            safe_data = _replace_empty_terminal_streams_for_model(safe_data)
        payload["data"] = self._compact_for_prompt(safe_data)
    else:
        payload["normalized_error"] = self._feedback_error_for_model(feedback)

    serialized = json.dumps(payload, ensure_ascii=False, indent=2)
    serialized = self._truncate_for_prompt(serialized, self.config.prompt_result_char_limit)

    if isinstance(raw_data, dict) and raw_data.get("untrusted"):
        # JSON encoding leaves "</UNTRUSTED_TOOL_OUTPUT>" intact inside a
        # string, so a fetched page could otherwise close its own block and
        # continue as if it were the system prompt.
        return (
            f'<UNTRUSTED_TOOL_OUTPUT source="{command.action.type}">\n'
            f"{UNTRUSTED_CONTENT_NOTICE}\n"
            f"{defang_prompt_boundary_tags(serialized)}\n"
            "</UNTRUSTED_TOOL_OUTPUT>"
        )
    return serialized


def _replace_empty_terminal_streams_for_model(value: Any) -> Any:
    """Make empty terminal stdout/stderr explicit in model-facing payloads."""
    if isinstance(value, dict):
        normalized: dict[Any, Any] = {}
        for key, item in value.items():
            if key in {"stdout", "stderr"} and item == "":
                normalized[key] = "no output"
            else:
                normalized[key] = _replace_empty_terminal_streams_for_model(item)
        return normalized
    if isinstance(value, list):
        return [_replace_empty_terminal_streams_for_model(item) for item in value]
    return value


def render_feedback(self, summary_line: str, feedback: dict[str, Any]) -> str:
    ok = bool(feedback.get("ok"))
    status = "OK" if ok else "ERROR"
    raw_suffix = ""
    if not ok:
        raw_error = redact_text(
            str(feedback.get("raw_error") or feedback.get("error") or "").strip()
        )
        normalized_error = self._feedback_error_for_model(feedback)
        if raw_error and raw_error != normalized_error:
            raw_suffix = f" Raw error: {raw_error}."
    return (
        f"Result: {status}. {summary_line}. "
        f"Progress: {self._state.progress}%. "
        f"Stagnation: {self._state.stagnation_count}."
        f"{raw_suffix}"
    )


def build_cancellation_message(self) -> str:
    # Every route into this message is a user gesture (Ctrl+C or Escape in the
    # terminal, the GUI's cancel), so it says who stopped the run instead of
    # leaving "on request" to be read as an internal limit being hit.
    if self._state.response_language == "ru":
        return "Работа агента остановлена вами. Показано то, что агент успел сделать."
    return "Agent run stopped by you. Showing what the agent completed so far."


def build_early_stop_message(self, step: int) -> str | None:
    is_ru = self._state.response_language == "ru"
    # Highest priority: a cancellation requested between steps stops the loop on
    # this safe-stop path before any error/stagnation heuristic is consulted.
    if self._cancel_event.is_set():
        return build_cancellation_message(self)
    if self._state.consecutive_errors >= self.config.max_consecutive_errors:
        errors = _summarize_recent_errors(self)
        if is_ru:
            return (
                "Агент остановлен: слишком много ошибок подряд. "
                f"Последние ошибки: {errors}"
            )
        return (
            "Agent stopped. Too many consecutive errors. "
            f"Last errors: {errors}"
        )

    # Terminal condition: service not found — don't kill, let model give final_answer
    if (
        self._state.stagnation_count >= self.config.stagnation_limit
        and "TERMINAL CONDITION" not in (self._state.pending_instruction or "")
    ):
        last_actions = ", ".join(self._state.recent_feedback[-3:])
        if is_ru:
            return (
                "Агент остановлен: прогресса нет, похоже на зацикливание. "
                f"Последние действия: {last_actions}"
            )
        return (
            "Agent stopped. No progress detected, possible loop. "
            f"Last actions: {last_actions}"
        )

    return None


def build_limit_reached_message(self) -> str:
    errors = _summarize_recent_errors(self)
    blockers = ", ".join(self._state.completion_blockers[-3:])
    if self._state.response_language == "ru":
        message = (
            "Агент не успел завершить задачу за отведённый лимит шагов. "
            f"Текущий прогресс: {self._state.progress}%. "
            f"Последние ошибки: {errors}."
        )
        if blockers:
            message += f" Блокеры: {blockers}."
        return message
    message = (
        "Agent did not complete the task within the step limit. "
        f"Current progress: {self._state.progress}%. "
        f"Last errors: {errors}."
    )
    if blockers:
        message += f" Blockers: {blockers}."
    return message


def build_safety_refusal_message(self) -> str:
    """Fixed, model-independent stop message for the 'refuse' action.

    The wording is not taken from the model's own text (only the internal
    'reason' arg is, and that is logged, never surfaced) so a jailbroken or
    manipulated model cannot smuggle unsafe content into the final answer
    by pretending to refuse.
    """
    if self._state.response_language == "ru":
        return (
            "Нет, агентная система Kvist не будет выполнять такой запрос — "
            "это прямо противоречит принципам безопасности. Если задача на "
            "самом деле легальна, скажите об этом прямо, и я её выполню."
        )
    return (
        "No — the Kvist agent system will not carry out this request, it "
        "directly conflicts with safety principles. If this is actually a "
        "legal task, say so explicitly and I will do it."
    )


def _summarize_recent_errors(self) -> str:
    items: list[str] = []
    for raw_error in self._state.error_history[-5:]:
        normalized = self._normalize_error_for_model(raw_error)
        if normalized not in items:
            items.append(normalized)
    if not items:
        return "none"
    return ", ".join(items[:3])


def is_repeat_blocked(self, command: AgentCommand) -> bool:
    signature = self._action_signature(command)
    seen_count = self._state.seen_actions.get(signature, 0)
    # DevOps run_shell: warn early on repeated identical commands
    if (
        self._state.current_agent_role == "devops"
        and command.action.type == "run_shell"
        and seen_count >= 2
    ):
        if command.action.type not in self._state.required_next_actions:
            command_text = " ".join(command.action.args.get("command", []))
            message = (
                f"Do not repeat the same systemctl query [{command_text}]. "
                "Summarize the result or try a more specific read-only query. "
                "If the unit is not found, stop and report honestly."
            )
            self._state.pending_instruction = message
            self._state.stagnation_count += 1
            self._state.step_logs.append(
                {
                    "status": "repeat_blocked",
                    "command": {
                        "plan_step": command.plan_step,
                        "user_message": command.user_message,
                        "action": {
                            "type": command.action.type,
                            "args": command.action.args,
                        },
                    },
                }
            )
            self._ui.debug_print(f"DevOps repeat blocked: {command_text}")
            return True

    if seen_count >= self.config.repeat_action_limit:
        if command.action.type in self._state.required_next_actions:
            pass # allow it
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

    if command.action.type == "write_file":
        path = str(command.action.args.get("path", "")).strip()
        if path:
            target_signature = f"write_target::{path.casefold()}"
            target_seen = self._state.seen_actions.get(target_signature, 0)
            if target_seen >= self.config.repeat_action_limit + 1:
                target_exists = (Path(self.executor.workspace) / path).exists()
                if target_exists:
                    self._set_required_next_actions(
                        {"read_file", "replace_in_file", "append_file", "write_file"},
                        _build_read_before_patch_instruction(self._state.task_language, path),
                    )
                message = (
                    f"You are rewriting {path} too many times in a row. "
                    "Read the file first, switch to another required artifact, or verify the current result."
                )
                self._state.pending_instruction = message
                self._state.stagnation_count += 1
                self._state.step_logs.append(
                    {
                        "status": "repeat_write_target_blocked",
                        "path": path,
                        "command": {
                            "plan_step": command.plan_step,
                            "user_message": command.user_message,
                            "action": {
                                "type": command.action.type,
                                "args": command.action.args,
                            },
                        },
                    }
                )
                self._ui.debug_print(f"Repeat write target blocked: {path}")
                return True
            self._state.seen_actions[target_signature] = target_seen + 1

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
    # A batch shares one user_message across its actions; it is still one message.
    if not clean or self._state.user_messages[-1:] == [clean]:
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


def update_completed_plan_steps(self, command: AgentCommand, data: dict[str, Any]) -> None:
    plan_info = self._state.plan_info
    if not plan_info:
        return

    touched_paths = {
        str(data.get("path", "")).strip(),
        str(command.action.args.get("path", "")).strip(),
    }
    touched_paths = {path for path in touched_paths if path}

    matched_items = _collect_matching_plan_items(
        command,
        data,
        plan_info.plan,
        self._state.completed_plan_steps,
        touched_paths,
        self._state.files_changed,
    )
    for item in matched_items:
        self._state.completed_plan_steps.add(item)


def _collect_matching_plan_items(
    command: AgentCommand,
    data: dict[str, Any],
    plan_items: list[str],
    completed_items: set[str],
    touched_paths: set[str],
    files_changed: list[str],
) -> list[str]:
    evidence_text = _build_plan_evidence_text(command, data, touched_paths)
    semantic_matches: list[str] = []
    fallback_match: str | None = None

    for item in plan_items:
        if item in completed_items:
            continue
        normalized = item.casefold()
        action_match = _plan_item_matches_action(normalized, command.action.type, files_changed)
        if any(path.casefold() in normalized for path in touched_paths):
            semantic_matches.append(item)
            continue
        if _plan_item_matches_paths(normalized, touched_paths):
            semantic_matches.append(item)
            continue
        if action_match and _plan_item_semantically_matches(normalized, evidence_text):
            semantic_matches.append(item)
            continue
        if action_match:
            fallback_match = item
            if command.action.type not in _WRITE_ACTIONS:
                break

    if semantic_matches:
        return semantic_matches
    return [fallback_match] if fallback_match else []


def _build_plan_evidence_text(command: AgentCommand, data: dict[str, Any], touched_paths: set[str]) -> str:
    parts = [
        command.thought,
        command.user_message,
        command.plan_step,
        str(command.action.type),
        " ".join(sorted(touched_paths)),
    ]

    for key in ("path", "query", "pattern", "url"):
        value = command.action.args.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value)

    for key in ("content", "new_text", "old_text"):
        value = command.action.args.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value[:4000])

    for key in ("path", "query", "pattern", "url", "content", "title"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            parts.append(value[:4000])

    return "\n".join(part for part in parts if part)


def _plan_item_semantically_matches(item: str, evidence_text: str) -> bool:
    if not evidence_text.strip():
        return False

    item_terms = _extract_semantic_terms(item)
    if not item_terms:
        return False

    evidence_terms = _extract_semantic_terms(evidence_text)
    if not evidence_terms:
        return False

    matched = item_terms & evidence_terms
    if len(matched) >= 2:
        return True
    return bool(matched & _HIGH_SIGNAL_TERMS)


def _extract_semantic_terms(text: str) -> set[str]:
    terms: set[str] = set()
    for raw_token in _SEMANTIC_TOKEN_RE.findall(text):
        lowered = raw_token.casefold()
        if lowered in _PLAN_STOPWORDS:
            continue
        normalized = _normalize_semantic_token(lowered)
        if not normalized:
            continue
        terms.add(normalized)
    return terms


def _normalize_semantic_token(token: str) -> str:
    if token in {"ux", "ui", "crm", "api", "sdk", "sql", "db"}:
        return token
    if len(token) <= 2:
        return ""
    if len(token) >= 9:
        return token[:6]
    if len(token) >= 6:
        return token[:5]
    return token


def _diff_change_counts(diff_text: str) -> tuple[int, int]:
    """Count removed and added content lines in a unified diff.

    File headers (---/+++) are not content and are skipped.
    """
    removed = 0
    added = 0
    for line in diff_text.splitlines():
        if line.startswith("---") or line.startswith("+++"):
            continue
        if line.startswith("-"):
            removed += 1
        elif line.startswith("+"):
            added += 1
    return removed, added


def _is_destructive_diff(removed: int, added: int) -> bool:
    if removed < _DESTRUCTIVE_DIFF_MIN_REMOVED:
        return False
    return added < removed * _DESTRUCTIVE_DIFF_KEEP_RATIO


def update_verification_evidence(self, command: AgentCommand, data: dict[str, Any]) -> None:
    action_type = command.action.type
    evidence: str | None = None
    cleared_paths: list[str] = []

    if action_type in {"git_status", "git_diff"}:
        if data.get("fallback_used"):
            self._push_bounded(
                self._state.recent_events,
                "Git unavailable, use fallback verification",
                self.config.summary_recent_events,
            )
            if self._state.files_changed:
                self._set_required_next_actions(
                    _VERIFY_ONLY_ACTIONS,
                    _build_git_fallback_instruction(self._state.task_language),
                )
            return
        evidence = action_type
        # git_diff counts as verification, git_status does NOT
        if action_type == "git_diff":
            removed, added = _diff_change_counts(str(data.get("stdout", "")))
            if _is_destructive_diff(removed, added):
                # The diff is the evidence. A diff that deletes the file is
                # evidence of destruction, not of a fix, so it must not clear
                # the verification requirement or count as evidence at all.
                self._state.destructive_diff = (
                    f"git_diff shows {removed} lines removed and {added} added. "
                    "This is file destruction, not a targeted fix."
                )
                return
            # A clean diff after a destructive one means the file was restored.
            self._state.destructive_diff = ""
            cleared_paths = list(self._state.pending_verification_paths)
            self._state.pending_verification_paths.clear()
    elif action_type == "run_shell":
        command_text = " ".join(str(part) for part in data.get("command", []))
        if data.get("returncode") == 0:
            # For devops, any successful run_shell is verification evidence
            if self._state.current_agent_role == "devops" or _VERIFY_SHELL_RE.search(command_text):
                evidence = f"run_shell: {command_text}"
                cleared_paths = list(self._state.pending_verification_paths)
                self._state.pending_verification_paths.clear()
    elif action_type in {"list_dir", "list_tree", "find_files", "search_code"}:
        if self._state.create_goal or self._state.files_changed or self._state.verify_only_goal:
            if action_type == "find_files":
                if self._state.verify_only_goal and not (self._state.create_goal or self._state.files_changed):
                    pattern = str(data.get("pattern") or command.action.args.get("pattern", "")).strip()
                    evidence = f"find_files: {pattern}" if pattern else "find_files"
                else:
                    evidence = "find_files"
            else:
                evidence = action_type
        # search_code counts as verification
        if action_type == "search_code" and self._state.pending_verification_paths:
            cleared_paths = list(self._state.pending_verification_paths)
            self._state.pending_verification_paths.clear()
    elif action_type == "read_file":
        if self._state.files_changed or self._state.verify_only_goal:
            path = str(data.get("path", "")).strip()
            if path:
                evidence = f"read_file: {path}"
                # read_file of a changed file counts as verification
                for pv_path in list(self._state.pending_verification_paths):
                    if pv_path in path or path in pv_path:
                        self._state.pending_verification_paths.remove(pv_path)
                        cleared_paths.append(pv_path)

    elif action_type == "read_multiple_files":
        if self._state.files_changed or self._state.verify_only_goal:
            paths = [str(info.get("path", "")).strip() for info in data.get("files", [])]
            for path in filter(None, paths):
                evidence = f"read_file: {path}"
                for pv_path in list(self._state.pending_verification_paths):
                    if pv_path in path or path in pv_path:
                        self._state.pending_verification_paths.remove(pv_path)
                        cleared_paths.append(pv_path)

    if evidence:
        self._push_unique(self._state.verification_evidence, evidence, limit=12)


def refresh_completion_requirements(self, force_enforcement: bool = False) -> None:
    blockers, kinds, required_actions = _compute_completion_status(self)
    self._state.missing_artifacts = _missing_required_artifacts(self)
    self._state.completion_blockers = blockers
    self._state.completion_blocker_kinds = kinds

    # Set completion_ready flag: task is ready when no blockers and verification exists
    self._state.completion_ready = (
        len(blockers) == 0
        and len(self._state.verification_evidence) > 0
        and (len(self._state.files_changed) > 0 or not self._state.create_goal)
    )

    if blockers and force_enforcement:
        self._set_required_next_actions(
            required_actions,
            _build_completion_blocker_instruction(
                self._state.task_language,
                blockers,
                required_actions,
            ),
        )
        return

    if (
        self._state.create_goal
        and not self._state.files_changed
        and self._state.read_only_streak >= 2
    ):
        self._set_required_next_actions(_WRITE_ACTIONS, _build_create_guidance(self._state.task_language))
        return

    keep_explicit_restriction = bool(self._state.required_next_actions and self._state.pending_instruction)

    if not blockers:
        self._state.required_next_actions = []
    elif not keep_explicit_restriction:
        self._state.required_next_actions = []


def register_blocked_action(self, command: AgentCommand) -> int:
    """Record an action the corrective guard refused to execute.

    Deliberately kept out of ``seen_actions``. That dict drives the repeat
    budget in ``is_repeat_blocked``, and charging a refusal against it means
    the runtime penalises the model for an action the runtime itself declined
    to run. In run_001 this exhausted the budget for ``read_file main.py``
    before verification, so the verification reads were rejected as repeats.

    Returns the number of times this exact action has been blocked.
    """
    signature = self._action_signature(command)
    count = self._state.blocked_actions.get(signature, 0) + 1
    self._state.blocked_actions[signature] = count
    return count


def required_followup_error(self, command: AgentCommand) -> str | None:
    required_actions = set(self._state.required_next_actions)
    if not required_actions or command.action.type in required_actions:
        return None
    if command.action.type in _FINISH_ACTIONS and _is_incomplete_finish(command):
        return None

    # A read-only inspection is never a stall. Blocking it here is what forces
    # the model to mutate a file it has never read: the runtime demands a write
    # while the system prompt demands a prior read, and the runtime wins. The
    # budget keeps the escape hatch from turning into an inspect loop.
    if command.action.type in _READ_ONLY_ACTIONS and self._state.corrective_read_budget > 0:
        self._state.corrective_read_budget -= 1
        return None

    return _build_required_action_error(
        self._state.task_language,
        self._state.required_next_actions,
        self._state.completion_blockers,
    )


def _is_report_artifact_blocked(self, command: AgentCommand) -> bool:
    """Block report-like artifact creation unless the user explicitly requested it."""
    if command.action.type not in _WRITE_ACTIONS:
        return False

    path = str(command.action.args.get("path", "")).strip().lower()
    if not path:
        return False

    if not _looks_like_report_artifact_path(path):
        return False

    if _user_explicitly_requested_report_artifact(self, path):
        return False

    return True


def _is_read_only_violation(self, command: AgentCommand) -> bool:
    """Block write actions on a run the user or the role made read-only."""
    if not self._state.read_only_goal:
        return False

    # DevOps role: run_shell is always allowed — it's the primary devops tool
    if self._state.current_agent_role == "devops":
        if command.action.type == "run_shell":
            return False
        # Still block file writes for devops read-only tasks
        if command.action.type in _WRITE_ACTIONS:
            return True
        return False

    # Block write actions
    if command.action.type in _WRITE_ACTIONS:
        return True

    if command.action.type == "run_shell":
        command_parts = command.action.args.get("command", [])
        return not _is_safe_read_only_shell_command(command_parts)

    return False


def set_required_next_actions(self, actions: set[str] | list[str], message: str) -> None:
    self._state.required_next_actions = sorted(set(actions))
    self._state.pending_instruction = message
    self._state.corrective_read_budget = _CORRECTIVE_READ_BUDGET


def _matches_response_language(text: str, language: str) -> bool:
    if language not in {"ru", "en"}:
        return True
    cyrillic = sum(1 for ch in text if "а" <= ch.lower() <= "я" or ch.lower() == "ё")
    latin = sum(1 for ch in text if "a" <= ch.lower() <= "z")
    if cyrillic == 0 and latin == 0:
        # Purely technical output (paths, numbers, symbols) — no signal.
        return True
    if language == "ru":
        # A Russian final must contain at least some Russian; Latin-only
        # technical terms inside it are fine.
        return cyrillic > 0
    return cyrillic <= latin


def _token_match(word: str, text: str) -> bool:
    """Return True if `word` appears as a standalone token in `text`.

    Uses word-boundary regex where ``_`` (underscore) is treated as a
    *continuation* character so that short markers like ``list``, ``find``,
    ``show`` do NOT match inside filenames like ``empty_list.py`` or
    ``find_files.sh`` but DO match standalone English usage like
    ``"list files"`` or ``"find the bug"``.
    """
    # Word boundary: not preceded/followed by [a-zA-Z_] so underscore
    # keeps the preceding/following letter attached to the token.
    pattern = r"(?<![a-zA-Z_])" + re.escape(word) + r"(?![a-zA-Z_])"
    return bool(re.search(pattern, text))


def _is_create_goal(user_goal: str) -> bool:
    lowered = user_goal.casefold()
    markers = (
        "create ",
        "build ",
        "generate ",
        "scaffold",
        "bootstrap",
        "создай",
        "сгенерируй",
    )
    return any(marker in lowered for marker in markers)


# The router owns the change-verb vocabulary; the run loop reads the same list
# so a request the router treats as a change cannot be a read-only run here.
_is_modify_goal = has_change_verb


def _is_verify_only_goal(user_goal: str) -> bool:
    """Detect short verify/check tasks that should end after one factual confirmation.

    A guess, so it only relaxes completion accounting (a read counts as
    evidence, a found-nothing answer is a valid ending). It never blocks a
    write and never ends a run by itself.
    """
    if _is_create_goal(user_goal):
        return False
    # A FIX/MODIFY task is never verify-only.
    if _is_modify_goal(user_goal):
        return False

    lowered = user_goal.casefold()
    ru_patterns = (
        "проверь",
        "есть ли",
        "найди",
        "покажи",
        "посмотри",
        "выведи",
        "какой",
        "какая",
        "какое",
        "какие",
        "сколько",
        "git status",
        "git diff",
    )
    en_phrases = (
        "check if",
        "check whether",
        "git status",
        "git diff",
    )
    # Short single-word markers use word-boundary matching via _token_match to
    # avoid false positives on filenames ("list" inside "empty_list.py").
    en_token_markers = {"find", "show", "list", "inspect"}

    if any(pattern in lowered for pattern in ru_patterns + en_phrases):
        return True
    return any(_token_match(marker, lowered) for marker in en_token_markers)


def _resolve_report_goal(self, user_goal: str) -> bool:
    """A report task expects a substantive spoken answer, not just a mutation.

    The planner already classifies task intent, so its ``task_kind`` is the
    source of truth. The keyword heuristic (``_is_report_goal``) is used only
    when the planner produced no usable classification (e.g. the planner call
    failed), so intent detection is not duplicated in two live code paths.
    """
    plan = self._state.plan_info
    task_kind = getattr(plan, "task_kind", "") if plan is not None else ""
    if task_kind in {"report", "action"}:
        return task_kind == "report"
    return _is_report_goal(user_goal)


def _is_report_goal(user_goal: str) -> bool:
    """Fallback keyword heuristic for 'report vs action'. See _resolve_report_goal."""
    return infer_task_kind(user_goal) == "report"


def _is_substantive_answer(message: str) -> bool:
    """Reject the auto-completion stub and other content-free final answers.

    A genuine report answer is long enough to carry findings; the known stubs
    ("Готово, все проверки пройдены." / "Done. All checks passed.") are short
    and self-referential.
    """
    stripped = message.strip()
    if len(stripped) < 80:
        return False
    lowered = stripped.casefold()
    stub_phrases = (
        "все проверки пройдены",
        "all checks passed",
        "проверка пройдена",
        "verification confirmed",
    )
    if len(stripped) < 160 and any(phrase in lowered for phrase in stub_phrases):
        return False
    return True


def build_report_failure_message(self) -> str:
    """Honest failure for a report task the model never actually answered.

    Never claims success: a green "checks passed" here would hide the miss
    until the user goes digging.
    """
    if self._state.response_language == "ru":
        return (
            "Не удалось сформировать содержательный ответ на запрос. "
            "Нужные данные прочитаны, но выводы не были выданы — "
            "попробуйте повторить или переформулировать запрос."
        )
    return (
        "Could not produce a substantive answer to the request. "
        "The needed data was read, but no findings were returned — "
        "please retry or rephrase the request."
    )


def _build_report_substance_instruction(language: str) -> str:
    if language == "ru":
        return (
            "Ответь по сути на вопрос пользователя в final_answer.args.message: "
            "приведи конкретные находки и рекомендации со ссылками на прочитанные "
            "файлы. Шаблонные фразы вроде «все проверки пройдены» запрещены."
        )
    return (
        "Answer the user's question in final_answer.args.message: give concrete "
        "findings and recommendations that reference the files you read. Template "
        "phrases like 'all checks passed' are forbidden."
    )


def _build_create_guidance(language: str) -> str:
    return (
        "This is a CREATE task. Do not loop on inspect and do not use final_answer or report_to_user without changes. "
        "Choose a path inside the workspace and move to write_file or append_file, then verify with facts."
    )


def _build_premature_final_answer_error(language: str) -> str:
    return (
        "Completion blocked: this looks like a CREATE task, "
        "but no files were created or changed. "
        "If the files should be created, create them inside the workspace, then verify. "
        f"{_HONEST_EXIT_HINT}"
    )


def _build_missing_implementation_error(language: str) -> str:
    return (
        "Completion blocked: this looks like a FIX/MODIFY task, "
        "but no files were changed. If a change is needed, read the target file, "
        f"edit it, then verify. {_HONEST_EXIT_HINT}"
    )


def _build_completion_hint(self) -> str:
    hint = (
        "The task looks complete. If nothing is left to verify, finish with "
        "final_answer or report_to_user."
    )
    if self._state.report_goal:
        hint += " " + _build_report_substance_instruction(self._state.task_language)
    return hint


def _extract_required_artifacts(user_goal: str, plan_info: Any) -> list[str]:
    # Requirement: only from user_goal, not from plan_info.plan
    candidates: list[str] = []
    
    # Extensions to consider as artifacts
    valid_extensions = {
        ".py", ".txt", ".json", ".md", ".toml", ".yaml", ".yml", ".xml",
        ".kt", ".java", ".cpp", ".h", ".hpp", ".sh", ".ps1", ".bat"
    }
    
    # Domains and versions to ignore
    ignored_patterns = [
        r"^python\.org/?$", r"^docs\.python\.org/?$",
        r"^\d+\.x$", r"^\d+\.\d+(\.\d+)?$"
    ]
    ignored_re = re.compile("|".join(ignored_patterns), re.IGNORECASE)

    for match in _ARTIFACT_RE.findall(user_goal):
        cleaned = match.strip().strip(".,:;()[]{}\"'")
        if not cleaned:
            continue
            
        # Ignore URLs
        if cleaned.startswith("http://") or cleaned.startswith("https://"):
            continue
            
        # Must be a path with / or have a valid extension
        has_slash = "/" in cleaned
        ext = Path(cleaned).suffix.lower()
        
        if not (has_slash or ext in valid_extensions):
            continue
            
        # Ignore domains and versions
        if ignored_re.match(cleaned):
            continue
            
        candidates.append(cleaned)

    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        value = candidate.replace("\\", "/")
        while value.startswith("./"):
            value = value[2:]
        value = value.lstrip("/")
        if not value or value in {".", ".."}:
            continue
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(value)
    return normalized[:24]


_ARTIFACT_SEARCH_SKIP_DIRS = {".git", ".kvist", ".venv", "venv", "node_modules", "__pycache__"}


def _anchor_bare_artifacts(workspace: Path, artifacts: list[str]) -> list[str]:
    """Point a bare file name at the file it already names deeper in the workspace.

    "fix reminders.py" mentions a file that lives in src/; requiring it at the
    root made the completion gate demand a duplicate there.
    """
    bare = {a for a in artifacts if "/" not in a and not (workspace / a).exists()}
    if not bare:
        return artifacts
    found: dict[str, str] = {}
    for root, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in _ARTIFACT_SEARCH_SKIP_DIRS)
        for name in sorted(files):
            if name in bare and name not in found:
                found[name] = (Path(root) / name).relative_to(workspace).as_posix()
    return [found.get(a, a) for a in artifacts]


def _is_service_not_found_error(error_text: str) -> bool:
    """Detect systemctl 'Unit X could not be found' terminal condition."""
    return bool(_SYSTEMD_UNIT_NOT_FOUND_RE.search(error_text or ""))


def _is_service_inspection_goal(user_goal: str) -> bool:
    """Check if the user goal is about inspecting a specific systemd service."""
    return bool(_SERVICE_INSPECTION_GOAL_RE.search(user_goal or ""))


def _extract_service_name_from_not_found(error_text: str) -> str:
    """Extract service name from 'Unit nginx.service could not be found.'"""
    match = _SYSTEMD_UNIT_NOT_FOUND_RE.search(error_text or "")
    if match:
        matched = match.group(0)
        # Extract the service name: "Unit nginx.service could not be found"
        parts = matched.split()
        if len(parts) >= 2:
            return parts[1]  # e.g. "nginx.service"
    return "the requested"


def _is_terminal_model_error(error_text: str) -> bool:
    lowered = str(error_text).casefold()
    markers = (
        "quota",
        "resource_exhausted",
        "invalid api key",
        "authentication",
        "unauthorized",
        "decommissioned",
        "no longer supported",
        "no healthy backend available for model",
        "is not found for api version",
        "not supported for generatecontent",
        "does not exist",
        "not found",
        "insufficient balance",
        "insufficient credit",
        "payment required",
        "error code: 402",
    )
    return any(marker in lowered for marker in markers)
def _mv_rm_operands(argv: list[str]) -> tuple[str, list[str], str]:
    """(verb, sources, destination) of a mv/rm command; verb is "" for anything else."""
    args = [str(part) for part in argv]
    if args[:1] == ["git"]:
        args = args[1:]
    if not args or args[0] not in {"mv", "rm"}:
        return "", [], ""
    operands = [
        part.replace("\\", "/").removeprefix("./")
        for part in args[1:]
        if not part.startswith("-")
    ]
    if args[0] == "rm":
        return "rm", operands, ""
    if len(operands) < 2:
        return "", [], ""
    return "mv", operands[:-1], operands[-1]


def _paths_moved_or_removed(self) -> set[str]:
    """Paths a successful mv/rm in this run took away on purpose."""
    gone: set[str] = set()
    for log in self._state.step_logs:
        action = log.get("command", {}).get("action", {})
        if log.get("status") != "ok" or action.get("type") != "run_shell":
            continue
        _verb, sources, _dest = _mv_rm_operands(action.get("args", {}).get("command", []))
        gone.update(sources)
    return gone


def _note_moved_files(self, argv: list[str]) -> None:
    """A successful mv changed the files it landed; without this a pure rename
    reads as "no files changed" and the completion gate refuses it."""
    verb, sources, dest = _mv_rm_operands(argv)
    if not verb:
        return
    # A path taken away again (a temp name in a rename chain) cannot be verified.
    for source in sources:
        if source in self._state.files_changed:
            self._state.files_changed.remove(source)
        if source in self._state.pending_verification_paths:
            self._state.pending_verification_paths.remove(source)
    if verb != "mv":
        return
    into_dir = (Path(self.executor.workspace) / dest).is_dir()
    for source in sources:
        self._mark_file_write_applied(f"{dest}/{Path(source).name}" if into_dir else dest)


_REMOVAL_VERB_RE = re.compile(
    r"\b(?:delet|remov|wip|eras|purg|get rid)\w*|удал|убер|сотр|сотри|стер",
    re.IGNORECASE,
)


def _named_in_removal_request(user_goal: str, artifact: str) -> bool:
    """A file named in a sentence that asks to remove things is not one to create.

    rm is blocked, so a requested deletion often runs through a script that the
    mv/rm bookkeeping never sees; sentence scope keeps "leave coupons.py alone"
    protected next to "delete scratch.py".
    """
    name = artifact.rstrip("/").casefold()
    for sentence in re.split(r"(?<=[.!?;])\s+|\n", user_goal):
        if _REMOVAL_VERB_RE.search(sentence) and name in sentence.casefold():
            return True
    return False


def _missing_required_artifacts(self) -> list[str]:
    missing: list[str] = []
    workspace = Path(self.executor.workspace)
    gone = _paths_moved_or_removed(self)
    for artifact in self._state.required_artifacts:
        if artifact.rstrip("/") in gone:
            continue
        if _named_in_removal_request(self._state.user_goal, artifact):
            continue
        is_dir = artifact.endswith("/")
        target = workspace / _anchor_bare_artifacts(workspace, [artifact])[0].rstrip("/")
        if is_dir and not target.is_dir():
            missing.append(artifact)
        elif not is_dir and not target.exists():
            missing.append(artifact)
    return missing


def _compute_completion_status(self) -> tuple[list[str], list[str], list[str]]:
    """Blocker texts for the model, matching kind slugs for the UI, and the
    actions that would clear them."""
    blockers: list[str] = []
    kinds: list[str] = []
    required_actions: set[str] = set()

    if self._state.destructive_diff:
        kinds.append("destructive")
        blockers.append(
            self._state.destructive_diff
            + " Do not report success. Either restore the file from git "
            "(git checkout -- <path>) and verify with a clean git_diff, or stop and "
            "tell the user exactly what was deleted."
        )
        # Two exits, both allowed: restore the file, or report honestly. Leaving
        # only mutation here would repeat the trap that caused the destruction.
        required_actions.update({
            "git_status", "git_diff", "read_file", "run_shell",
            "final_answer", "report_to_user",
        })
    missing = _missing_required_artifacts(self)
    remaining_plan_steps = _remaining_plan_steps(self)
    actionable_remaining_plan_steps = [
        item for item in remaining_plan_steps
        if not _PLAN_REPORT_RE.search(item.casefold())
        and not _plan_step_is_non_blocking_after_progress(self, item)
        and not _plan_step_is_unreachable_by_any_action(item)
    ]
    verify_only_complete = (
        self._state.verify_only_goal
        and not self._state.files_changed
        and bool(self._state.verification_evidence)
    )

    if self._state.create_goal and not self._state.files_changed:
        kinds.append("not_created")
        blockers.append(_build_premature_final_answer_error(self._state.task_language))
        required_actions.update(_WRITE_ACTIONS)

    if self._state.modify_goal and not self._state.files_changed:
        kinds.append("not_modified")
        blockers.append(_build_missing_implementation_error(self._state.task_language))
        required_actions.update(_WRITE_ACTIONS)

    if (
        not verify_only_complete
        and not self._state.read_only_goal
        and not self._state.files_changed
        and _plan_requires_file_changes(self)
    ):
        kinds.append("not_modified")
        blockers.append(_build_missing_implementation_error(self._state.task_language))
        required_actions.update(_WRITE_ACTIONS)

    if actionable_remaining_plan_steps and not verify_only_complete:
        kinds.append("plan_incomplete")
        blockers.append(_build_incomplete_plan_error(self._state.task_language, actionable_remaining_plan_steps))
        if all(_PLAN_VERIFY_RE.search(item.casefold()) for item in actionable_remaining_plan_steps):
            required_actions.update(_VERIFY_ONLY_ACTIONS)
        elif _plan_requires_file_changes(self):
            required_actions.update(_WRITE_ACTIONS)
        # else: info-gathering / devops task — don't force write actions,
        # let the agent continue with whatever actions are appropriate

    if missing and not (self._state.read_only_goal and not self._state.files_changed):
        kinds.append("artifacts_missing")
        blockers.append(_build_missing_artifacts_error(self._state.task_language, missing))
        required_actions.update(_WRITE_ACTIONS)

    if self._state.files_changed and not self._state.verification_evidence:
        kinds.append("unverified")
        blockers.append(_build_missing_verification_error(self._state.task_language))
        required_actions.update(_VERIFY_ONLY_ACTIONS)

    if self._state.pending_verification_paths:
        kinds.append("unverified")
        joined = ", ".join(self._state.pending_verification_paths[:5])
        blockers.append(
            f"Task cannot finish: verification required for recently changed files: {joined}. "
            "Use git_diff, read_file, search_code, or run_shell (pytest/py_compile) to verify. "
            "git_status alone is NOT sufficient."
        )
        required_actions.update(_VERIFY_ONLY_ACTIONS)

    for kind, text in _evidence_completion_blockers(self):
        kinds.append(kind)
        blockers.append(text)
        required_actions.update(_WRITE_ACTIONS)

    return blockers, kinds, sorted(required_actions)


def _evidence_completion_blockers(self) -> list[tuple[str, str]]:
    """Blockers that hold regardless of plan-step accounting.

    Loose plan-step matching can mark every step complete on a run that read
    and changed nothing, which silences every plan-based blocker. These checks
    look only at hard evidence (files touched, recorded errors, denials).
    """
    st = self._state
    ru = st.task_language == "ru"
    out: list[tuple[str, str]] = []

    change_task = (
        st.create_goal
        or st.modify_goal
        or bool(st.required_artifacts)
        or _plan_requires_file_changes(self)
    )
    verify_only_done = st.verify_only_goal and bool(st.verification_evidence)
    if change_task and not st.read_only_goal and not verify_only_done and not st.files_changed:
        out.append((
            "no_changes",
            "Completion blocked: this task changes files but files_changed is empty. "
            f"Make the actual edit before claiming it done. {_HONEST_EXIT_HINT}"
            if not ru else
            "Завершение заблокировано: задача меняет файлы, но files_changed пуст. "
            f"Внеси реальную правку, прежде чем заявлять о готовности. {_HONEST_EXIT_HINT_RU}",
        ))

    if (
        st.progress >= 100
        and st.error_history
        and (st.recovery_pending or not st.files_changed)
    ):
        out.append((
            "errors_present",
            "Completion blocked: progress is 100% but there are unresolved errors "
            f"({st.error_history[-1]}). Resolve them. {_HONEST_EXIT_HINT}"
            if not ru else
            "Завершение заблокировано: прогресс 100%, но есть неустранённые ошибки "
            f"({st.error_history[-1]}). Устрани их. {_HONEST_EXIT_HINT_RU}",
        ))

    if (
        (change_task or st.error_history)
        and not st.files_read
        and not st.files_changed
        and not st.verification_evidence
    ):
        out.append((
            "no_activity",
            "Completion blocked: no file was read and no file was changed. "
            f"Inspect the workspace before claiming the work done. {_HONEST_EXIT_HINT}"
            if not ru else
            "Завершение заблокировано: ни один файл не прочитан и не изменён. "
            f"Изучи проект, прежде чем заявлять о готовности. {_HONEST_EXIT_HINT_RU}",
        ))

    return out


def _effective_completed_plan_steps(self) -> int:
    """Completed plan steps that survive scrutiny.

    On a clean run this is the raw matched count. On a blocked run the loose
    matcher is not trusted: without a file change or verification evidence
    nothing is closed, and the count can never exceed (total - remaining).
    """
    plan_info = self._state.plan_info
    if not plan_info or not plan_info.plan:
        return 0
    total = len(plan_info.plan)
    done = len(self._state.completed_plan_steps & set(plan_info.plan))
    if not self._state.completion_blockers:
        return max(0, min(done, total))
    if not self._state.files_changed and not self._state.verification_evidence:
        return 0
    remaining = len(_remaining_plan_steps(self))
    return max(0, min(done, total - remaining))


def _blocked_progress(self) -> int:
    plan_info = self._state.plan_info
    if not plan_info or not plan_info.plan:
        return min(self._state.progress, 99)
    return round(_effective_completed_plan_steps(self) / len(plan_info.plan) * 100)


def seal_run_status(self) -> None:
    """Fix the final run_status/progress/summary from one shared state.

    Runs at finalize so a run that stopped on the step budget or an early stop
    still reports "blocked" (not a stale in-progress percentage) when blockers
    remain, and progress reflects steps actually closed.
    """
    self._refresh_completion_requirements()
    if self._state.finished_incomplete or self._state.completion_blockers:
        self._state.run_status = "blocked"
        self._state.progress = _blocked_progress(self)
    elif self._state.progress >= 100:
        self._state.run_status = "completed"
    else:
        self._state.run_status = "in_progress"
    self._state.summary = self._build_summary()


def update_recovery_tracking(self, command: AgentCommand, ok: bool, error_text: str) -> None:
    """Maintain the post-hard-denial recovery counter.

    A hard denial resets the counter and arms the policy. Every later action
    with a new signature (success or failure) counts as one distinct strategy;
    two clear the policy.
    """
    if not ok and error_text and _is_hard_environment_denial(error_text):
        self._push_unique(
            self._state.hard_denial_rules,
            _hard_denial_rule_text(error_text),
            limit=6,
        )
        self._state.recovery_signatures = set()
        self._state.recovery_pending = True
        return

    if not self._state.recovery_pending:
        return
    if command.action.type in ("final_answer", "report_to_user"):
        return
    self._state.recovery_signatures.add(self._action_signature(command))
    if len(self._state.recovery_signatures) >= _RECOVERY_ATTEMPTS_REQUIRED:
        self._state.recovery_pending = False


def _plan_requires_file_changes(self) -> bool:
    plan_info = self._state.plan_info
    if not plan_info or not plan_info.plan:
        return self._state.create_goal
    return any(_PLAN_EDIT_RE.search(item.casefold()) for item in plan_info.plan)


def _remaining_plan_steps(self) -> list[str]:
    plan_info = self._state.plan_info
    if not plan_info:
        return []
    return [
        item for item in plan_info.plan
        if item not in self._state.completed_plan_steps
        and not _plan_step_resolved_by_alternative_branch(self, item)
    ]


def _report_plan_steps(self) -> list[str]:
    return [
        item for item in _remaining_plan_steps(self)
        if _PLAN_REPORT_RE.search(item.casefold())
    ]


def _plan_item_matches_action(item: str, action_type: str, files_changed: list[str]) -> bool:
    if action_type in _WRITE_ACTIONS:
        return bool(_PLAN_EDIT_RE.search(item))
    if action_type in {"read_file", "read_multiple_files", "list_dir", "list_tree", "find_files", "search_code", "web_search", "fetch_url"}:
        if files_changed and _PLAN_VERIFY_RE.search(item):
            return True
        return bool(_PLAN_INSPECT_RE.search(item))
    if action_type in {"git_status", "git_diff", "run_shell"}:
        # run_shell is the universal devops action — matches inspect, edit, or verify steps
        return True
    if action_type == "delegate_task":
        return bool(_PLAN_DELEGATE_RE.search(item))
    return False


def _plan_item_matches_paths(item: str, touched_paths: set[str]) -> bool:
    for path in touched_paths:
        lowered = path.casefold()
        parts = [part for part in lowered.split("/") if part]
        if lowered.startswith("handlers/") and "handlers/" in item:
            return True
        if lowered.startswith("keyboards/") and "keyboards/" in item:
            return True
        if lowered.startswith("db/") and "db/" in item:
            return True
        if lowered == "main.py" and "main.py" in item:
            return True
        if len(parts) >= 2:
            parent = parts[0] + "/"
            if parent in item:
                return True
    return False


def _build_missing_artifacts_error(language: str, missing: list[str]) -> str:
    joined = ", ".join(missing[:8])
    return f"Task cannot finish: required artifacts are still missing: {joined}."


def _build_incomplete_plan_error(language: str, remaining_steps: list[str]) -> str:
    preview = "; ".join(remaining_steps[:3])
    if len(remaining_steps) > 3:
        preview += f"; {len(remaining_steps) - 3} more"
    return (
        f"Task cannot finish as done: the execution plan is not complete yet. Remaining: {preview}. "
        f"{_HONEST_EXIT_HINT}"
    )


def _build_missing_verification_error(language: str) -> str:
    return "Task cannot finish without verification of the changes: structure, syntax, import, or run evidence is required."


def _join_blockers(blockers: list[str], with_hint: bool = True) -> str:
    # Each blocker names the honest exit on its own; joined, it is said once.
    texts = [
        text.replace(_HONEST_EXIT_HINT, "").replace(_HONEST_EXIT_HINT_RU, "").strip()
        for text in blockers
    ]
    if with_hint:
        texts.append(_HONEST_EXIT_HINT)
    return " ".join(texts)


def _build_completion_blocker_instruction(language: str, blockers: list[str], actions: list[str]) -> str:
    action_text = ", ".join(actions) or "none"
    blocker_text = _join_blockers(blockers, with_hint=False)
    return (
        f'final_answer and report_to_user with "outcome": "done" are forbidden. Blockers: {blocker_text} '
        f"Next, either take a corrective action using one of: {action_text}; "
        'or, if the work cannot or should not be done, finish with "outcome": "incomplete" and say why.'
    )


def _build_required_action_error(language: str, actions: list[str], blockers: list[str]) -> str:
    action_text = ", ".join(actions)
    blocker_text = _join_blockers(blockers)
    return f"A corrective action is required. Allowed next actions: {action_text}. Blockers: {blocker_text}"


def _build_strict_next_action_instruction(
    language: str,
    actions: list[str],
    missing_artifacts: list[str],
) -> str:
    action_text = ", ".join(actions) or "none"
    first_artifact = missing_artifacts[0] if missing_artifacts else ""
    message = (
        f"You again chose a forbidden action type. The next action.type must be one of: {action_text}. "
        "Do not use inspect/read/list_tree/find/search/final_answer/report_to_user on the next step. "
        "For a CREATE task, switch directly to changing files inside the workspace. "
        'The one exception: if the work cannot or should not be done, finish with "outcome": "incomplete" and say why.'
    )
    if first_artifact:
        message += f" Start with artifact: {first_artifact}."
    return message


def _build_read_before_patch_instruction(language: str, path: str) -> str:
    return (
        f"Stop rewriting {path} blindly. Read {path} first, then use replace_in_file, "
        "append_file, or one deliberate write_file based on the actual content."
    )


def _build_git_fallback_instruction(language: str) -> str:
    return (
        "Git is unavailable or the repo is not initialized. Do not fail the task: "
        "switch to list_tree, read_file, find_files, or run_shell to verify structure, syntax, and execution."
    )


def _plan_step_resolved_by_alternative_branch(self, item: str) -> bool:
    normalized = item.casefold().strip()
    if not normalized:
        return False

    completed = {step.casefold().strip() for step in self._state.completed_plan_steps}
    if not completed:
        return False

    if _PLAN_EXISTS_RE.match(normalized):
        return any(_PLAN_NOT_EXISTS_RE.match(step) for step in completed)
    if _PLAN_NOT_EXISTS_RE.match(normalized):
        return any(_PLAN_EXISTS_RE.match(step) for step in completed)
    return False


def _plan_step_is_unreachable_by_any_action(item: str) -> bool:
    """Whether no action in the protocol could ever mark *item* complete.

    Plan steps are matched to actions by vocabulary. A planner writing an
    analysis task produces judgement steps — "Оценить инсталляторы по
    критериям", "Сопоставить документацию с кодом" — that name no inspect,
    edit, verify or report verb, so nothing the agent can do will ever complete
    them. Left blocking, they deadlock the run: completion stays forbidden
    forever while the model is told the plan is unfinished, so it keeps
    inspecting and re-reads the same files until the step budget dies.

    Such a step is not skipped work; its deliverable *is* the final answer. It
    simply cannot be a gate. Every gate that a real action could satisfy is
    untouched.
    """
    normalized = item.casefold().strip()
    if not normalized:
        return False
    return not any(
        pattern.search(normalized)
        for pattern in (_PLAN_INSPECT_RE, _PLAN_EDIT_RE, _PLAN_VERIFY_RE, _PLAN_REPORT_RE)
    )


def _plan_step_is_non_blocking_after_progress(self, item: str) -> bool:
    normalized = item.casefold().strip()
    if not normalized:
        return False

    has_meaningful_progress = bool(self._state.files_changed or self._state.verification_evidence)
    if not has_meaningful_progress:
        return False

    is_structure_inspect = (
        bool(_PLAN_STRUCTURE_INSPECT_RE.search(normalized))
        and not _PLAN_EDIT_RE.search(normalized)
    )
    is_inspect_only = (
        bool(_PLAN_INSPECT_RE.search(normalized))
        and not _PLAN_EDIT_RE.search(normalized)
        and not _PLAN_VERIFY_RE.search(normalized)
    )
    return is_structure_inspect or is_inspect_only


def _looks_like_report_artifact_path(path: str) -> bool:
    normalized = path.replace("\\", "/").strip().casefold()
    filename = Path(normalized).name
    if filename in _REPORT_ARTIFACT_PATTERNS:
        return True

    parsed = Path(filename)
    if parsed.suffix not in _REPORT_ARTIFACT_EXTENSIONS:
        return False

    stem = parsed.stem
    return any(keyword in stem for keyword in _REPORT_ARTIFACT_KEYWORDS)


def _user_explicitly_requested_report_artifact(self, path: str) -> bool:
    goal = self._state.user_goal.casefold()
    normalized = path.replace("\\", "/").strip().casefold()
    filename = Path(normalized).name

    requested_artifacts = {
        item.replace("\\", "/").strip().casefold()
        for item in self._state.required_artifacts
    }
    if normalized in requested_artifacts or filename in requested_artifacts:
        return True

    if normalized in goal or filename in goal:
        return True

    report_words = (
        "report",
        "summary",
        "status file",
        "completion report",
        "task report",
        "отч",
        "сводк",
        "резюм",
        "итог",
    )
    request_words = (
        "create",
        "write",
        "save",
        "generate",
        "prepare",
        "создай",
        "сохрани",
        "сгенерируй",
        "подготов",
    )
    return any(word in goal for word in report_words) and any(word in goal for word in request_words)


def _is_sqlite3_statement_read_only(statement: str) -> bool:
    """Classify one already-casefolded SQL statement or dot-command.

    `statement` is one `;`-separated piece of a single sqlite3 CLI argument.
    """
    if not statement:
        return True
    if statement.startswith("."):
        return statement in _SAFE_READ_ONLY_SQLITE_DOT_COMMANDS
    if statement.startswith("select"):
        return True
    if statement.startswith("pragma"):
        # "PRAGMA x = value" mutates state even for an otherwise-safe name.
        if "=" in statement:
            return False
        pragma_name = statement[len("pragma"):].strip().split("(", 1)[0].strip()
        return pragma_name in _SAFE_READ_ONLY_SQLITE_PRAGMAS
    if _UNSAFE_SQLITE_STATEMENT_RE.match(statement):
        return False
    # An unrecognized statement shape (e.g. a bare ATTACH without a keyword
    # match above) fails closed rather than being assumed read-only.
    return False


def _is_safe_read_only_sqlite3_command(parts: list[str]) -> bool:
    """True only if every statement passed to the sqlite3 CLI is read-only.

    `parts` are casefolded argv tokens including the leading "sqlite3".
    Workspace/path validation for the database file argument is the
    executor's/terminal's job (see terminal.py `_validate_common_tokens`),
    not this classifier's — it only judges SQL/dot-command intent.
    """
    positional = [token for token in parts[1:] if not token.startswith("-")]
    if len(positional) < 2:
        # No SQL/dot-command supplied (just a filename, or nothing at all):
        # there is no query to justify running the CLI for a read-only task.
        return False

    statement_tokens = positional[1:]  # positional[0] is the database file
    for token in statement_tokens:
        for raw_statement in token.split(";"):
            if not _is_sqlite3_statement_read_only(raw_statement.strip()):
                return False
    return True


def _is_safe_read_only_shell_command(command_parts: Any) -> bool:
    if not isinstance(command_parts, list) or not command_parts:
        return False

    parts = [str(part).strip().casefold() for part in command_parts if str(part).strip()]
    if not parts:
        return False

    joined = " ".join(parts)
    if any(marker in joined for marker in _READ_ONLY_SHELL_DENYLIST):
        return False

    program = parts[0]
    if program == "git":
        return len(parts) >= 2 and parts[1] in _SAFE_READ_ONLY_GIT_SUBCOMMANDS
    if program in {"python", "python3"}:
        return len(parts) == 2 and parts[1] in {"--version", "-v"}
    if program == "sqlite3":
        return _is_safe_read_only_sqlite3_command(parts)
    return program in _SAFE_READ_ONLY_SHELLS
