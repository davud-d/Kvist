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
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.change_journal import build_diff_report

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

from kernel.action_result import SKIPPED_ERROR, build_action_result
from kernel.agent_state import PLAN_PLANNING, RunCancelled, RunState
from kernel.executor import UNTRUSTED_CONTENT_NOTICE, ExecutorError
from kernel.call_labels import call_label
from kernel.native_tools import (
    OPENING_NOTE,
    READONLY_TOOLS,
    NativeToolsRejected,
    NativeTurn,
    assistant_message,
    calls_from_turn,
    tool_definition,
    tool_message,
    tools_for_role,
)
from kernel.parser import (
    MAX_BATCH_ACTIONS,
    CommandValidationError,
    build_command,
    parse_model_response,
    plan_step_for_calls,
)
from kernel.protocol import Action, AgentCommand, ModelResponse, ToolCallRequest
from kernel.redaction import defang_prompt_boundary_tags, redact_text, redact_value
from kernel import end_session, plan_mode
from kernel.role_router import has_change_verb, has_no_write_marker
from kernel.task_intent import asks_for_explanation, infer_task_kind, is_create_goal

_READ_ONLY_ACTIONS = {
    "read_file", "read_multiple_files", "list_dir",
    "list_tree", "find_files", "search_code",
}

_WRITE_ACTIONS = {"write_file", "append_file", "replace_in_file"}
# Beyond the file reads, what plan mode still allows: it asks the person and
# looks things up, and never changes the workspace.
_PLANNING_EXTRA_ACTIONS = {
    "git_status", "git_diff", "web_search", "fetch_url", "propose_plan", "offer_plan", "refuse",
}
_VERIFY_ONLY_ACTIONS = {"git_status", "git_diff", "run_shell", "list_tree", "find_files", "read_file"}

