# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import sys
import time
import uuid
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
            "progress": command.progress,
            **preview,
        }
    if command.action.type in _NON_RECONCILABLE_SIDE_EFFECTS:
        return {**common, "action_kind": "side_effect", "action_type": command.action.type}
    return None

from agent_state import RunState
from executor import UNTRUSTED_CONTENT_NOTICE, ExecutorError
from parser import CommandValidationError, parse_agent_command
from protocol import AgentCommand
from redaction import redact_text, redact_value

_READ_ONLY_ACTIONS = {
    "read_file", "read_multiple_files", "list_dir",
    "list_tree", "find_files", "search_code",
}

_WRITE_ACTIONS = {"write_file", "append_file", "replace_in_file"}
_VERIFY_ONLY_ACTIONS = {"git_status", "git_diff", "run_shell", "list_tree", "find_files", "read_file"}

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

_ARTIFACT_RE = re.compile(r"(?<!\w)([A-Za-z0-9_./-]+(?:\.[A-Za-z0-9_-]+|/))(?!\w)")
_PLAN_INSPECT_RE = re.compile(r"inspect|read|review|analy|find|tree|search|провер|прочит|просмотр|найти|структур|поиск")
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


def _build_report_context(self, user_goal: str, summary: str, result: str, issues: str) -> str:
    """Build a structured JSON context block for the chat reporter."""
    state = self._state
    context = {
        "user_goal": user_goal,
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
        _load_role_prompt,
        call_model,
        safe_extract_reasoning_delta,
        safe_extract_stream_delta,
        _create_json_completion,
    )

    report_prompt = _load_role_prompt("report")
    stream_model = self.config.fast_model

    if not getattr(self.config, "stream", True):
        raw = call_model(self, model=stream_model, system_prompt=report_prompt, user_prompt=context)
        self._ui.print_user_markdown(raw)
        return raw

    messages = [
        {"role": "system", "content": report_prompt},
        {"role": "user", "content": context},
    ]
    stream = _create_json_completion(self, stream_model, messages, stream=True)
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
                reasoning_chunks.append(reasoning_text)
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

    return "".join(chunks)


