# -*- coding: utf-8 -*-
from __future__ import annotations

import threading
import uuid
from typing import Any

from rich.console import Console
from rich.text import Text

from agent_context import (
    build_runtime_prompt,
    build_summary,
    compact_for_prompt,
    format_dependency_versions_for_prompt,
    format_web_facts_for_prompt,
    truncate_for_prompt,
)
from agent_logging import (
    clear_checkpoint,
    create_session,
    delete_session,
    discard_session_if_empty,
    find_resumable_session,
    generate_session_name,
    is_valid_session_name,
    list_sessions,
    load_active_session,
    load_checkpoint,
    load_session,
    peek_checkpoint,
    persist_logs,
    begin_run,
    read_session_document,
    adopt_session_style,
    restore_session_document,
    session_style,
    sanitize_for_log,
    save_active_session,
    save_checkpoint,
    save_session,
)
from agent_model import (
    call_action_model,
    call_model,
    call_model_streaming,
    _create_completion_with_reasoning_fallback,
    _create_json_completion,
    _emit_usage,
    count_tokens,
    init_tiktoken_encoding,
    _TOKEN_ENCODING_UNINITIALIZED,
    prefer_smart_model,
    safe_extract_completion_content,
    safe_extract_reasoning_delta,
    safe_extract_stream_delta,
    select_action_model_for_run,
    trim_prompt_to_token_limit,
    trim_chat_messages_to_token_limit,
)
from agent_planner import (
    create_plan,
    fallback_plan,
    is_complex_goal,
    no_plan_info,
    parse_json_dict,
)
from agent_runtime import (
    action_signature,
    build_cancellation_message,
    build_early_stop_message,
    build_error_feedback,
    build_limit_reached_message,
    build_safety_refusal_message,
    build_report_failure_message,
    command_language_error,
    execute_command,
    feedback_error_for_model,
    feedback_to_detail,
    feedback_to_line,
    finalize_run,
    is_repeat_blocked,
    normalize_error_for_model,
    plan_progress,
    refresh_completion_requirements,
    push_bounded,
    push_unique,
    record_error,
    record_user_message,
    register_blocked_action,
    register_feedback,
    register_final_answer,
    register_language_error,
    register_parse_error,
    register_safety_refusal,
    render_feedback,
    required_followup_error,
    reset_run_state,
    mark_file_write_applied,
    run_loop,
    seal_run_status,
    set_required_next_actions,
    update_completed_plan_steps,
    update_progress,
    update_recovery_tracking,
    update_state_from_success,
    update_verification_evidence,
    _effective_completed_plan_steps,
    _is_report_artifact_blocked,
    _is_read_only_violation,
)
from change_journal import JournalUnavailableError, classify_pending_write
from durable_json import JsonStorageCorruptError
from deps import detect_dependency_versions
from agent_state import AgentConfig, RunState
from agent_web import (
    append_known_version_to_query,
    contains_package_token,
    extract_fact_from_content,
    extract_version_token,
    is_web_topic,
    normalize_message,
    prepare_fetch_url_command,
    prepare_web_search_command,
    summarize_search_results,
)
from executor import SafeExecutor
from performance_logger import PerfLogger
from reasoning_effort import merge_reasoning_kwargs
from redaction import redact_text, redact_value
from role_router import route_role_with_llm, route_role
from system_context import (
    current_os_context,
    current_project_context,
    current_sandbox_context,
    current_shell_context,
    current_time_context,
)
from sandbox import sandbox_network_allowed
from ui import AgentUI
from language_utils import detect_request_language
from language_policy import resolve_response_language, response_language_name


class ChatRequestCancelled(Exception):
    """The user interrupted a chat request.

    Raised instead of letting ``KeyboardInterrupt`` escape the chat path, so
    the interrupt cancels one request rather than the whole REPL session.
    """


# How much read/searched content a run contributes to session history, so a
# later turn in a different role can cite it instead of re-reading. At 1200/600
# chars a chat follow-up was handed a fraction of the file the previous run had
# read and truthfully reported it only had part of it. Session history is
# persisted and re-injected, so the ceiling stays finite — but it is now large
# enough to carry a real source file, not an excerpt.
_SESSION_READ_CONTENT_CHARS = 40000
_SESSION_MULTI_READ_CONTENT_CHARS = 20000
_SESSION_MULTI_READ_MAX_FILES = 10
_SESSION_MAX_HARVESTED_OUTPUTS = 12
_SESSION_SHELL_OUTPUT_CHARS = 4000

# Preamble that hands harvested session output to a later chat turn. It states
# the content is available (so the role uses it) but must NOT forbid admitting
# absence: if the harvester missed something, the role has to say "no data"
# rather than invent it. Positive availability + explicit honesty, no gag.
_CHAT_HISTORY_PREAMBLE = (
    "(Before you respond: here is what happened earlier in this session. "
    "The 'Output:' blocks below are real command results and real file "
    "contents that were read for you — treat them as available to you, "
    "exactly as if you had produced them yourself, and answer the user's "
    "question directly from them when they contain the answer. If the answer "
    "is not present in the content below, say so honestly and ask to read it "
    "rather than inventing it.)"
)