# Progress floor per phase the core derives from a response's calls. "done" is
# absent: 100 belongs to the terminal action, not to a phase label.
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
    "user denied the change, environment blocks it), end your turn again with a "
    "response that has no tool call and says plainly what was not done and why; "
    "it is recorded as not done."
)
_HONEST_EXIT_HINT_RU = (
    "Если работу нельзя или не нужно выполнять (цели нет, предпосылка ложна, "
    "пользователь отклонил правку, среда не даёт), закончи ход ещё раз ответом "
    "без вызовов и прямо скажи, что не сделано и почему; ход запишется как незавершённый."
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
    notify(
        build_action_result(
            step,
            command.action.type,
            command.action.args,
            feedback,
            call_id=command.call_id,
            batch_id=command.batch_id,
        )
    )


_BLOCKER_LABELS = {
    "destructive": ("массовое удаление — нужна сверка", "mass deletion — needs review"),
    "not_created": ("файл ещё не создан", "file not created yet"),
    "not_modified": ("правки ещё не внесены", "no changes made yet"),
    "artifacts_missing": ("не хватает файлов", "required files missing"),
    "unverified": ("ждёт проверки правок", "changes not verified"),
    "no_changes": ("файлы не изменены", "no file changed"),
    "errors_present": ("есть неустранённые ошибки", "unresolved errors"),
    "no_activity": ("ничего не прочитано и не изменено", "nothing read or changed"),
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


def _run_position(self) -> dict[str, Any]:
    """Measured facts the live panel shows in place of a declared percentage.

    Why completion is being withheld, how close the run is to the stagnation
    limit, and the step budget.
    """
    return {
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


def _note_response_text(self, text: str) -> None:
    """Give the live panel the words of the latest response.

    They are the thinking column's fallback for providers that stream no
    reasoning. Optional channel, skipped by UIs without ``set_last_thought``.
    """
    notify = getattr(self._ui, "set_last_thought", None)
    if notify is not None:
        notify(text)


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
_VERIFY_SHELL_RE = re.compile(r"py_compile|compileall|pytest|python(?:3)?\s+-m\s+|python(?:3)?\s+-c|python(?:3)?\s+\S+\.py(?:\s|$)|uv\s+run|node\s+--check")

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
        "approved_plan": state.approved_plan,
        "errors": state.error_history[-5:] if state.error_history else [],
    }
    safe_context = redact_value(context)
    assert isinstance(safe_context, dict)
    return json.dumps(safe_context, ensure_ascii=False, indent=2)


def _render_report(self, context: str) -> str:
    """Call the chat model to render a report from agent context. Streams markdown to console."""
    from kernel.agent_model import (
        _emit_usage,
        build_report_system_prompt,
        safe_extract_completion_content,
        append_reasoning_delta,
        safe_extract_reasoning_delta,
        safe_extract_stream_delta,
        _create_json_completion,
    )

    system_prompt = build_report_system_prompt(self)
    stream_model = self.config.fast_model
    messages = [
        {"role": "system", "content": system_prompt},
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
            input_tokens=self._count_tokens(system_prompt) + self._count_tokens(context),
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
        input_tokens=self._count_tokens(system_prompt) + self._count_tokens(context),
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
    cleanup_steps: list[tuple[str, Any]] = [
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
    self._state.report_goal = _is_report_goal(user_goal)
    # "check if main.py exists" names a file it must not be required to create.
    if not self._state.required_artifacts and not (read_only_goal or verify_only_goal):
        self._state.required_artifacts = _anchor_artifacts(
            Path(self.executor.workspace),
            _extract_required_artifacts(user_goal),
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

            if self._state.native_tools is None:
                self._state.native_tools = _native_tools_for_run(self)
            self._current_step = step
            loop_pre = time.monotonic()
            turn: NativeTurn | None = None
            try:
                if self._state.native_tools:
                    turn = _call_native_turn(self, user_goal, step)
                    response = ModelResponse(text=turn.content.strip(), calls=calls_from_turn(turn))
                else:
                    prompt = self._build_runtime_prompt(user_goal, step)
                    _note_run_context(self, action="waiting for the model")
                    response = parse_model_response(self._call_action_model(prompt))
            except RunCancelled:
                # Cancelled mid-generation: the stream was closed and no action
                # ran, so exit through the same safe-stop finalization as a
                # between-steps cancel — report rendered, session/checkpoint
                # written, no half-applied work.
                final_message = _stop_by_user(self)
                break
            except NativeToolsRejected as error:
                _leave_native_tools(self, error)
                continue
            except Exception as error:
                stop_message = _handle_model_error(self, step, error, loop_pre)
                if stop_message:
                    final_message = stop_message
                    break
                step += 1
                continue

            outcome, step = _run_turn(self, user_goal, step, response, turn)
            if outcome.finished:
                final_message = outcome.final_message
                report_rendered = outcome.report_rendered
                task_completed = True
                break
            self._save_checkpoint(user_goal, step - 1)

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
        self._ui.print_final(
            final_message, self._state.user_messages, outcome=_final_outcome(self, task_completed)
        )

    return final_message


def _final_outcome(self, task_completed: bool) -> str:
    """"done" only for a run that finished and passed its completion gates; "closed" for end_session's close."""
    state = self._state
    if state.session_closed:
        return "closed"
    refused = any(log.get("status") == "safety_refusal" for log in state.step_logs)
    if task_completed and not state.finished_incomplete and state.run_status != "blocked" and not refused:
        return "done"
    return "incomplete"


def _stop_by_user(self) -> str:
    self._state.stopped_by_user = True
    return self._build_cancellation_message()


def _handle_model_error(self, step: int, error: Exception, loop_pre: float) -> str | None:
    """Record a failed model call and cool down; the stop message when it is terminal."""
    error_text = redact_text(str(error).strip() or type(error).__name__)
    self._state.consecutive_errors += 1
    self._record_error(error_text)
    self._state.step_logs.append({"step": step, "status": "model_error", "error": error_text})
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
        return f"Agent stopped: the model is unavailable. {normalized}"
    loop_post = time.monotonic()
    model_duration = getattr(self, "_last_model_duration", loop_post - loop_pre)
    self._perf_logger.log_event_loop_lag(
        lag_ms=((loop_post - loop_pre) - model_duration) * 1000,
        step=step,
        context="model_call_error",
    )
    # Cooldown to avoid burning through all steps on transient API failures.
    # Skip if the model layer already exhausted its own retries (avoid double-sleep).
    if getattr(self, "_retries_exhausted", False):
        return None
    cooldown = self.config.retry_base_delay * (2 ** min(self._state.consecutive_errors, 4))
    if any(m in error_text.casefold() for m in ("rate limit", "too many requests", "high demand", "503", "timeout")):
        cooldown = 5.0
    time.sleep(cooldown)
    return None


def _native_tools_for_run(self) -> bool:
    from kernel.agent_model import native_tools_supported, resolve_action_model

    return native_tools_supported(self, resolve_action_model(self))


def _leave_native_tools(self, error: Exception) -> None:
    """Continue the run on the text protocol after the provider refused tools.

    The runtime prompt the text path builds already carries the recent results,
    so the switch loses no work — only the conversation.
    """
    from kernel.agent_model import forget_native_tools

    forget_native_tools(self, self._state.selected_action_model)
    self._state.native_tools = False
    self._state.native_messages = []
    self._ui.print_error(
        "Провайдер отказался от вызова инструментов; этот прогон продолжается на текстовом протоколе."
        if self._state.response_language == "ru"
        else "The provider refused tool calls; this run continues with the text protocol."
    )
    self._ui.debug_print(f"[native tools] {redact_text(str(error))}")


def _call_native_turn(self, user_goal: str, step: int) -> NativeTurn:
    from kernel.agent_model import _load_role_prompt, build_role_system_prompt, call_native_action_model

    state = self._state
    role = state.current_agent_role
    tools = tools_for_role(_load_role_prompt(role))
    if end_session.available(self, role):
        tools.append(tool_definition(end_session.ACTION))
    state.native_tool_defs = plan_mode.tools_for_phase(tools, self)
    if not state.native_messages:
        state.native_messages = [
            {"role": "system", "content": build_role_system_prompt(self, role, native_tools=True)},
            {"role": "user", "content": self._build_runtime_prompt(user_goal, step) + OPENING_NOTE},
        ]
        state.native_note_sent = state.pending_instruction
    elif state.pending_instruction and state.pending_instruction != state.native_note_sent:
        state.native_messages.append(
            {"role": "user", "content": f"Additional instruction:\n{state.pending_instruction}"}
        )
        state.native_note_sent = state.pending_instruction
    _note_unchanged_run(state)
    _note_run_context(self, action="calling tools")
    return call_native_action_model(self, state.native_messages, state.native_tool_defs)


_UNCHANGED_NOTE_EVERY = 10


def _note_unchanged_run(state: RunState) -> None:
    """Tell a run that should change files how long it has gone without doing so.

    A native conversation carries no step counter, so a model circling in
    analysis cannot see from inside that thirty calls changed nothing. The note
    is a fact about its own work, not an order: what it means is the
    model's to weigh. A read-only task is expected to only read, so it gets none.
    """
    if state.files_changed or state.plan_phase == PLAN_PLANNING or not (state.modify_goal or state.create_goal):
        return
    calls = sum(1 for message in state.native_messages if message.get("role") == "tool")
    mark = calls // _UNCHANGED_NOTE_EVERY
    if mark <= state.native_unchanged_mark:
        return
    state.native_unchanged_mark = mark
    state.native_messages.append(
        {"role": "user", "content": f"Run so far: {calls} tool calls, and none of them changed a file."}
    )


def _run_turn(
    self, user_goal: str, step: int, response: ModelResponse, turn: NativeTurn | None
) -> tuple[_ActionOutcome, int]:
    """Run one model response: its calls, or the end of the turn when it has none.

    ``turn`` is the native response the calls came from; every one of its calls
    is answered in the conversation, in call order, because an API rejects a
    turn with an unanswered call.
    """
    if turn is not None:
        self._state.native_messages.append(assistant_message(turn))
    if any(call.name == end_session.ACTION for call in response.calls):
        # The tool speaks: words the model wrote beside it never reach the person.
        response = ModelResponse(text="", calls=response.calls)
    if not response.calls:
        if not response.text:
            return _register_empty_response(self), step + 1
        return _finish_turn(self, user_goal, step, response.text), step + 1
    if response.text:
        _note_response_text(self, response.text)
        self._record_user_message(response.text)
    planned = _plan_calls(self, response, uuid.uuid4().hex[:12])
    # A silent response still leaves a trace of what it set out to do.
    _keep_step_summary(self, step, response.text or _calls_note(planned))
    outcome, next_step, answers = _run_calls(self, user_goal, step, planned)
    if turn is not None:
        for item, answer in zip(planned, answers):
            self._state.native_messages.append(tool_message(item.request.call_id, answer))
    # A response whose calls all failed to parse ran nothing, but it still
    # costs a step: otherwise a model repeating a broken call never meets
    # the budget.
    return outcome, max(next_step, step + 1)


def _register_empty_response(self) -> _ActionOutcome:
    self._state.consecutive_errors += 1
    self._record_error("The model returned neither a tool call nor text.")
    # A fact, not a nudge: "or finish with final_answer" was once taken
    # literally, and a progress line went out as the final answer.
    self._state.pending_instruction = (
        "Your last response reached the runtime empty: no text and no tool call. "
        "If you made a call, it was lost on the way and did not run."
    )
    return _BLOCKED


def _finish_turn(self, user_goal: str, step: int, text: str) -> _ActionOutcome:
    """A response without calls ends the turn: its text is the answer, through the finish gates."""
    if self._state.plan_phase == PLAN_PLANNING:
        self._state.pending_instruction = (
            "Plan mode is on, so the turn ends with propose_plan: it is how the person sees "
            "your plan and answers the questions in it. Put open questions in the plan."
        )
        return _BLOCKED
    command = AgentCommand(
        plan_step="done",
        action=Action(
            type="final_answer",
            args={"message": text},
            description=call_label("final_answer", {}, self._state.response_language or "en"),
        ),
        batch_id=uuid.uuid4().hex[:12],
    )
    return _run_one_action(self, user_goal, step, command)


def _run_final_answer(self, user_goal: str, step: int, command: AgentCommand) -> _ActionOutcome:
    """An end of the turn, through every guard and the finish gates.

    The first end the runtime refuses sends the model the reasons. An end
    after that, while the completion blockers still hold, is its answer to
    them: accepted, but as "not done", with one line from the core saying why.
    A run can then never claim what its evidence does not show, and can never
    be trapped between a refused finish and nothing left to do.
    """
    if self._state.finish_refusals and not _is_incomplete_finish(command):
        self._refresh_completion_requirements(force_enforcement=True)
        if self._state.completion_blockers:
            message = str(command.action.args["message"]) + _incomplete_note(self)
            command.action.args.update({"message": message, "outcome": "incomplete"})
    outcome = (
        _finish_with_final_answer(self, step, command)
        if _prepare_action(self, user_goal, step, command)
        else _BLOCKED
    )
    if not outcome.finished:
        self._state.finish_refusals += 1
    return outcome


def _incomplete_note(self) -> str:
    """The core's own line under an answer accepted as not done."""
    ru = self._state.response_language == "ru"
    labels = [
        _BLOCKER_LABELS[kind][0 if ru else 1]
        for kind in dict.fromkeys(self._state.completion_blocker_kinds)
        if kind in _BLOCKER_LABELS
    ]
    reason = "; ".join(labels) or ("проверки завершения не пройдены" if ru else "completion checks did not pass")
    if ru:
        return f"\n\n_Kvist: задача не отмечена как выполненная ({reason})._"
    return f"\n\n_Kvist: the task is not marked as done ({reason})._"


def _calls_note(planned: list[_PlannedCall]) -> str:
    return " · ".join(
        item.command.action.description if item.command is not None else item.request.name or "?"
        for item in planned
    )


@dataclass(slots=True, frozen=True)
class _PlannedCall:
    """A call of the response: the command to run, or why it cannot run."""

    request: ToolCallRequest
    command: AgentCommand | None
    error: str = ""
    over_limit: bool = False

    @property
    def readonly(self) -> bool:
        return self.request.name in READONLY_TOOLS


# Read-only calls of one response that may run at the same time.
MAX_PARALLEL_CALLS = 4
_NOT_RUN_AFTER_FINISH = "Not executed: an earlier call in this response already ended the task."
_NOT_RUN_BESIDE_REFUSAL = "Not executed: this response refuses the task."
_NOT_RUN_BUDGET = "Not executed: the step budget ran out."
_SKIPPED_AFTER_FAILED_WRITE = SKIPPED_ERROR
_SKIPPED_LABELS = ("пропущено: предыдущая запись упала", "skipped: an earlier write failed")


def _over_limit(calls: list[ToolCallRequest]) -> list[bool]:
    """Per call: whether it is past the cap. Reads are exempt; the other calls are capped."""
    others = 0
    flags: list[bool] = []
    for request in calls:
        if request.name in READONLY_TOOLS:
            flags.append(False)
            continue
        others += 1
        flags.append(others > MAX_BATCH_ACTIONS)
    return flags


def _plan_calls(self, response: ModelResponse, batch_id: str) -> list[_PlannedCall]:
    over_limit = _over_limit(response.calls)
    kept = [call for call, over in zip(response.calls, over_limit) if not over]
    plan_step = plan_step_for_calls([call.name for call in kept], bool(self._state.files_changed))
    language = self._state.response_language or "en"
    planned: list[_PlannedCall] = []
    for number, (request, over) in enumerate(zip(response.calls, over_limit), start=1):
        if over:
            planned.append(_PlannedCall(request, None, (
                f"Not executed: the protocol accepts at most {MAX_BATCH_ACTIONS} calls besides reads "
                "per response; send this one again in your next response if you still need it."
            ), over_limit=True))
            continue
        try:
            command = build_command(
                request, text=response.text, batch_id=batch_id, plan_step=plan_step, language=language
            )
        except CommandValidationError as error:
            reason = redact_text(str(error))
            if not request.error:
                reason = f"Tool call {number} ({request.name}) was not run: {reason}"
            planned.append(_PlannedCall(request, None, reason))
            continue
        planned.append(_PlannedCall(request, command))
    return planned


def _run_calls(
    self, user_goal: str, step: int, planned: list[_PlannedCall]
) -> tuple[_ActionOutcome, int, list[str]]:
    """Run a response's calls in call order and answer every one of them.

    Consecutive read-only calls run as one group, in parallel; a writing call
    runs alone. Every call is its own step, so step logs, checkpoints, the
    pending-action record and the step budget keep their meaning. A failed
    read costs only itself. A failed or refused write stops the writes after
    it — a later write may rest on it — but not the reads.
    """
    answers: list[str] = []
    not_run: list[str] = []
    outcome = _ActionOutcome()
    write_failed = False
    refusal = next(
        (item for item in planned if item.command is not None and item.command.action.type == "refuse"),
        None,
    )
    index = 0
    while index < len(planned):
        item = planned[index]
        if outcome.finished or (refusal is not None and item is not refusal):
            answers.append(_NOT_RUN_AFTER_FINISH if outcome.finished else _NOT_RUN_BESIDE_REFUSAL)
            index += 1
            continue
        if item.over_limit:
            answers.append(item.error)
            not_run.append(item.error)
            index += 1
            continue
        if item.command is None:
            _register_unreadable_call(self, item.error)
            answers.append(item.error)
            not_run.append(item.error)
            write_failed = write_failed or not item.readonly
            index += 1
            continue
        if item.readonly:
            group = _readonly_run(planned, index)
            step, group_answers, group_not_run = _run_readonly_group(
                self, user_goal, step, group, first_in_response=index == 0
            )
            answers.extend(group_answers)
            not_run.extend(group_not_run)
            index += len(group)
            continue
        index += 1
        if step > self._state.step_budget and index > 1:
            answers.append(_NOT_RUN_BUDGET)
            not_run.append(f"{item.command.action.description}: {_NOT_RUN_BUDGET}")
            continue
        if write_failed:
            _show_skipped_call(self, step, item.command)
            step += 1
            answers.append(_SKIPPED_AFTER_FAILED_WRITE)
            not_run.append(f"{item.command.action.description}: {_SKIPPED_AFTER_FAILED_WRITE}")
            continue
        serial = getattr(self, "_result_serial", 0)
        result = _run_one_action(self, user_goal, step, item.command)
        step += 1
        answer = _call_answer(self, serial, result)
        answers.append(answer)
        if result.finished:
            outcome = result
        elif not result.proceed:
            write_failed = True
            if getattr(self, "_result_serial", 0) == serial:
                not_run.append(f"{item.command.action.description}: {answer}")
    _note_calls_not_run(self, not_run)
    return outcome, step, answers


def _readonly_run(planned: list[_PlannedCall], start: int) -> list[_PlannedCall]:
    """The read-only calls that follow each other from ``start``, at most a response's worth."""
    group: list[_PlannedCall] = []
    for item in planned[start:]:
        if item.command is None or not item.readonly:
            break
        group.append(item)
    return group


def _run_readonly_group(
    self, user_goal: str, step: int, group: list[_PlannedCall], *, first_in_response: bool
) -> tuple[int, list[str], list[str]]:
    """Guards in call order, the executor in parallel, results registered in call order.

    Only the executor call leaves the main thread: every guard and every
    change to the run state stays where it was, so a group behaves like the
    same reads made one by one, just sooner.
    """
    answers: list[str] = [""] * len(group)
    not_run: list[str] = []
    ready: list[tuple[int, int, int]] = []  # (position, step, result serial)
    for position, item in enumerate(group):
        command = item.command
        assert command is not None
        if step > self._state.step_budget and (position or not first_in_response):
            answers[position] = _NOT_RUN_BUDGET
            not_run.append(f"{command.action.description}: {_NOT_RUN_BUDGET}")
            continue
        serial = getattr(self, "_result_serial", 0)
        if _prepare_action(self, user_goal, step, command):
            ready.append((position, step, serial))
        else:
            answers[position] = _call_answer(self, serial, _BLOCKED)
            if getattr(self, "_result_serial", 0) == serial:
                not_run.append(f"{command.action.description}: {answers[position]}")
        step += 1
    feedbacks = _execute_in_parallel(self, [group[position].command for position, _step, _serial in ready])
    for (position, call_step, serial), feedback in zip(ready, feedbacks):
        result = _finish_action(self, call_step, group[position].command, feedback)
        answers[position] = _call_answer(self, serial, result)
    return step, answers, not_run


def _execute_in_parallel(self, commands: list[AgentCommand]) -> list[dict[str, Any]]:
    """Executor results in the order of ``commands``, whatever order they finish in."""
    if len(commands) < 2:
        return [self._execute_command(command) for command in commands]
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL_CALLS, thread_name_prefix="kvist-read") as pool:
        return list(pool.map(self._execute_command, commands))


def _register_unreadable_call(self, error: str) -> None:
    """A call the model wrote but the runtime could not read counts as a format error."""
    self._state.parse_errors += 1
    self._state.consecutive_errors += 1
    self._record_error(error)
    self._state.step_logs.append({"status": "call_format_error", "error": error})
    self._ui.debug_print(f"Tool call format error: {error}")


def _call_answer(self, serial: int, outcome: _ActionOutcome) -> str:
    """The executed call's result, or the reason the runtime did not run it."""
    if outcome.finished:
        return "Accepted."
    if getattr(self, "_result_serial", 0) != serial:
        return self._last_result_detail
    reason = self._state.pending_instruction or "The runtime refused this call."
    self._state.native_note_sent = self._state.pending_instruction
    return f"Not executed: {reason}"


def _show_skipped_call(self, step: int, command: AgentCommand) -> None:
    """A write the response asked for but the runtime did not run, shown in its place."""
    self._ui.print_step_header(step, command, 0.0)
    feedback = {
        "ok": False,
        "action_type": command.action.type,
        "error": _SKIPPED_AFTER_FAILED_WRITE,
    }
    _emit_action_result(self, step, command, feedback)
    self._state.step_logs.append(
        {"step": step, "status": "skipped_after_failed_write", "command": _command_log(command)}
    )
    self._ui.update_live(
        user_message=f"{command.action.description}: {_SKIPPED_LABELS[0 if self._state.response_language == 'ru' else 1]}",
        status="call skipped",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )


def _note_calls_not_run(self, not_run: list[str]) -> None:
    """Tell a text-mode model which of its calls did not run, and why.

    A native model reads it in each call's answer. A text-mode model sees
    results only through the next prompt, where a call that never ran has no
    result, and a later successful call clears the refusal it left behind.
    """
    if not not_run:
        return
    self._state.step_logs.append({"status": "calls_not_run", "reasons": not_run})
    if self._state.native_tools:
        return
    pending = self._state.pending_instruction
    fresh = [reason for reason in not_run if reason not in pending]
    if fresh:
        note = "Not every call of your last response ran: " + " ".join(fresh)
        self._state.pending_instruction = f"{pending} {note}".strip()


def _command_log(command: AgentCommand) -> dict[str, Any]:
    return {
        "plan_step": command.plan_step,
        "text": command.text,
        "call_id": command.call_id,
        "batch_id": command.batch_id,
        "action": {"type": command.action.type, "args": command.action.args},
    }


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

    if command.action.type == "final_answer":
        return _run_final_answer(self, user_goal, step, command)

    if command.action.type == end_session.ACTION:
        return _run_end_session(self, step, command)

    if not _prepare_action(self, user_goal, step, command):
        return _BLOCKED

    if command.action.type == "report_to_user":
        return _finish_with_report(self, user_goal, step, command)
    if command.action.type in plan_mode.PLAN_ACTIONS:
        return _run_plan_action(self, step, command)
    return _execute_action(self, user_goal, step, command)


def _run_end_session(self, step: int, command: AgentCommand) -> _ActionOutcome:
    """end_session, outside every guard: the person sees only its fixed texts.

    Its answer goes on the runtime's own channel, never wrapped as untrusted
    tool output: it holds only numbers and the runtime's words.
    """
    result = end_session.handle(self, command, step=step)
    if not result.ok:
        self._state.consecutive_errors += 1
        self._record_error(result.answer)
    _keep_result_in_view(
        self, self._action_signature(command), end_session.runtime_block(command.call_id, result.answer)
    )
    if result.final_message:
        self._state.session_closed = True
        return _ActionOutcome(finished=True, final_message=result.final_message)
    return _ActionOutcome(proceed=result.ok)


def _run_plan_action(self, step: int, command: AgentCommand) -> _ActionOutcome:
    """offer_plan / propose_plan: a dialog with the person instead of an executor call."""
    run = plan_mode.offer_plan if command.action.type == "offer_plan" else plan_mode.propose_plan
    result = run(self, command)
    outcome = _finish_action(self, step, command, result.feedback)
    if result.instruction:
        self._state.pending_instruction = result.instruction
    if result.ended:
        plan = str(command.action.args["plan"]).strip()
        return _ActionOutcome(finished=True, final_message=plan_mode.ended_message(self._state, plan))
    return outcome


def _prepare_action(self, user_goal: str, step: int, command: AgentCommand) -> bool:
    """Every guard that runs before an action, and its header on screen.

    False when a guard turned the action away; the reason is already in
    pending_instruction or in a registered error result.
    """
    self._current_step = step
    if _reject_wrong_language(self, step, command):
        return False

    if _reject_before_execution(self, step, command):
        return False

    self._ui.print_step_header(
        step, command, getattr(self, "_last_reasoning_seconds", 0.0)
    )

    if _reject_exhausted_web_budget(self, user_goal, step, command):
        return False

    _note_run_context(
        self,
        action=command.action.description or command.action.type,
        files_changed=len(self._state.files_changed),
        model_tier=self._state.selected_action_role or "fast",
        **_run_position(self),
    )
    self._ui.update_live(
        user_message=command.action.description,
        status="executing action",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
    )
    return True


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
        or _reject_plan_misuse(self, step, command)
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
            "command": _command_log(command),
        }
    )
    self._ui.update_live(
        user_message=command.action.description,
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
        user_message=command.action.description,
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
            "error": "Task is already complete. Do not write report artifacts. Give your final answer: a response with no tool call.",
        }
        live_status = "completion enforced"
        live_message = "Task is complete, requiring final_answer"
    else:
        feedback = {
            "ok": False,
            "action_type": command.action.type,
            "error": "Report artifacts are not part of the requested work. Do not create task_report.txt/summary.txt files. Continue the actual task and report in your final answer (a response with no tool call) only at the end.",
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


def _reject_plan_misuse(self, step: int, command: AgentCommand) -> bool:
    if command.action.type not in plan_mode.PLAN_ACTIONS:
        return False
    reason = plan_mode.rejection_reason(self, command)
    if not reason:
        return False
    self._register_feedback(step, command, {"ok": False, "action_type": command.action.type, "error": reason})
    self._ui.update_live(
        user_message=command.action.description,
        status="plan action refused",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=True,
    )
    return True


def _reject_read_only_violation(self, step: int, command: AgentCommand) -> bool:
    if not self._is_read_only_violation(command):
        return False
    if self._state.plan_phase == PLAN_PLANNING:
        error = (
            "Plan mode: files stay read-only until the person accepts your plan. Explore with "
            "read-only calls, then call propose_plan."
        )
    else:
        error = "This is a read-only/verify task. File modifications are not allowed. Use read-only actions (read_file, find_files, list_dir, git_status) and then give your final answer (a response with no tool call)."
    feedback = {
        "ok": False,
        "action_type": command.action.type,
        "error": error,
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
        user_message=command.action.description,
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
        # The prompt no longer lists blockers on every step, so the refusal
        # itself is where the model learns what "done" still lacks.
        self._state.pending_instruction = f"Your finish was not accepted as done. {error_text}"
        self._state.step_logs.append(
            {
                "step": step,
                "status": "blocked_final_answer",
                "reason": error_text,
                "command": _command_log(command),
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
        self.executor.set_output_sink(functools.partial(watch_output, step, call_id=command.call_id))
    try:
        feedback = self._execute_command(command)
    finally:
        self.executor.set_output_sink(None)
        if pending_action is not None:
            self.executor.end_action_transaction()
            self._anchored_artifacts = None
    outcome = _finish_action(self, step, command, feedback)
    # A side effect is on disk the moment it runs, so its checkpoint cannot
    # wait. A read has nothing to recover — resume simply reads again — and is
    # saved once with the rest of its batch (run_loop): each durable write
    # costs up to a second on a spinning disk, several per action.
    if pending_action is not None:
        self._save_checkpoint(user_goal, step)
    return outcome


def _finish_action(
    self, step: int, command: AgentCommand, feedback: dict[str, Any]
) -> _ActionOutcome:
    """Everything that follows an executed action, on the main thread."""
    self._current_step = step
    self._register_feedback(step, command, feedback)
    _emit_action_result(self, step, command, feedback)
    ok = bool(feedback.get("ok"))


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
        and self._state.plan_phase != PLAN_PLANNING
    ):
        self._set_required_next_actions(_WRITE_ACTIONS, _build_create_guidance(self._state.task_language))

    self._ui.update_live(
        user_message=command.action.description,
        status="continuing" if ok else "error received",
        step=step,
        progress=self._state.progress,
        plan_step=command.plan_step,
        is_error=not ok,
    )
    return _ActionOutcome(proceed=ok)


def reset_run_state(self) -> None:
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
            f"Your final answer is not written in {language_name} "
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
        f"Retry the same intended step, but write your final answer in "
        f"{language_name} (response_language: {language}). "
        "Tool names and argument keys stay English."
    )
    self._state.step_logs.append(
        {
            "step": step,
            "status": "language_error",
            "error": error_text,
            "command": _command_log(command),
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


def _keep_step_summary(self, step: int, note: str) -> None:
    """Carry the model's own words into every later prompt.

    Nothing else the model concludes outlives its call: its reasoning is not
    shown again. A response's text is kept once, whether or not its calls ran:
    words that announce a finding before a refused edit still hold the finding.
    """
    summaries = self._state.rolling_summaries
    if not note or (summaries and summaries[max(summaries)] == note):
        return
    summaries[step] = note


def _keep_result_in_view(self, signature: str, detail: str) -> None:
    """Show the newest result of each distinct action, newest last.

    A repeated action replaces its own earlier copy, so a second git_status
    does not cost a file read its place in the prompt.
    """
    self._result_serial = getattr(self, "_result_serial", 0) + 1
    self._last_result_detail = detail
    details = self._state.recent_result_details
    signatures = self._state.recent_result_signatures
    if len(signatures) != len(details):
        signatures[:] = [""] * len(details)
    if signature in signatures:
        index = signatures.index(signature)
        del signatures[index]
        del details[index]
    signatures.append(signature)
    details.append(detail)
    limit = self.config.detailed_feedback_limit
    del signatures[:-limit]
    del details[:-limit]


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
            self._state.pending_instruction = (
                f"TERMINAL CONDITION: systemctl reports that {service_name} service "
                "is not found on this system. This IS the answer — the service unit "
                "does not exist. Do NOT run grep, list-unit-files, is-active, "
                "or any other systemctl command. Do NOT repeat status. "
                "Give your final answer with an honest message: "
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
    _keep_result_in_view(self, signature, detail_line)
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
            "command": _command_log(command),
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

    The phase of the response's calls sets a floor; progress is monotonic, so
    it never walks backwards.
    """
    target_progress = self._state.progress

    if ok:
        target_progress = max(target_progress, _PLAN_STEP_BANDS.get(command.plan_step, 0))

    self._state.last_progress = self._state.progress
    self._state.progress = max(0, min(target_progress, 99))


def register_final_answer(self, step: int, message: str) -> None:
    incomplete = self._state.finished_incomplete
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

    # Every result is its own block, named by the call it answers, so a batch's
    # results stay apart. JSON encoding leaves "</UNTRUSTED_TOOL_OUTPUT>" intact
    # inside a string, so a fetched page could otherwise close its own block
    # and continue as if it were the system prompt.
    notice = f"{UNTRUSTED_CONTENT_NOTICE}\n" if isinstance(raw_data, dict) and raw_data.get("untrusted") else ""
    call_id = _CALL_ID_UNSAFE_RE.sub("", command.call_id)
    call_attribute = f' call_id="{call_id}"' if call_id else ""
    return (
        f'<UNTRUSTED_TOOL_OUTPUT source="{command.action.type}"{call_attribute}>\n'
        f"{notice}"
        f"{defang_prompt_boundary_tags(serialized)}\n"
        "</UNTRUSTED_TOOL_OUTPUT>"
    )


# A provider's call id is echoed into the block's opening tag; anything that
# could end the attribute is dropped.
_CALL_ID_UNSAFE_RE = re.compile(r"[^A-Za-z0-9_.:-]")


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
                    "command": _command_log(command),
                }
            )
            self._ui.debug_print(f"DevOps repeat blocked: {command_text}")
            return True

    if (
        seen_count >= self.config.repeat_action_limit
        and command.action.type not in self._state.required_next_actions
        and _earlier_answer_in_view(self, command, signature)
    ):
        self._state.pending_instruction = (
            "This exact action already ran and its result is still shown under "
            "\"Detailed recent action data\"; running it again returns the same answer. "
            "Work from that result, or take an action that answers a different question."
        )
        self._state.stagnation_count += 1
        self._state.step_logs.append(
            {
                "status": "repeat_blocked",
                "command": _command_log(command),
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
                        "command": _command_log(command),
                    }
                )
                self._ui.debug_print(f"Repeat write target blocked: {path}")
                return True
            self._state.seen_actions[target_signature] = target_seen + 1

    self._state.seen_actions[signature] = seen_count + 1
    self._state.last_action_signature = signature
    return False


def _earlier_answer_in_view(self, command: AgentCommand, signature: str) -> bool:
    """A repeat is useless only while its earlier answer is in front of the model.

    Once a read has left the prompt, running it again is the only way to see
    it: refusing that left the model reasoning about a file it could no longer
    read. Actions with side effects stay counted as repeats regardless.
    """
    if command.action.type not in _READ_ONLY_ACTIONS:
        return True
    return signature in self._state.recent_result_signatures


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
    # A response's words are recorded once, however many calls it makes.
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
        and self._state.plan_phase != PLAN_PLANNING
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
    if command.action.type in plan_mode.PLAN_ACTIONS:
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
    """Block write actions on a run the user, the role or plan mode made read-only."""
    if self._state.plan_phase == PLAN_PLANNING:
        return not _allowed_while_planning(command)
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


def _allowed_while_planning(command: AgentCommand) -> bool:
    action_type = command.action.type
    if action_type == "run_shell":
        return _is_safe_read_only_shell_command(command.action.args.get("command", []))
    return (
        action_type in _READ_ONLY_ACTIONS
        or action_type in _PLANNING_EXTRA_ACTIONS
    )


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


_is_create_goal = is_create_goal


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


def _is_report_goal(user_goal: str) -> bool:
    """A report task expects a substantive spoken answer, not just a mutation."""
    task_kind = infer_task_kind(user_goal)
    # A change that also asks for an explanation still owes the explanation.
    if task_kind == "change":
        return asks_for_explanation(user_goal)
    return task_kind == "report"


def _expects_file_changes(self) -> bool:
    return infer_task_kind(self._state.user_goal) == "change"


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
            "Ответь по сути на вопрос пользователя в финальном ответе: "
            "приведи конкретные находки и рекомендации со ссылками на прочитанные "
            "файлы. Шаблонные фразы вроде «все проверки пройдены» запрещены."
        )
    return (
        "Answer the user's question in your final answer: give concrete "
        "findings and recommendations that reference the files you read. Template "
        "phrases like 'all checks passed' are forbidden."
    )


def _build_create_guidance(language: str) -> str:
    return (
        "This is a CREATE task. Do not loop on inspect and do not end the turn without changes. "
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
        "The task looks complete. If nothing is left to verify, give your final "
        "answer: a response with no tool call."
    )
    if self._state.report_goal:
        hint += " " + _build_report_substance_instruction(self._state.task_language)
    return hint


def _extract_required_artifacts(user_goal: str) -> list[str]:
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

    for match in _ARTIFACT_RE.finditer(user_goal):
        # A path outside the workspace ("~/.config/editor", "/etc/hosts") is
        # context about the machine, not something this run can produce; read
        # as relative it became a deliverable no action could ever satisfy.
        if match.group(1).startswith("/") or user_goal[match.start() - 1 : match.start()] == "~":
            continue
        cleaned = match.group(1).strip().strip(".,:;()[]{}\"'")
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
        if not value or value in {".", ".."}:
            continue
        folded = value.casefold()
        if folded in seen:
            continue
        seen.add(folded)
        normalized.append(value)
    return normalized[:24]


_ARTIFACT_SEARCH_SKIP_DIRS = {".git", ".kvist", ".venv", "venv", "node_modules", "__pycache__"}


def _anchor_artifacts(workspace: Path, artifacts: list[str]) -> list[str]:
    """Point a partial file path at the file it already names deeper in the workspace.

    "fix reminders.py" mentions a file that lives in src/, and a task quoting
    dist/app.js means web/dist/app.js; requiring either where it was written
    made the completion gate demand a duplicate there.
    """
    partial = {a for a in artifacts if not a.endswith("/") and not (workspace / a).exists()}
    if not partial:
        return artifacts
    found: dict[str, str] = {}
    for root, dirs, files in os.walk(workspace):
        dirs[:] = sorted(d for d in dirs if d not in _ARTIFACT_SEARCH_SKIP_DIRS)
        for name in sorted(files):
            relative = (Path(root) / name).relative_to(workspace).as_posix()
            for artifact in partial - found.keys():
                if relative.endswith("/" + artifact):
                    found[artifact] = relative
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
    artifacts = self._state.required_artifacts
    for artifact, anchored in zip(artifacts, _anchored_required_artifacts(self, workspace)):
        if artifact.rstrip("/") in gone:
            continue
        if _named_in_removal_request(self._state.user_goal, artifact):
            continue
        is_dir = artifact.endswith("/")
        target = workspace / anchored.rstrip("/")
        if is_dir and not target.is_dir():
            missing.append(artifact)
        elif not is_dir and not target.exists():
            missing.append(artifact)
    return missing


def _anchored_required_artifacts(self, workspace: Path) -> list[str]:
    """_anchor_artifacts, walked again only after an action that can create files.

    A file named by the task can appear deeper in the tree only through a
    write, a shell command or a delegation; the walk re-ran on every refresh —
    twice per action, reads included — and cost seconds per action on a large
    repository. _execute_action drops the cache after a side effect.
    """
    artifacts = self._state.required_artifacts
    cached = getattr(self, "_anchored_artifacts", None)
    if cached is None or cached[0] != artifacts:
        cached = (list(artifacts), _anchor_artifacts(workspace, artifacts))
        self._anchored_artifacts = cached
    return cached[1]


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
            "git_status alone is NOT sufficient. "
            f"{_CHEAPEST_VERIFICATION_HINT}"
        )
        required_actions.update(_VERIFY_ONLY_ACTIONS)

    for kind, text in _evidence_completion_blockers(self):
        kinds.append(kind)
        blockers.append(text)
        required_actions.update(_WRITE_ACTIONS)

    return blockers, kinds, sorted(required_actions)


def _evidence_completion_blockers(self) -> list[tuple[str, str]]:
    """Blockers that look only at hard evidence (files touched, recorded errors, denials)."""
    st = self._state
    ru = st.task_language == "ru"
    out: list[tuple[str, str]] = []

    # A request whose result is a change to files is not second-guessed:
    # whatever the last call did, nothing changed means not done. The other
    # keyword guesses keep their exceptions for read-only and verify-only
    # runs, since they can mistake a question for an edit.
    change_by_intent = _expects_file_changes(self)
    change_task = (
        change_by_intent
        or st.create_goal
        or st.modify_goal
        or bool(st.required_artifacts)
    )
    verify_only_done = st.verify_only_goal and bool(st.verification_evidence)
    guessed_change_open = change_task and not st.read_only_goal and not verify_only_done
    if not st.files_changed and (change_by_intent or guessed_change_open):
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


def _blocked_progress(self) -> int:
    return min(self._state.progress, 99)


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


def _build_missing_artifacts_error(language: str, missing: list[str]) -> str:
    joined = ", ".join(missing[:8])
    return (
        f"Task cannot finish as done: the task names these workspace files and they do not exist yet: {joined}. "
        f"{_HONEST_EXIT_HINT}"
    )


# A weak model given only "verification is required" keeps editing or
# re-finishing; naming the cheapest accepted action ends that circling.
_CHEAPEST_VERIFICATION_HINT = (
    "The lightest action that counts: read_file on a file you changed "
    "(or git_diff). One such call is enough; then finish."
)


def _build_missing_verification_error(language: str) -> str:
    return (
        "Task cannot finish without verification of the changes: structure, syntax, "
        f"import, or run evidence is required. {_CHEAPEST_VERIFICATION_HINT}"
    )


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
        f"Ending the turn as done is not accepted yet. Blockers: {blocker_text} "
        f"Next, either take a corrective action using one of: {action_text}; "
        "or, if the work cannot or should not be done, end the turn again without a tool call "
        "and say plainly what was not done and why."
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
        "Do not use inspect/read/list_tree/find/search on the next step, and do not end the turn yet. "
        "For a CREATE task, switch directly to changing files inside the workspace. "
        "The one exception: if the work cannot or should not be done, end the turn without a tool call "
        "and say plainly what was not done and why."
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
