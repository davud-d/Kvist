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
    if command.action.type == "skill_call" and not _skill_call_readonly(self, command):
        # A skill's effect (a click, a request, a started server) cannot be
        # proven after a crash either: resume reports it instead of replaying it.
        return {**common, "action_kind": "side_effect", "action_type": "skill_call",
                "skill": command.action.args.get("skill"), "tool": command.action.args.get("tool")}
    return None


def _skill_call_readonly(self, command: AgentCommand) -> bool:
    args = command.action.args
    return bool(self.skills.tool_readonly(str(args.get("skill")), str(args.get("tool"))))

from kernel.context_meter import note_pending
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
from kernel.role_router import has_change_verb, has_deferred_write_marker, has_no_write_marker
from kernel.task_intent import asks_for_explanation, infer_task_kind, is_create_goal
from kernel.background_jobs import UiJobListener
from kernel.agent_tools import AGENT_TOOL_ACTIONS, memory_prompt_section, run_agent_tool
from kernel.resources import CleanupReport, Owner, StopOutcome
from kernel.task_control import PAUSED
from kernel.agent_logging import mark_checkpoint_paused

_READ_ONLY_ACTIONS = {
    "read_file", "read_multiple_files", "list_dir",
    "list_tree", "find_files", "search_code",
}

_WRITE_ACTIONS = {"write_file", "append_file", "replace_in_file"}
# The agent's own tools never change the workspace, except a skill tool not
# declared read-only (checked per call).
_AGENT_STATE_ACTIONS = {"skills", "memory", "sessions", "helper", "ask_user"}
# Beyond the file reads, what plan mode still allows: it asks the person and
# looks things up, and never changes the workspace.
_PLANNING_EXTRA_ACTIONS = {
    "job_output", "job_stop", "git_status", "git_diff", "web_search", "fetch_url", "propose_plan", "offer_plan", "edit_plan", "cancel_plan", "refuse",
    *_AGENT_STATE_ACTIONS,
}

# Progress floor per phase the core derives from a response's calls. "done" is
# absent: 100 belongs to the terminal action, not to a phase label.
_PLAN_STEP_BANDS = {"inspect": 15, "plan": 30, "edit": 55, "verify": 85}