class LocalAgent:
    def __init__(
        self,
        client: Any,
        executor: SafeExecutor,
        config: AgentConfig | None = None,
        *,
        ui: AgentUI,
        console: Console | None = None,
        perf_logger: PerfLogger | None = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.config = config or AgentConfig()
        self._cache: dict[str, str] = {}
        self._state = RunState()
        # Set from any thread (e.g. a transport reader) to stop the current
        # action-loop turn between steps or mid-generation. Lives on the agent,
        # not on RunState, so it survives a per-run state reset and is cleared
        # explicitly at the start of every turn. Transport-agnostic on purpose.
        self._cancel_event = threading.Event()
        self.console = console or Console()
        self._perf_logger = perf_logger or PerfLogger(executor.workspace)
        self._ui = ui
        # Loading tiktoken's rank table costs tens of MiB and is unnecessary
        # for session recovery and chat-only runs. count_tokens initializes it
        # on the first context-budget calculation.
        self._encoding = _TOKEN_ENCODING_UNINITIALIZED
        self._current_step = 0
        # Perceived-latency pipeline: single in-flight prefetch of the next
        # step's action call, and the last call's wall-clock (dwell tuning).
        self._prefetch: dict[str, Any] | None = None
        self._last_model_duration = 0.0
        # Last resolved user-facing language for this session; used as the
        # fallback when a new request has no detectable language.
        self._last_response_language: str | None = None
        self.current_session = load_active_session(self)
        self.executor.set_active_session(self.current_session)
        self._session_history: list[dict[str, Any]] = []
        # Set by load_checkpoint; describes a mutation interrupted mid-flight.
        self._pending_action: dict[str, Any] | None = None
        # Identifies the active run; a new run() stamps a fresh id.
        self._run_id: str | None = None
        # Lifecycle phase of a resumed checkpoint (set by load_checkpoint).
        self._resumed_phase: str = "executing"
        load_session(self)

    def _harvest_run_outputs(self) -> list[str]:
        """Collect the substantive content a later turn needs to answer about.

        Beyond shell stdout this captures the *contents* of files the run read
        (read_file / read_multiple_files) and code-search hits. Without it a
        follow-up handled by another role (e.g. chat after coder) receives only
        the request text and truthfully reports it has no file content — the
        role change silently drops the very data the user is asking about.
        """
        outputs: list[str] = []
        for log_entry in self._state.step_logs[-10:]:
            if log_entry.get("status") != "ok":
                continue
            action_type = log_entry.get("command", {}).get("action", {}).get("type", "")
            data = log_entry.get("feedback", {}).get("data", {})
            if action_type == "run_shell":
                stdout = data.get("stdout", "")
                if stdout:
                    outputs.append(stdout[:_SESSION_SHELL_OUTPUT_CHARS])
            elif action_type == "read_file":
                content = data.get("content", "")
                if content:
                    outputs.append(
                        f"[read {data.get('path', '')}]\n{content[:_SESSION_READ_CONTENT_CHARS]}"
                    )
            elif action_type == "read_multiple_files":
                files = data.get("files", [])
                for file_item in files[:_SESSION_MULTI_READ_MAX_FILES]:
                    content = file_item.get("content", "")
                    if content:
                        outputs.append(
                            f"[read {file_item.get('path', '')}]\n"
                            f"{content[:_SESSION_MULTI_READ_CONTENT_CHARS]}"
                        )
                # Never drop files silently — a hidden gap here is BUG-1 again.
                if len(files) > _SESSION_MULTI_READ_MAX_FILES:
                    hidden = files[_SESSION_MULTI_READ_MAX_FILES:]
                    hidden_paths = ", ".join(
                        str(item.get("path", "")) for item in hidden
                    )
                    outputs.append(
                        f"[{len(hidden)} more file(s) were read this step but not "
                        f"stored in history: {hidden_paths}]"
                    )
            elif action_type == "search_code":
                hits = data.get("results", [])
                if hits:
                    lines = [
                        f"{hit.get('path', '')}:{hit.get('line', '')}: {hit.get('text', '')}"
                        for hit in hits[:10]
                    ]
                    outputs.append(
                        f"[search '{data.get('query', '')}']\n" + "\n".join(lines)
                    )
        return outputs[-_SESSION_MAX_HARVESTED_OUTPUTS:]

    def _add_to_session_history(
        self,
        user_goal: str,
        role: str,
        result: str,
        stopped_by_user: bool = False,
    ) -> None:
        """Record a finished or user-stopped turn in session history for context."""
        outputs = self._harvest_run_outputs()

        entry = {
            "goal": user_goal,
            "role": role,
            "result": result[:200],
        }
        if outputs:
            entry["outputs"] = outputs
        if stopped_by_user:
            entry["stopped_by_user"] = True
        safe_entry = redact_value(entry)
        assert isinstance(safe_entry, dict)
        self._session_history.append(safe_entry)
        # Keep last 10 entries
        if len(self._session_history) > 10:
            self._session_history = self._session_history[-10:]
        self.save_session()

    def new_session(self, name: str | None = None) -> str:
        """Save the current session, then create and activate a fresh empty one.

        Without a name Kvist allocates a timestamped one. This is the ``/new``
        contract: a new session always *starts*, it never overwrites — the
        previous conversation stays on disk and is reachable through
        ``/session``. Other named sessions and the workspace change journal are
        never touched. Returns the new session's name.

        Durable-first: the new session is committed to disk before any
        in-memory state is discarded, so a failed write leaves both the on-disk
        and the in-memory session intact and usable.
        """
        if name is not None and not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if name is not None and name == self.current_session:
            raise ValueError(f"Session {name!r} already exists")
        self.save_session()
        previous = self.current_session
        resolved = name or self._generate_session_name()
        if not self._create_session(resolved):
            raise ValueError(f"Session {resolved!r} already exists")
        self._save_active_session(resolved)
        self.current_session = resolved
        self.executor.set_active_session(resolved)
        self._reset_run_state()
        self._restore_session_document({})
        self._pending_action = None
        self._discard_session_if_empty(previous)
        return resolved

    def start_fresh_session(self) -> tuple[str, str | None]:
        """Open a launch in its own session.

        Starting Kvist never continues the previous launch's conversation — a
        new request would otherwise be planned against stale context. Returns
        the new session's name plus the session still holding a resumable
        checkpoint (``None`` when there is none), so the caller can point the
        user at ``/resume``; ``resume`` itself walks there on its own.
        """
        try:
            resumable = self._find_resumable_session()
        except JsonStorageCorruptError:
            resumable = None
        return self.new_session(), resumable

    def _save_session_marker(self) -> None:
        """Keep compatibility with callers that create an empty session marker."""
        self.save_session()

    def switch_session(self, name: str) -> None:
        """Save the current session, then activate an EXISTING named session.

        Switching never creates a session: a missing target fails before any
        state is mutated, so a typo cannot silently fork history. Creation is
        the explicit job of ``new_session(name)``. Switching also never
        touches the workspace: conversation state and workspace rollback
        (change journal) are separate concepts. Leaving an auto-named session
        that was never used drops it, exactly as starting a new one does.
        """
        if not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if name == self.current_session:
            return
        # Read (and thereby validate) the target before mutating anything:
        # a corrupt or missing target must leave the active session intact.
        target_document = self._read_session_document(name)
        if not target_document:
            raise ValueError(f"Unknown session: {name!r}")
        self.save_session()
        previous = self.current_session
        self._save_active_session(name)
        self.current_session = name
        self.executor.set_active_session(name)
        self._reset_run_state()
        self._restore_session_document(target_document)
        self._discard_session_if_empty(previous)

    def _set_environment_facts(self) -> None:
        """Recompute the cheap, per-run environment facts shown in the prompt.

        Each fact is a single compact line (or empty) so the always-on
        "System environment" section stays small no matter how much is added
        here — one line per fact, never a dump.
        """
        self._state.system_os = current_os_context()
        self._state.system_shell = current_shell_context()
        self._state.system_project = current_project_context(self.executor.workspace) or ""
        self._state.system_git = self.executor.git_context_summary() or ""
        self._state.system_sandbox = current_sandbox_context(
            self.executor.sandbox_mode, sandbox_network_allowed()
        )

    def request_cancel(self) -> None:
        """Request that the current action-loop turn stop at the next safe point.

        Callable from any thread. It sets a flag only — it never touches an
        in-flight action or the transport. The loop observes it between steps
        (safe-stop) and while consuming a model stream (mid-generation).
        """
        self._cancel_event.set()

    def _reset_cancel(self) -> None:
        """Clear a pending cancellation so it cannot leak into the next turn."""
        self._cancel_event.clear()

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        # Cleared before routing/planning so a cancel requested during a
        # previous turn never aborts this one's setup.
        self._reset_cancel()
        self._reset_run_state()
        self._state.task_language = detect_request_language(user_goal)
        # User-facing response language is resolved by the runtime layer
        # before any model call; the JSON protocol itself stays English.
        self._state.response_language = resolve_response_language(
            user_goal, previous_language=self._last_response_language
        )
        self._last_response_language = self._state.response_language
        self._set_environment_facts()

        # ── Role routing: either passed from outside or detected locally ─────
        self._state.current_agent_role = routed_role or self._route_role(user_goal)
        # Shell risk assessment (command_policy) is role-aware; keep it in
        # sync with the routed role for this run.
        self.executor.set_active_role(self._state.current_agent_role)

        if self.config.verbose:
            self._select_action_model_for_run()
            # selected_action_role is model tier (fast/smart), not agent role
            self._ui.debug_print(
                f"Actual role: {self._state.current_agent_role} | "
                f"Selected tier: {self._state.selected_action_role} | "
                f"Selected model: {self._state.selected_action_model}"
            )

        # ── Chat mode: lightweight path, no JSON protocol ────────
        if self._state.current_agent_role == "chat":
            try:
                result = self._run_chat(user_goal)
            except ChatRequestCancelled:
                self._add_to_session_history(
                    user_goal,
                    "chat",
                    self._stopped_chat_reply(),
                    stopped_by_user=True,
                )
                return self._report_cancelled_turn()
            self._add_to_session_history(user_goal, "chat", result)
            return result

        # ── Standard path (coder / devops): single run_loop ──────
        # Run identity: durably activate this run BEFORE any planner/tool call.
        # begin_run atomically replaces any prior resumable checkpoint with a
        # NEW phase="planning" record, so a crash during planning leaves NEW
        # recoverable (re-plannable, no side effect yet) and OLD unreachable —
        # never a window where neither exists.
        self._run_id = uuid.uuid4().hex
        self._pending_action = None
        self._begin_run(user_goal, self._run_id)
        try:
            self._state.plan_info = (
                self._no_plan_info(user_goal)
                if self.config.skip_planning
                else self._create_plan(user_goal)
            )
            if not self._state.selected_action_model:
                self._select_action_model_for_run()
            self._state.dependency_versions = detect_dependency_versions(self.executor.workspace)
            self._ui.print_sandbox_notice(self.executor.sandbox_note)
            self._ui.print_plan(self._state.plan_info)
        except KeyboardInterrupt:
            return self._report_stop_before_loop(user_goal)
        result = self._run_loop(user_goal, start_step=1)
        self._record_run_turn(user_goal, result)
        return result

    def _record_run_turn(self, user_goal: str, result: str) -> None:
        self._add_to_session_history(
            user_goal,
            self._state.current_agent_role,
            result,
            stopped_by_user=self._state.stopped_by_user,
        )

    def _report_stop_before_loop(self, user_goal: str) -> str:
        """End a turn the user stopped while it was still being planned.

        Nothing ran yet, but the turn is still recorded: the next request may
        well be "carry on", and it has to know this one was cut short.
        """
        self._state.stopped_by_user = True
        message = self._build_cancellation_message()
        self._ui.print_final(message, self._state.user_messages)
        self._record_run_turn(user_goal, message)
        return message

    def resume(self) -> str | None:
        self._reset_cancel()
        self._reset_run_state()
        self._follow_checkpoint_to_its_session()
        result = self._load_checkpoint()
        if result is None:
            return None
        user_goal, last_step = result
        resumed_phase = getattr(self, "_resumed_phase", "executing")

        # Reconcile a mutation that was in-flight when the process died. The
        # checkpoint carries the intended write's pre/intended digests, so we
        # can prove whether it executed instead of blindly replaying it.
        start_step = last_step + 1
        pending = getattr(self, "_pending_action", None)
        if isinstance(pending, dict):
            reconciliation = self._reconcile_pending_action(pending)
            if reconciliation is None:
                return self._report_ambiguous_resume(pending)
            start_step = reconciliation

        # Environment facts are recomputed for the current machine rather than
        # restored: run() sets them, resume() must too or the prompt shows blanks.
        self._set_environment_facts()
        # Re-detect for the current workspace like run() does; the restored
        # versions may be stale after an interrupted run.
        self._state.dependency_versions = detect_dependency_versions(self.executor.workspace)
        self.executor.set_active_role(self._state.current_agent_role)
        if not self._state.task_language:
            self._state.task_language = detect_request_language(user_goal)
        if self._state.response_language not in ("ru", "en"):
            self._state.response_language = resolve_response_language(
                user_goal, previous_language=self._last_response_language
            )
        self._last_response_language = self._state.response_language
        if not self._state.selected_action_model:
            self._select_action_model_for_run()
        self._ui.debug_print(
            f"Restored from checkpoint: goal='{user_goal}', step={last_step}, progress={self._state.progress}%"
        )
        self._ui.debug_print(
            f"Execution model restored: tier={self._state.selected_action_role}, model={self._state.selected_action_model}"
        )
        # A run interrupted during planning has no durable plan and made no
        # external side effect: re-plan NEW and start fresh from step 1 rather
        # than reviving the superseded run or forgetting NEW.
        if resumed_phase == "planning" or self._state.plan_info is None:
            try:
                self._state.plan_info = (
                    self._no_plan_info(user_goal)
                    if self.config.skip_planning
                    else self._create_plan(user_goal)
                )
                self._ui.print_plan(self._state.plan_info)
            except KeyboardInterrupt:
                return self._report_stop_before_loop(user_goal)
            start_step = 1
        result = self._run_loop(user_goal, start_step=start_step)
        self._record_run_turn(user_goal, result)
        return result

    def _follow_checkpoint_to_its_session(self) -> None:
        """Activate the session holding the interrupted run, if it is elsewhere.

        A launch opens its own session, so the checkpoint the user means by
        ``/resume`` usually lives in the session they were in before. Resuming
        into it also restores that conversation, which is the context the
        interrupted run belongs to.
        """
        try:
            if self._peek_checkpoint() is not None:
                return
            holder = self._find_resumable_session()
        except JsonStorageCorruptError:
            return
        if holder is None or holder == self.current_session:
            return
        self.switch_session(holder)
        if self._last_response_language == "ru":
            message = f"Возобновляю прерванную задачу из сессии «{holder}»."
        else:
            message = f"Resuming the interrupted task from session '{holder}'."
        self.console.print(Text(message, style="dim"))

    def _reconcile_pending_action(self, pending: dict[str, Any]) -> int | None:
        """Decide where to resume given an interrupted action.

        Returns the step to resume at, or ``None`` when the outcome is ambiguous
        and Kvist must stop and ask instead of guessing. Distinguishes intent
        (the agent meant to produce content) from provenance (the agent is
        proven to have produced it): matching the intended content alone never
        grants ``/revert`` ownership.
        """
        pending_step = pending.get("step")
        if not isinstance(pending_step, int):
            return None
        # Action identity: a pending action stamped with a different run must
        # never be reconciled against this run.
        run_id = pending.get("run_id")
        if run_id is not None and self._run_id is not None and run_id != self._run_id:
            return None

        # Non-reconcilable side effects (run_shell, delegate_task): their effect
        # cannot be proven from workspace state after a crash, so they are never
        # replayed — the only safe answer is ambiguous.
        if pending.get("action_kind") == "side_effect":
            return None

        path = pending.get("path")
        intended_digest = pending.get("intended_digest")
        if not isinstance(path, str) or not isinstance(intended_digest, str):
            return None
        try:
            outcome = classify_pending_write(
                self.executor.workspace,
                path,
                pending.get("pre_digest"),
                intended_digest,
                run_id=pending.get("run_id"),
                action_id=pending.get("action_id"),
            )
        except JournalUnavailableError:
            # The recovery journal itself is unreadable: we cannot prove anything.
            return None
        if outcome == "ambiguous":
            return None
        if outcome in ("agent_committed", "desired_present"):
            # The desired file state is on disk, so the write must NOT be
            # replayed. Reconstruct the FULL logical post-action runtime state
            # via the same transition normal success uses, then resume past the
            # step. `agent_committed` already carries journal ownership (promoted
            # in-process); `desired_present` is left unowned — authorship is
            # unproven, so `/revert` will not destroy it.
            self._apply_recovered_file_write(pending)
            return pending_step + 1
        # not_executed: the workspace still holds the pre-mutation content, so
        # the step can safely be retried from scratch.
        return pending_step

    def _apply_recovered_file_write(self, pending: dict[str, Any]) -> None:
        """Replay the logical success transition of a recovered file write.

        Routes a synthetic successful command+feedback through the SAME
        ``_register_feedback`` path as normal execution, so plan progression,
        progress, verification re-arm, recent-result state, and step logs are
        reconstructed identically — without re-executing the write.
        """
        from protocol import Action, AgentCommand

        action_type = str(pending.get("action_type") or "write_file")
        path = str(pending.get("path") or "")
        step = int(pending.get("step") or self._current_step or 1)
        command = AgentCommand(
            thought="[recovered after interruption]",
            plan_step=str(pending.get("plan_step") or "edit"),
            user_message="[recovered after interruption]",
            action=Action(type=action_type, args={"path": path}),
            step_summary="",
        )
        feedback = {"ok": True, "action_type": action_type, "data": {"path": path}}
        self._register_feedback(step, command, feedback)

    def _report_ambiguous_resume(self, pending: dict[str, Any]) -> str:
        if pending.get("action_kind") == "side_effect":
            action = str(pending.get("action_type", "command"))
            if self._last_response_language == "ru":
                message = (
                    f"Возобновление остановлено: прерванное действие «{action}» "
                    f"могло уже выполниться, и повторять его небезопасно. Проверьте "
                    f"его эффект вручную; checkpoint сохранён. При необходимости "
                    f"начните заново через /new."
                )
            else:
                message = (
                    f"Resume stopped safely: the interrupted '{action}' may have "
                    f"already run, and replaying it is unsafe. Verify its effect "
                    f"manually; the checkpoint is kept. Start over with /new if "
                    f"needed."
                )
            self.console.print(Text(message, style="yellow"))
            return message

        path = str(pending.get("path", "?"))
        if self._last_response_language == "ru":
            message = (
                f"Возобновление остановлено: невозможно определить, выполнилось ли "
                f"прерванное изменение файла «{path}». Проверьте файл вручную; "
                f"checkpoint сохранён. Исправьте состояние и повторите /resume, "
                f"либо начните заново через /new."
            )
        else:
            message = (
                f"Resume stopped safely: cannot determine whether the interrupted "
                f"write to '{path}' completed. Inspect the file manually; the "
                f"checkpoint is kept. Fix the state and retry /resume, or start "
                f"over with /new."
            )
        self.console.print(Text(message, style="yellow"))
        return message

    _reset_run_state = reset_run_state
    _run_loop = run_loop
    _finalize_run = finalize_run

    _create_plan = create_plan
    _fallback_plan = fallback_plan
    _no_plan_info = no_plan_info
    _is_complex_goal = is_complex_goal
    _parse_json_dict = parse_json_dict

    _build_runtime_prompt = build_runtime_prompt
    _format_dependency_versions_for_prompt = format_dependency_versions_for_prompt
    _format_web_facts_for_prompt = format_web_facts_for_prompt
    _compact_for_prompt = compact_for_prompt
    _truncate_for_prompt = truncate_for_prompt
    _build_summary = build_summary

    _call_action_model = call_action_model
    _call_model = call_model
    _call_model_streaming = call_model_streaming
    _create_json_completion = _create_json_completion
    _init_tiktoken_encoding = staticmethod(init_tiktoken_encoding)
    _count_tokens = count_tokens
    _trim_prompt_to_token_limit = trim_prompt_to_token_limit
    _trim_chat_messages_to_token_limit = trim_chat_messages_to_token_limit
    _prefer_smart_model = prefer_smart_model
    _select_action_model_for_run = select_action_model_for_run

    _execute_command = execute_command
    _build_error_feedback = build_error_feedback
    _command_language_error = command_language_error
    _feedback_error_for_model = feedback_error_for_model
    _normalize_error_for_model = normalize_error_for_model
    _register_parse_error = register_parse_error
    _register_language_error = register_language_error
    _register_feedback = register_feedback
    _update_state_from_success = update_state_from_success
    _mark_file_write_applied = mark_file_write_applied
    _update_progress = update_progress
    _register_final_answer = register_final_answer
    _register_safety_refusal = register_safety_refusal
    _plan_progress = plan_progress
    _effective_completed_plan_steps = _effective_completed_plan_steps
    _seal_run_status = seal_run_status
    _update_recovery_tracking = update_recovery_tracking
    _refresh_completion_requirements = refresh_completion_requirements
    _record_error = record_error
    _feedback_to_line = feedback_to_line
    _feedback_to_detail = feedback_to_detail
    _render_feedback = render_feedback
    _build_early_stop_message = build_early_stop_message
    _build_cancellation_message = build_cancellation_message
    _build_limit_reached_message = build_limit_reached_message
    _build_safety_refusal_message = build_safety_refusal_message
    _build_report_failure_message = build_report_failure_message
    _is_repeat_blocked = is_repeat_blocked
    _action_signature = action_signature
    _record_user_message = record_user_message
    _required_followup_error = required_followup_error
    _register_blocked_action = register_blocked_action
    _set_required_next_actions = set_required_next_actions
    _update_completed_plan_steps = update_completed_plan_steps
    _push_unique = push_unique
    _push_bounded = push_bounded
    _update_verification_evidence = update_verification_evidence
    _is_report_artifact_blocked = _is_report_artifact_blocked
    _is_read_only_violation = _is_read_only_violation

    _prepare_web_search_command = prepare_web_search_command
    _prepare_fetch_url_command = prepare_fetch_url_command
    _is_web_topic = is_web_topic
    _append_known_version_to_query = append_known_version_to_query
    _contains_package_token = contains_package_token
    _extract_version_token = extract_version_token
    _extract_fact_from_content = extract_fact_from_content
    _summarize_search_results = summarize_search_results
    _normalize_message = normalize_message

    _sanitize_for_log = sanitize_for_log
    _persist_logs = persist_logs
    _save_checkpoint = save_checkpoint
    _load_checkpoint = load_checkpoint
    _clear_checkpoint = clear_checkpoint
    _create_session = create_session
    _generate_session_name = generate_session_name
    _find_resumable_session = find_resumable_session
    _discard_session_if_empty = discard_session_if_empty
    _begin_run = begin_run
    _peek_checkpoint = peek_checkpoint
    save_session = save_session
    _load_session = load_session
    _read_session_document = read_session_document
    _restore_session_document = restore_session_document
    adopt_session_style = adopt_session_style
    session_style = session_style
    _save_active_session = save_active_session
    _list_sessions = list_sessions
    _delete_session = delete_session

    # ── Role routing ─────────────────────────────────────────────

    def _route_role(self, user_goal: str) -> str:
        """Determine agent role via LLM router (fast_model)."""
        try:
            return route_role_with_llm(
                user_goal=user_goal,
                client=self.client,
                model=self.config.fast_model,
            )
        except Exception:
            # Fallback to keyword-based routing
            return route_role(user_goal)

    # ── Chat mode ────────────────────────────────────────────────

    def _run_chat(self, user_goal: str) -> str:
        """Lightweight chat mode: direct markdown streaming, no JSON protocol."""
        from agent_model import build_role_system_prompt, safe_extract_completion_content
        from context_utils import trim_history
        from llm_clients import fallback_candidates_for_model

        system_prompt = build_role_system_prompt(self, "chat")

        # Initialize chat history if it doesn't exist
        if not hasattr(self, "_chat_history"):
            self._chat_history = [{"role": "system", "content": system_prompt}]
        elif self._chat_history and self._chat_history[0]["role"] == "system":
            # Refreshed every turn so an edit to KVIST-CHAT.md lands on the
            # next message instead of the next session.
            self._chat_history[0] = {"role": "system", "content": system_prompt}

        # Add new call messages (from user, system, and function calls)
        # Inject session history context
        session_context = ""
        if hasattr(self, "_session_history") and self._session_history:
            history_lines = [_CHAT_HISTORY_PREAMBLE]
            for entry in self._session_history[-5:]:
                history_lines.append(
                    f"- [{entry['role']}] Asked: \"{entry['goal']}\""
                )
                if entry.get("stopped_by_user"):
                    history_lines.append(
                        "    Status: the user stopped this turn before it finished."
                    )
                for output in entry.get("outputs", []):
                    history_lines.append(f"    Output: {output}")
            session_context = "\n".join(history_lines) + "\n\n---\n\n"

        # Per-turn runtime directive: the reply language is resolved by the
        # runtime layer (language_policy), not guessed by the model.
        language_note = (
            f"(Runtime directive: response_language = {self._state.response_language}. "
            f"Reply in {response_language_name(self._state.response_language)}.)\n\n"
        )

        # Tell the model which provider/model is actually serving this turn, so
        # it can answer honestly when asked what it runs on.
        model_note = (
            f"(Runtime fact: you are being served by provider = "
            f"{self.config.provider}, model = {self.config.fast_model}. "
            f"If the user asks which model you are, answer with this.)\n\n"
        )

        # Snapshot before the turn mutates history: a cancelled or failed turn
        # must not leave a user message that no answer ever replies to.
        history_before = list(self._chat_history)
        # Persisted/displayed history keeps the clean user text — the runtime
        # directives are per-turn scaffolding for the model, not part of what
        # the user said, and must not show up as their message in the GUI or
        # get baked permanently into every future turn's context.
        self._chat_history.append({"role": "user", "content": user_goal})

        # Coarse turn cap so a session document cannot grow without bound; the
        # real limit is the token budget applied right after, which is what
        # decides how much of a long conversation actually survives.
        self._chat_history = trim_history(self._chat_history, max_recent=40)
        self._chat_history = self._trim_chat_messages_to_token_limit(self._chat_history)

        # Wire history sent to the model decorates only the current turn with
        # the runtime directives; earlier turns are sent as the clean text
        # that was actually stored. Re-trim afterwards so the decoration
        # itself can't push the request back over the hard input limit.
        wire_history = self._chat_history[:-1] + [
            {
                "role": "user",
                "content": language_note + model_note + session_context + user_goal,
            }
        ]
        wire_history = self._trim_chat_messages_to_token_limit(wire_history)

        content = ""
        last_error: str | None = None

        # Build model list: fast model first, then fallback candidates
        models_to_try: list[str] = [self.config.fast_model]
        try:
            fallbacks = fallback_candidates_for_model(
                self.config.provider,
                self.config.fast_model,
                allow_runtime_fallbacks=True,
            )
            for fb in fallbacks:
                if fb not in models_to_try:
                    models_to_try.append(fb)
        except Exception:
            pass

        self._chat_answer_chunks: list[str] = []
        for attempt, model in enumerate(models_to_try):
            try:
                if self.config.stream:
                    content = self._stream_chat_response(wire_history, model=model)
                else:
                    safe_history = redact_value(wire_history)
                    assert isinstance(safe_history, list)
                    chat_kwargs: dict[str, Any] = {
                        "model": model,
                        "messages": safe_history,
                    }
                    merge_reasoning_kwargs(
                        chat_kwargs,
                        self.config.provider,
                        self.config.reasoning_effort,
                    )
                    response = _create_completion_with_reasoning_fallback(
                        self, chat_kwargs
                    )
                    content = safe_extract_completion_content(
                        response, self.config.provider, model
                    )
                    self._ui.print_user_markdown(content)

                # Save model response to history and return
                if content:
                    self._chat_history.append({"role": "assistant", "content": content})
                    return content

                # Empty content (but no exception) — try next model
                last_error = "Model returned empty content"
                continue

            except (ChatRequestCancelled, KeyboardInterrupt) as error:
                # Ordered before ``Exception`` so a stopped turn is never
                # retried against a fallback model. The turn stays in history,
                # answered by what arrived plus a note that the user cut it
                # short, so the next message is read with that in mind.
                self._chat_history = history_before + [
                    {"role": "user", "content": user_goal},
                    {"role": "assistant", "content": self._stopped_chat_reply()},
                ]
                raise ChatRequestCancelled from error

            except Exception as error:
                last_error = redact_text(
                    str(error).strip() or type(error).__name__
                )
                if attempt < len(models_to_try) - 1:
                    self._ui.debug_print(
                        f"Chat attempt {attempt + 1} failed ({last_error}), "
                        "trying fallback..."
                    )
                continue

        # All models exhausted — show friendly error, don't crash REPL
        self._chat_history = history_before
        error_detail = f": {last_error}" if last_error else ""
        fallback_msg = (
            f"The model did not return a response{error_detail}. "
            "Try again, or use /provider to switch to a different provider."
        )
        self.console.print(Text(fallback_msg, style="yellow"))
        return fallback_msg

    def _stopped_chat_reply(self) -> str:
        note = (
            "[Ответ остановлен пользователем до завершения.]"
            if self._state.response_language == "ru"
            else "[The user stopped this reply before it finished.]"
        )
        partial = "".join(getattr(self, "_chat_answer_chunks", [])).rstrip()
        return f"{partial}\n\n{note}" if partial else note

    def _report_cancelled_turn(self) -> str:
        message = (
            "Ответ остановлен вами."
            if self._state.response_language == "ru"
            else "Reply stopped by you."
        )
        self.console.print(Text(message, style="yellow"))
        return message

    def _stream_chat_response(self, messages: list, model: str | None = None) -> str:
        """Stream chat response as markdown directly to console."""
        stream_model = model or self.config.fast_model
        safe_messages = redact_value(messages)
        assert isinstance(safe_messages, list)
        stream_kwargs: dict[str, Any] = {
            "model": stream_model,
            "messages": safe_messages,
            "stream": True,
        }
        merge_reasoning_kwargs(
            stream_kwargs, self.config.provider, self.config.reasoning_effort
        )
        try:
            stream = _create_completion_with_reasoning_fallback(self, stream_kwargs)
        except KeyboardInterrupt as error:
            raise ChatRequestCancelled from error
        try:
            content = self._consume_chat_stream(stream)
            # Real tiktoken usage of the completed chat call (thinking tokens
            # are billed output too, so they are counted alongside the answer).
            input_tokens = sum(
                self._count_tokens(str(message.get("content", "")))
                for message in safe_messages
                if isinstance(message, dict)
            )
            _emit_usage(
                self,
                model=stream_model,
                input_tokens=input_tokens,
                output_tokens=self._count_tokens(content)
                + self._count_tokens("".join(getattr(self, "_last_chat_reasoning", []) or [])),
                kind="chat",
            )
            return content
        except KeyboardInterrupt as error:
            raise ChatRequestCancelled from error
        finally:
            # Applies to cancellation, provider failure, and success alike:
            # an abandoned HTTP stream would otherwise hold its connection.
            self._close_chat_stream(stream)

    def _close_chat_stream(self, stream: Any) -> None:
        close = getattr(stream, "close", None)
        if close is None:
            return
        try:
            close()
        except Exception as error:
            # Already unwinding; a close failure must not replace the reason
            # we are here, but it is reported rather than swallowed.
            self._ui.debug_print(
                f"Chat stream close failed: {redact_text(str(error))}"
            )

    def _consume_chat_stream(self, stream: Any) -> str:
        chunks: list[str] = []
        # Shared, not copied: if the user stops the reply mid-stream, the turn
        # is recorded with whatever had arrived by then.
        self._chat_answer_chunks = chunks
        reasoning_chunks: list[str] = []

        import time
        last_update = time.monotonic()
        last_reasoning_update = last_update

        with self._ui.live_markdown() as update_markdown:
            reasoning_update = getattr(update_markdown, "reasoning", None)
            for chunk in stream:
                # The same flag the action loop watches (see agent_model's
                # stream loop). Without it a cancel requested from another
                # thread — the GUI's only way to stop a turn — was ignored
                # here and a chat answer kept streaming to its end.
                if self._cancel_event.is_set():
                    raise ChatRequestCancelled
                text = ""

                # 1. OpenAI variant (strict objects with attributes)
                delta = safe_extract_stream_delta(chunk)
                if delta is not None:
                    text = getattr(delta, "content", "") if not isinstance(delta, dict) else delta.get("content", "")

                # 2. freeflow-llm variant (raw dicts)
                elif isinstance(chunk, dict):
                    if "choices" in chunk and chunk["choices"]:
                        text = chunk["choices"][0].get("delta", {}).get("content", "")
                    else:
                        text = chunk.get("content", "")

                # 3. freeflow-llm variant (simple object with content field)
                elif hasattr(chunk, "content"):
                    text = chunk.content

                # 4. Fallback for raw string
                elif isinstance(chunk, str):
                    text = chunk

                reasoning_text = safe_extract_reasoning_delta(delta)
                if reasoning_text and reasoning_update is not None and not chunks:
                    reasoning_chunks.append(reasoning_text)
                    now = time.monotonic()
                    if now - last_reasoning_update > 0.1:
                        reasoning_update("".join(reasoning_chunks))
                        last_reasoning_update = now

                if text:
                    # The first answer token seals thinking: flush whatever
                    # reasoning was buffered so a fast stream never drops it.
                    if not chunks and reasoning_update is not None and reasoning_chunks:
                        reasoning_update("".join(reasoning_chunks))
                    chunks.append(text)

                    # Throttle rendering to ~10 FPS to prevent CPU choke
                    now = time.monotonic()
                    if now - last_update > 0.1:
                        update_markdown("".join(chunks))
                        last_update = now

            if reasoning_update is not None and reasoning_chunks and not chunks:
                reasoning_update("".join(reasoning_chunks))
            # Final update to guarantee the last parts are rendered
            update_markdown("".join(chunks))

        # Kept for the caller's usage accounting: thinking tokens are billed.
        self._last_chat_reasoning = reasoning_chunks
        return "".join(chunks)