def finalize_run(
    self,
    user_goal: str,
    task_completed: bool,
    primary_error: BaseException | None,
) -> None:
    """Attempt every lifecycle cleanup without replacing a primary failure."""
    cleanup_steps: list[tuple[str, Any]] = [
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


def run_loop(self, user_goal: str, start_step: int) -> str:
    self._ui.start_live()

    final_message = ""
    create_goal = _is_create_goal(user_goal)
    modify_goal = _is_modify_goal(user_goal)
    read_only_goal = _is_read_only_goal(user_goal)
    verify_only_goal = _is_verify_only_goal(user_goal)
    self._state.user_goal = user_goal
    self._state.create_goal = create_goal
    self._state.modify_goal = modify_goal
    self._state.read_only_goal = read_only_goal
    self._state.verify_only_goal = verify_only_goal
    if not self._state.required_artifacts and not read_only_goal:
        self._state.required_artifacts = _extract_required_artifacts(user_goal, self._state.plan_info)
    self._refresh_completion_requirements()
    report_rendered = False
    task_completed = False
    try:
        for step in range(start_step, self.config.max_steps + 1):
            early_stop_message = self._build_early_stop_message(step)
            if early_stop_message:
                final_message = early_stop_message
                break

            prompt = self._build_runtime_prompt(user_goal, step)
            self._current_step = step
            loop_pre = time.monotonic()
            try:
                raw_text = self._call_action_model(prompt)
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
                # Cooldown to avoid burning through all steps on transient API failures.
                # Skip if the model layer already exhausted its own retries (avoid double-sleep).
                retries_exhausted = getattr(self, "_retries_exhausted", False)
                if retries_exhausted:
                    continue
                cooldown = self.config.retry_base_delay * (2 ** min(self._state.consecutive_errors, 4))
                if any(m in error_text.casefold() for m in ("rate limit", "too many requests", "high demand", "503", "timeout")):
                    cooldown = 5.0
                time.sleep(cooldown)
                continue

            try:
                command = parse_agent_command(raw_text)
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
                continue

            language_error = self._command_language_error(command)
            if language_error:
                self._register_language_error(step, command, language_error)
                self._ui.update_live(
                    user_message="Fixing the model response language and retrying the step",
                    status="fixing response language",
                    step=step,
                    progress=self._state.progress,
                    plan_step="language correction",
                    is_error=True,
                )
                continue

            self._record_user_message(command.user_message)

            followup_error = self._required_followup_error(command)
            if followup_error:
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
                            "progress": command.progress,
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
                continue

            if self._is_repeat_blocked(command):
                if create_goal and not self._state.files_changed:
                    self._set_required_next_actions(_WRITE_ACTIONS, _build_create_guidance(self._state.task_language))
                self._ui.update_live(
                    user_message=command.user_message,
                    status="looking for another working step",
                    step=step,
                    progress=self._state.progress,
                    plan_step=command.plan_step,
                    is_error=True,
                )
                continue

            # Block report-like artifact creation at the UI/report layer only.
            # Never auto-complete here: the model may try to write task_report.txt
            # before doing any real work, and that must stay a corrective error.
            if self._is_report_artifact_blocked(command):
                self._refresh_completion_requirements(force_enforcement=True)
                if self._state.completion_ready:
                    feedback = {
                        "ok": False,
                        "action_type": command.action.type,
                        "result": "Task is already complete. Do not write report artifacts. Use final_answer or report_to_user instead.",
                    }
                    live_status = "completion enforced"
                    live_message = "Task is complete, requiring final_answer"
                else:
                    feedback = {
                        "ok": False,
                        "action_type": command.action.type,
                        "result": "Report artifacts are not part of the requested work. Do not create task_report.txt/summary.txt files. Continue the actual task and report through final_answer or report_to_user only at the end.",
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
                continue

            # Block write actions for read-only tasks
            if self._is_read_only_violation(command):
                feedback = {
                    "ok": False,
                    "action_type": command.action.type,
                    "result": "This is a read-only/verify task. File modifications are not allowed. Use read-only actions (read_file, find_files, list_dir, git_status) and then final_answer or report_to_user.",
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
                continue

            self._ui.print_step_header(
                step, command, getattr(self, "_last_reasoning_seconds", 0.0)
            )

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

            # Completion guard: if task is ready, only allow final_answer
            if self._state.completion_ready and command.action.type not in ("final_answer", "report_to_user"):
                # Provide corrective feedback and give model one more chance
                corrective_feedback = {
                    "ok": False,
                    "action_type": command.action.type,
                    "result": "Task is already verified and complete. You must use final_answer or report_to_user now. No further actions are permitted.",
                }
                self._register_feedback(step, command, corrective_feedback)
                self._ui.update_live(
                    user_message="Task is complete, requiring final_answer",
                    status="completion enforced",
                    step=step,
                    progress=self._state.progress,
                    plan_step=command.plan_step,
                    is_error=False,
                )
                # If model ignores this twice, force final_answer
                if self._state.seen_actions.get("completion_ready_violations", 0) >= 1:
                    final_message = self._build_auto_completion_message()
                    self._register_final_answer(step, final_message)
                    task_completed = True
                    break
                self._state.seen_actions["completion_ready_violations"] = self._state.seen_actions.get("completion_ready_violations", 0) + 1
                continue

            if command.action.type == "final_answer":
                self._refresh_completion_requirements(force_enforcement=True)
                if self._state.completion_blockers:
                    error_text = " ".join(self._state.completion_blockers)
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
                                "progress": command.progress,
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
                    continue
                final_message = str(command.action.args["message"])
                self._register_final_answer(step, final_message)
                task_completed = True
                break

            if command.action.type == "report_to_user":
                self._refresh_completion_requirements(force_enforcement=True)
                if self._state.completion_blockers:
                    error_text = " ".join(self._state.completion_blockers)
                    self._state.consecutive_errors += 1
                    self._record_error(error_text)
                    self._state.pending_instruction = (
                        f"Task is not complete. Blockers: {error_text}. "
                        "Fix the remaining issues before reporting."
                    )
                    self._ui.update_live(
                        user_message="Report blocked — task not complete",
                        status="report blocked",
                        step=step,
                        progress=self._state.progress,
                        plan_step=command.plan_step,
                        is_error=True,
                    )
                    continue
                # Collect context and hand off to chat reporter
                summary = str(command.action.args.get("summary", ""))
                tech_result = str(command.action.args.get("result", ""))
                issues = str(command.action.args.get("issues", ""))
                context = _build_report_context(self, user_goal, summary, tech_result, issues)
                report_text = _render_report(self, context)
                final_message = report_text or tech_result or summary
                self._register_final_answer(step, final_message)
                report_rendered = True
                task_completed = True
                break

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
            try:
                feedback = self._execute_command(command)
            finally:
                if pending_action is not None:
                    self.executor.end_action_transaction()
            self._register_feedback(step, command, feedback)

            # Store rolling summary on interval steps (every Nth step)
            if (
                step % self.config.rolling_summary_interval == 0
                and command.step_summary
            ):
                self._state.rolling_summaries[step] = command.step_summary

            # Immediate completion check: if task just became ready, finish now
            if (
                self._state.completion_ready
                and feedback.get("ok")
                and (self._state.create_goal or self._state.verify_only_goal)
            ):
                final_message = self._build_auto_completion_message()
                self._register_final_answer(step, final_message)
                task_completed = True
                break

            if (
                create_goal
                and not self._state.files_changed
                and feedback.get("ok")
                and command.action.type in _READ_ONLY_ACTIONS
                and self._state.read_only_streak >= 2
            ):
                self._set_required_next_actions(_WRITE_ACTIONS, _build_create_guidance(self._state.task_language))

            self._ui.update_live(
                user_message=command.user_message,
                status="continuing" if feedback.get("ok") else "error received",
                step=step,
                progress=self._state.progress,
                plan_step=command.plan_step,
                is_error=not bool(feedback.get("ok")),
            )

            # Checkpoint every step: mutating actions land on disk immediately,
            # so a coarser interval leaves resume state trailing the real
            # workspace. The atomic write is negligible next to the LLM call.
            self._save_checkpoint(user_goal, step)

        if not final_message:
            final_message = self._build_limit_reached_message()
    finally:
        self._finalize_run(
            user_goal,
            task_completed=task_completed,
            primary_error=sys.exception(),
        )

    if not self.config.verbose and not report_rendered:
        self._ui.print_final(final_message, self._state.user_messages)

    return final_message


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
    error = redact_text(str(raw_error).strip()) or "Unknown error"
    lowered = error.casefold()
    
    is_overloaded = any(m in lowered for m in ("all providers exhausted", "rate limit", "too many requests", "high demand", "503"))
    
    if is_overloaded:
        return "Provider rate limit or high demand reached. Try again later or switch the provider."
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

    elif action_type == "read_multiple_files":
        for file_info in data.get("files", []):
            path = str(file_info.get("path", "")).strip()
            self._push_unique(self._state.files_read, path)

    elif action_type in {"write_file", "append_file", "replace_in_file"}:
        path = str(data.get("path", "")).strip()
        self._mark_file_write_applied(path)
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
    plan_progress = self._plan_progress()
    target_progress = max(self._state.progress, plan_progress)

    if ok:
        target_progress = max(target_progress, min(command.progress, _progress_ceiling(self._state, command.action.type)))

    self._state.last_progress = self._state.progress
    self._state.progress = max(0, min(target_progress, 99))


def register_final_answer(self, step: int, message: str) -> None:
    for item in _report_plan_steps(self):
        self._state.completed_plan_steps.add(item)
    self._refresh_completion_requirements()
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
        return (
            f'<UNTRUSTED_TOOL_OUTPUT source="{command.action.type}">\n'
            f"{UNTRUSTED_CONTENT_NOTICE}\n"
            f"{serialized}\n"
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


def build_early_stop_message(self, step: int) -> str | None:
    is_ru = self._state.response_language == "ru"
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


def build_auto_completion_message(self) -> str:
    """Build automatic completion message when task is verified but model doesn't call final_answer."""
    files_summary = ", ".join(self._state.files_changed[:5])
    if len(self._state.files_changed) > 5:
        extra = len(self._state.files_changed) - 5
        files_summary += (
            f" и ещё {extra}" if self._state.response_language == "ru" else f" and {extra} more"
        )

    if self._state.response_language == "ru":
        if self._state.files_changed:
            return f"Готово. Изменённые файлы: {files_summary}. Проверка пройдена."
        return "Готово, все проверки пройдены."
    if self._state.files_changed:
        return f"Done. Modified files: {files_summary}. Verification confirmed."
    return "Done. All checks passed."


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
                        "progress": command.progress,
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
                            "progress": command.progress,
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

    if evidence:
        self._push_unique(self._state.verification_evidence, evidence, limit=12)


def refresh_completion_requirements(self, force_enforcement: bool = False) -> None:
    blockers, required_actions = _compute_completion_status(self)
    self._state.missing_artifacts = _missing_required_artifacts(self)
    self._state.completion_blockers = blockers

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
    """Block write actions for read-only/verify tasks."""
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


def _is_modify_goal(user_goal: str) -> bool:
    """Detect fix/modify/change/update/edit intent from user goal."""
    lowered = user_goal.casefold()
    markers = (
        "add ",
        "fix ",
        "fix:",
        "modify ",
        "change ",
        "update ",
        "edit ",
        "refactor ",
        "improve ",
        "enhance ",
        "исправь",
        "исправить",
        "измени",
        "изменить",
        "обнови",
        "обновить",
        "добавь",
        "добавить",
        "отредактируй",
        "отредактировать",
        "переделай",
        "доработай",
    )
    return any(marker in lowered for marker in markers)


def _is_read_only_goal(user_goal: str) -> bool:
    """Detect read-only/verify intent from user goal."""
    if _is_create_goal(user_goal):
        return False
    # A FIX/MODIFY task is never read-only, even if it mentions filenames
    # like empty_list.py whose substrings match short markers ("list").
    if _is_modify_goal(user_goal):
        return False

    lowered = user_goal.casefold()

    # Russian patterns
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

    # English patterns — short single-word markers use word-boundary
    # matching via _token_match to avoid false positives on filenames
    # (e.g. "list" must not trigger inside "empty_list.py").
    en_token_markers = {"find", "show", "inspect", "list"}
    en_phrase_patterns = (
        "check if",
        "check whether",
        "git status",
        "git diff",
    )

    if any(pattern in lowered for pattern in ru_patterns + en_phrase_patterns):
        return True
    return any(_token_match(marker, lowered) for marker in en_token_markers)


def _is_verify_only_goal(user_goal: str) -> bool:
    """Detect short verify/check tasks that should end after one factual confirmation."""
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
        "выведи",
        "git status",
        "git diff",
    )
    en_phrases = (
        "check if",
        "check whether",
        "git status",
        "git diff",
    )
    en_token_markers = {"find", "show", "list"}

    if any(pattern in lowered for pattern in ru_patterns + en_phrases):
        return True
    return any(_token_match(marker, lowered) for marker in en_token_markers)


def _build_create_guidance(language: str) -> str:
    return (
        "This is a CREATE task. Do not loop on inspect and do not use final_answer or report_to_user without changes. "
        "Choose a path inside the workspace and move to write_file or append_file, then verify with facts."
    )


def _build_premature_final_answer_error(language: str) -> str:
    return (
        "Completion blocked: this looks like a CREATE task, "
        "but no files were created or changed. "
        "Create the required files inside the workspace, then verify."
    )


def _build_missing_implementation_error(language: str) -> str:
    return (
        "Completion blocked: this looks like a FIX/MODIFY task, "
        "but no files were changed. Read the target file, make a minimal fix, then verify."
    )


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
    )
    return any(marker in lowered for marker in markers)
def _missing_required_artifacts(self) -> list[str]:
    missing: list[str] = []
    workspace = Path(self.executor.workspace)
    for artifact in self._state.required_artifacts:
        is_dir = artifact.endswith("/")
        target = workspace / artifact.rstrip("/")
        if is_dir and not target.is_dir():
            missing.append(artifact)
        elif not is_dir and not target.exists():
            missing.append(artifact)
    return missing


def _compute_completion_status(self) -> tuple[list[str], list[str]]:
    blockers: list[str] = []
    required_actions: set[str] = set()

    if self._state.destructive_diff:
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
    ]
    verify_only_complete = (
        self._state.verify_only_goal
        and not self._state.files_changed
        and bool(self._state.verification_evidence)
    )

    if self._state.create_goal and not self._state.files_changed:
        blockers.append(_build_premature_final_answer_error(self._state.task_language))
        required_actions.update(_WRITE_ACTIONS)

    if self._state.modify_goal and not self._state.files_changed:
        blockers.append(_build_missing_implementation_error(self._state.task_language))
        required_actions.update(_WRITE_ACTIONS)

    if (
        not verify_only_complete
        and not self._state.read_only_goal
        and not self._state.files_changed
        and _plan_requires_file_changes(self)
    ):
        blockers.append(_build_missing_implementation_error(self._state.task_language))
        required_actions.update(_WRITE_ACTIONS)

    if actionable_remaining_plan_steps and not verify_only_complete:
        blockers.append(_build_incomplete_plan_error(self._state.task_language, actionable_remaining_plan_steps))
        if all(_PLAN_VERIFY_RE.search(item.casefold()) for item in actionable_remaining_plan_steps):
            required_actions.update(_VERIFY_ONLY_ACTIONS)
        elif _plan_requires_file_changes(self):
            required_actions.update(_WRITE_ACTIONS)
        # else: info-gathering / devops task — don't force write actions,
        # let the agent continue with whatever actions are appropriate

    if missing and not (self._state.read_only_goal and not self._state.files_changed):
        blockers.append(_build_missing_artifacts_error(self._state.task_language, missing))
        required_actions.update(_WRITE_ACTIONS)

    if self._state.files_changed and not self._state.verification_evidence:
        blockers.append(_build_missing_verification_error(self._state.task_language))
        required_actions.update(_VERIFY_ONLY_ACTIONS)

    if self._state.pending_verification_paths:
        joined = ", ".join(self._state.pending_verification_paths[:5])
        blockers.append(
            f"Task cannot finish: verification required for recently changed files: {joined}. "
            "Use git_diff, read_file, search_code, or run_shell (pytest/py_compile) to verify. "
            "git_status alone is NOT sufficient."
        )
        required_actions.update(_VERIFY_ONLY_ACTIONS)

    return blockers, sorted(required_actions)


def _plan_requires_file_changes(self) -> bool:
    plan_info = self._state.plan_info
    if not plan_info or not plan_info.plan:
        return self._state.create_goal
    return any(_PLAN_EDIT_RE.search(item.casefold()) for item in plan_info.plan)


def _progress_ceiling(state: RunState, action_type: str) -> int:
    if action_type in _WRITE_ACTIONS:
        return 80
    if action_type in {"git_status", "git_diff", "run_shell"}:
        return 99 if state.files_changed else 35
    if action_type in {"list_tree", "find_files", "read_file"}:
        return 99 if state.files_changed else 25
    if action_type in {"read_multiple_files", "list_dir", "search_code", "web_search", "fetch_url"}:
        return 25
    return 40


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
    return f"Task cannot finish: the execution plan is not complete yet. Remaining: {preview}."


def _build_missing_verification_error(language: str) -> str:
    return "Task cannot finish without verification of the changes: structure, syntax, import, or run evidence is required."


def _build_completion_blocker_instruction(language: str, blockers: list[str], actions: list[str]) -> str:
    action_text = ", ".join(actions) or "none"
    blocker_text = " ".join(blockers)
    return (
        f"final_answer and report_to_user are forbidden. Blockers: {blocker_text} "
        f"The next step must be a corrective action using one of: {action_text}."
    )


def _build_required_action_error(language: str, actions: list[str], blockers: list[str]) -> str:
    action_text = ", ".join(actions)
    blocker_text = " ".join(blockers)
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
        "For a CREATE task, switch directly to changing files inside the workspace."
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
    return program in _SAFE_READ_ONLY_SHELLS