# Distinct actions after a hard environment denial before that denial stops
# counting as an unresolved error. It never blocks finishing: a denial must not
# push the model into working around it.
_RECOVERY_ATTEMPTS_REQUIRED = 2


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

    Consecutive failed steps and the step budget. Completion belongs to the model.
    """
    return {
        "blocker": "",
        "stall": self._state.stagnation_count,
        "stall_limit": self.config.stagnation_limit,
        "max_steps": self._state.step_budget,
    }


def _note_response_text(self, text: str) -> None:
    """Give the live panel the words of the latest response.

    They are the thinking column's fallback for providers that stream no
    reasoning. Optional channel, skipped by UIs without ``set_last_thought``.
    """
    notify = getattr(self._ui, "set_last_thought", None)
    if notify is not None:
        notify(text)


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
    if task_completed and (not self._state.finished_incomplete or self._state.task_cancelled):
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
    self.executor.jobs.listener = UiJobListener(self._ui, on_finished=self._background_finished)

    final_message = ""
    create_goal = _is_create_goal(user_goal)
    modify_goal = _is_modify_goal(user_goal)
    verify_only_goal = _is_verify_only_goal(user_goal)
    # Only an authoritative source may forbid writes: the reader role and an
    # explicit "do not modify" request. A keyword guess must never hard-block a
    # write — "переименуй ... и проверь, что всё работает" is a rename task, and
    # blocking its writes left it with nothing it was allowed to do. Clearing
    # create/modify avoids the runtime demanding a write it will then refuse.
    read_only_goal = (
        self._state.current_agent_role == "reader"
        or has_no_write_marker(user_goal)
        or self.force_read_only
    )
    for update in self._state.task_updates:
        if has_no_write_marker(update):
            read_only_goal = True
        elif (
            self._state.current_agent_role != "reader"
            and not self.force_read_only
            and (_is_modify_goal(update) or _is_create_goal(update))
        ):
            read_only_goal = False
    # "Don't change files until steps 1-4 are done" orders the work: no change
    # is owed while those stages are open, and writes stay possible after them.
    write_deferred = has_deferred_write_marker(user_goal)
    if read_only_goal or write_deferred:
        create_goal = False
        modify_goal = False
    self._state.user_goal = user_goal
    self._state.create_goal = create_goal
    self._state.modify_goal = modify_goal
    self._state.read_only_goal = read_only_goal
    self._state.verify_only_goal = verify_only_goal
    self._state.report_goal = _is_report_goal(user_goal)
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
            _collect_job_note(self)
            _collect_helper_note(self)
            _drain_inbox(self)
            try:
                if self._state.native_tools:
                    turn = _call_native_turn(self, user_goal, step)
                    response = ModelResponse(text=turn.content.strip(), calls=calls_from_turn(turn))
                else:
                    prompt = self._build_runtime_prompt(user_goal, step)
                    if self._state.job_note:
                        prompt = f"{prompt}\n\n{self._state.job_note}"
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

            self._state.job_note = ""
            self._state.job_note_sent = ""
            outcome, step = _run_turn(self, user_goal, step, response, turn)
            if outcome.finished:
                final_message = outcome.final_message
                report_rendered = outcome.report_rendered
                task_completed = True
                break
            self._save_checkpoint(user_goal, step - 1)
            # The checkpoint made the session document exist, which the title is stored into.
            self._request_session_title(user_goal)

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
        cleanup = _end_of_turn_resources(self, answered=task_completed)
        self._finalize_run(
            user_goal,
            task_completed=task_completed,
            primary_error=sys.exception(),
        )

    paused = self.control.state == PAUSED
    summary = cleanup.summary(self._state.response_language or "en") if cleanup is not None else ""
    if summary and not report_rendered:
        final_message = f"{final_message}\n\n_{summary}_"
    elif summary:
        self._ui.print_result(summary)
    if not self.config.verbose and not report_rendered:
        self._ui.print_final(
            final_message,
            self._state.user_messages,
            outcome="paused" if paused else _final_outcome(self, task_completed),
        )
    if not paused:
        self.control.finish()
    return final_message


def _collect_job_note(self) -> None:
    """Fold what the background jobs printed since the last request into the pending note."""
    text, job_ids = self.executor.jobs.take_note()
    if not text:
        return
    self._state.job_note = f"{self._state.job_note}\n{text}".strip()
    notice = getattr(self._ui, "print_job_notice", None)
    if notice is not None:
        notice(text, job_ids)


def _stop_background_jobs(self) -> list[Any]:
    """Jobs live as long as the task: nothing is running when the answer is given."""
    jobs = self.executor.jobs
    stopped = jobs.stop_all()
    jobs.listener = None
    return stopped


def _end_of_turn_resources(self, *, answered: bool = False) -> CleanupReport | None:
    """What the task owns, at the end of the turn.

    A pause keeps it all (a dev server, the virtual screen, a frozen skill,
    paused helpers) for the person's correction. A final answer keeps running
    helpers and background jobs when the transport wakes the agent once they
    finish (``wake_listener``): their result is then a turn of its own. Any
    other ending — "stop all", an error, a limit — and a final answer without
    a way to be woken stop helpers, skills, background jobs and every resource
    of this run, and report what could not be confirmed.
    """
    if self.control.state == PAUSED:
        self.skills.pause()
        return None
    reason = "user_cancelled" if self.control.cancel_requested() else "task_finished"
    report = CleanupReport(reason)
    keep_running = answered and reason == "task_finished" and self.wake_listener is not None
    if keep_running:
        self._background_role = self._state.current_agent_role
    else:
        report.outcomes.extend(_stop_left_running(self, reason))
    report.outcomes.extend(self.skills.finish_task(reason).outcomes)
    owner = Owner(run_id=str(getattr(self, "_run_id", "") or "run"))
    report.outcomes.extend(self.resources.cleanup(owner, reason).outcomes)
    return report


def _stop_left_running(self, reason: str) -> list[StopOutcome]:
    """Stop the helpers and background jobs of this conversation, whichever turn started them."""
    outcomes: list[StopOutcome] = []
    if self.helpers is not None:
        for agent_id in self.helpers.stop_all(reason):
            outcomes.append(StopOutcome(agent_id, f"helper {agent_id}", stopped=True, kind="helper"))
    for job in _stop_background_jobs(self):
        outcomes.append(StopOutcome(job.job_id, f"background job {job.job_id}", stopped=True, kind="job"))
    return outcomes


def _collect_helper_note(self) -> None:
    """Helper notices since the last request go into the next one, as facts — no model polls for them."""
    if self.helpers is None:
        return
    text = self.helpers.take_notices()
    if text:
        self._state.job_note = f"{self._state.job_note}\nHelpers:\n{text}".strip()


def _drain_inbox(self) -> None:
    """A helper's messages from the main agent become follow-ups of its task before its next request."""
    source = self.inbox_source
    if source is None:
        return
    for text in source():
        self._state.task_updates.append(text)
        self._state.pending_instruction = f"{self._state.pending_instruction}\n{text}".strip()


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
    """The loop reached a safe point after a pause or a stop; which one decides what survives."""
    if not self.control.interrupted():
        # Ctrl+C reaches the loop as a KeyboardInterrupt without a request.
        self.control.request_cancel("user_cancelled")
    state = self.control.reached_safe_point()
    self._state.stopped_by_user = True
    if state == PAUSED:
        self._state.paused_by_user = True
        mark_checkpoint_paused(self)
        return _build_pause_message(self)
    return self._build_cancellation_message()


def _build_pause_message(self) -> str:
    if self._state.response_language == "ru":
        return ("Работа на паузе: агент остановился в безопасной точке, состояние задачи сохранено. "
                "Напишите уточнение — агент учтёт его и продолжит, — или «продолжай».")
    return ("Paused: the agent stopped at a safe point and kept the task's state. "
            "Write a correction and it continues with it, or say \"continue\".")


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
    if self.is_helper:
        # A helper never manages helpers and never asks the person directly:
        # its questions go through the main agent.
        tools = [tool for tool in tools if tool["function"]["name"] not in ("helper", "ask_user")]
    tools.extend(self.skills.tool_definitions())
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
    if state.job_note and state.job_note != state.job_note_sent:
        state.native_messages.append({"role": "user", "content": state.job_note})
        state.job_note_sent = state.job_note
    _note_run_context(self, action="calling tools")
    return call_native_action_model(self, state.native_messages, state.native_tool_defs)


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
    """A response without calls ends the turn; its text is the final answer."""
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
    """The model owns completion; only authorization and plan mode constrain it."""
    if not _prepare_action(self, user_goal, step, command):
        return _BLOCKED
    self._state.no_change_declared = _declares_no_change(command)
    return _finish_with_final_answer(self, step, command)


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
_NOT_RUN_INTERRUPTED = (
    "Not executed: the person paused or stopped the task before this call. "
    "Check again whether it is still needed before making it."
)
_SKIPPED_AFTER_FAILED_WRITE = SKIPPED_ERROR
_SKIPPED_LABELS = ("пропущено: предыдущее действие упало", "skipped: an earlier action failed")


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
                request, text=response.text, batch_id=batch_id, plan_step=plan_step, language=language,
                resolve_skill_tool=self.skills.resolve,
            )
        except CommandValidationError as error:
            reason = redact_text(str(error))
            if not request.error:
                reason = f"Tool call {number} ({request.name}) was not run: {reason}"
                # The result only carries the reason; without the arguments a
                # repeated rejection cannot be traced to what the model sent.
                # Debug output only, so a long write_file body is cut.
                sent = redact_text(json.dumps(request.arguments, ensure_ascii=False, default=str))
                self._ui.debug_print(f"[rejected call] {request.name} arguments: {sent[:500]}")
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
        if self.control.interrupted() and not outcome.finished:
            # A pause or stop between calls: the rest of the response does not
            # run; after the person's correction the model decides again.
            answers.append(_NOT_RUN_INTERRUPTED)
            if item.command is not None:
                not_run.append(f"{item.command.action.description}: {_NOT_RUN_INTERRUPTED}")
            index += 1
            continue
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
    # A safety refusal takes priority over every authorization guard:
    # an illegal/malicious request must stop immediately, unconditionally,
    # not queue behind calls that assume the task is legitimate.
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
    if result.log["answer"] == "warning":
        # The tool has already shown the entire reply; no model call or batch tail may follow it.
        return _ActionOutcome(
            finished=True,
            final_message=end_session.warning_text(self._state.response_language),
            report_rendered=True,
        )
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
    """Plan transitions stay in the runtime, away from filesystem execution."""
    run = getattr(plan_mode, command.action.type)
    result = run(self, command)
    outcome = _finish_action(self, step, command, result.feedback)
    if result.instruction:
        self._state.pending_instruction = result.instruction
    if result.ended:
        self._state.finished_incomplete = True
        message = result.final_message or plan_mode.ended_message(self._state, str(command.action.args.get("plan", "")))
        return _ActionOutcome(finished=True, final_message=message)
    return outcome


def _prepare_action(self, user_goal: str, step: int, command: AgentCommand) -> bool:
    """Every guard that runs before an action, and its header on screen.

    False when a guard turned the action away; the reason is already in
    pending_instruction or in a registered error result.
    """
    self._current_step = step
    if _reject_before_execution(self, step, command):
        return False

    record_action_attempt(self, command)

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


def _reject_before_execution(self, step: int, command: AgentCommand) -> bool:
    return (
        _reject_read_only_violation(self, step, command)
        or _reject_plan_misuse(self, step, command)
    )


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


def _declares_no_change(command: AgentCommand) -> bool:
    return command.action.args.get("outcome") == "no_change_needed"


def _finish_with_final_answer(self, step: int, command: AgentCommand) -> _ActionOutcome:
    final_message = str(command.action.args["message"])
    if _is_incomplete_finish(command):
        _register_incomplete_finish(self, step, final_message)
    else:
        self._register_final_answer(step, final_message)
    return _ActionOutcome(finished=True, final_message=final_message)


def _finish_with_report(
    self, user_goal: str, step: int, command: AgentCommand
) -> _ActionOutcome:
    incomplete = _is_incomplete_finish(command)
    self._state.no_change_declared = _declares_no_change(command)
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
    if command.action.type == "run_shell":
        self.executor.set_job_context(command.action.description, command.call_id)
    try:
        feedback = self._execute_command(command)
    finally:
        self.executor.set_output_sink(None)
        if pending_action is not None:
            self.executor.end_action_transaction()
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
    """Record the actual result without prescribing the next kind of action."""
    self._current_step = step
    self._register_feedback(step, command, feedback)
    _emit_action_result(self, step, command, feedback)
    ok = bool(feedback.get("ok"))
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
    if command.action.type in AGENT_TOOL_ACTIONS:
        return redact_value(run_agent_tool(self, command))
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
    ok = bool(feedback.get("ok"))
    shell_result = feedback.get("data") or {}
    if command.action.type == "run_shell" and "returncode" in shell_result:
        self._state.last_shell_returncode = shell_result["returncode"]
    summary_line = self._feedback_to_line(command, feedback)
    detail_line = self._feedback_to_detail(command, feedback)
    note_pending(self, detail_line)
    signature = self._action_signature(command)

    if ok:
        self._state.consecutive_errors = 0
        self._state.pending_instruction = ""
        self._update_state_from_success(command, feedback)
        if command.action.type in _READ_ONLY_ACTIONS:
            self._state.read_only_streak += 1
        else:
            self._state.read_only_streak = 0
            self._state.inspection_note_streak = 0
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
            self._state.inspection_note_streak = 0
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
                f"systemctl reports that {service_name} service is not found. "
                "For an inspection request this may be the answer; for an installation "
                "or repair request, decide what remains to do from the user's goal."
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
    else:
        self._state.stagnation_count = 0

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
    that the changed path is recorded for reporting and later inspection.
    This tracking never obliges the model to perform a particular check.
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
            if "error" in file_info:
                continue
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
            items = data.get("files", [])
            missing = [str(item.get("path", "")) for item in items if "error" in item]
            note = f", not found: {', '.join(missing)}" if missing else ""
            return f"ok: read {len(items) - len(missing)} files{note}"
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
        return "это не сделаю."
    return "I won't do this."


def _summarize_recent_errors(self) -> str:
    items: list[str] = []
    for raw_error in self._state.error_history[-5:]:
        normalized = self._normalize_error_for_model(raw_error)
        if normalized not in items:
            items.append(normalized)
    if not items:
        return "none"
    return ", ".join(items[:3])


def record_action_attempt(self, command: AgentCommand) -> None:
    """Keep attempt counts for diagnostics, never as an execution quota.

    The same command can answer a new question after a file, service or external
    resource changes. The step budget bounds runs, including repeated successes.
    """
    signature = self._action_signature(command)
    self._state.seen_actions[signature] = self._state.seen_actions.get(signature, 0) + 1
    self._state.last_action_signature = signature


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


def update_verification_evidence(self, command: AgentCommand, data: dict[str, Any]) -> None:
    """Record observed checks for the report, without certifying completion.

    Command names do not determine whether a check is relevant. The model sees
    the output and decides what it demonstrates and whether more work is needed.
    """
    action_type = command.action.type
    evidence = None
    if action_type in {"git_status", "git_diff"} and not data.get("fallback_used"):
        evidence = action_type
    elif action_type == "run_shell" and data.get("returncode") == 0:
        command_text = " ".join(str(part) for part in data.get("command", []))
        evidence = f"run_shell: {command_text}"
    elif action_type in {"read_file", "read_multiple_files"}:
        files = data.get("files", []) if action_type == "read_multiple_files" else [data]
        for info in files:
            path = str(info.get("path", "")).strip()
            if not path or "error" in info:
                continue
            normalized = Path(path).as_posix()
            for pending in list(self._state.pending_verification_paths):
                if Path(pending).as_posix() == normalized:
                    self._state.pending_verification_paths.remove(pending)
            if self._state.files_changed or self._state.verify_only_goal:
                self._push_unique(self._state.verification_evidence, f"read_file: {path}", limit=12)
    if evidence:
        self._push_unique(self._state.verification_evidence, evidence, limit=12)


def refresh_completion_requirements(self, force_enforcement: bool = False) -> None:
    """Discard legacy completion gates, including those restored from checkpoints.

    These fields stay in the state document for older frontends/checkpoints;
    they no longer decide whether an action or a final answer is permitted.
    """
    self._state.required_artifacts = []
    self._state.missing_artifacts = []
    self._state.completion_blockers = []
    self._state.completion_blocker_kinds = []
    self._state.required_next_actions = []
    self._state.corrective_read_budget = 0
    self._state.completion_ready = False


def _is_read_only_violation(self, command: AgentCommand) -> bool:
    """Block write actions on a run the user, the role or plan mode made read-only."""
    if command.action.type == "skill_call":
        # A skill tool may run in a read-only phase only when its approved
        # manifest declares it read-only; the core then refuses any write it
        # attempts through ctx as well.
        read_only_phase = (
            self._state.plan_phase == PLAN_PLANNING
            or self._state.read_only_goal
            or self._state.current_agent_role == "reader"
        )
        return read_only_phase and not _skill_call_readonly(self, command)
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
    """Label inspection requests for observational summaries, never for gates."""
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


def _note_moved_files(self, argv: list[str]) -> None:
    """Record the destinations of successful moves in the change summary."""
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


def _blocked_progress(self) -> int:
    return min(self._state.progress, 99)


def seal_run_status(self) -> None:
    """Seal the declared outcome; an interrupted/budget-limited run is incomplete."""
    self._refresh_completion_requirements()
    if self._state.finished_incomplete or self._state.progress < 100:
        self._state.run_status = "blocked"
        self._state.progress = _blocked_progress(self)
    else:
        self._state.run_status = "completed"
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
    executor's/terminal's job (see terminal_validation.py `_validate_common_tokens`),
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
