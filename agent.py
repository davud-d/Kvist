# -*- coding: utf-8 -*-
from __future__ import annotations

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
    is_valid_session_name,
    list_sessions,
    load_active_session,
    load_checkpoint,
    load_session,
    persist_logs,
    read_session_document,
    restore_session_document,
    sanitize_for_log,
    save_active_session,
    save_checkpoint,
    save_session,
)
from agent_model import (
    call_action_model,
    call_model,
    call_model_streaming,
    _create_json_completion,
    count_tokens,
    init_tiktoken_encoding,
    _TOKEN_ENCODING_UNINITIALIZED,
    prefer_smart_model,
    safe_extract_completion_content,
    safe_extract_stream_delta,
    select_action_model_for_run,
    trim_prompt_to_token_limit,
)
from agent_planner import (
    create_plan,
    fallback_plan,
    is_complex_goal,
    parse_json_dict,
)
from agent_runtime import (
    action_signature,
    build_early_stop_message,
    build_error_feedback,
    build_limit_reached_message,
    build_auto_completion_message,
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
    render_feedback,
    required_followup_error,
    reset_run_state,
    run_loop,
    set_required_next_actions,
    update_completed_plan_steps,
    update_progress,
    update_state_from_success,
    update_verification_evidence,
    _is_report_artifact_blocked,
    _is_read_only_violation,
)
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
from redaction import redact_text, redact_value
from role_router import route_role_with_llm, route_role
from system_context import current_os_context, current_shell_context, current_time_context
from ui import AgentUI
from language_utils import detect_request_language
from language_policy import resolve_response_language, response_language_name


class ChatRequestCancelled(Exception):
    """The user interrupted a chat request.

    Raised instead of letting ``KeyboardInterrupt`` escape the chat path, so
    the interrupt cancels one request rather than the whole REPL session.
    """


class LocalAgent:
    def __init__(
        self,
        client: Any,
        executor: SafeExecutor,
        config: AgentConfig | None = None,
    ) -> None:
        self.client = client
        self.executor = executor
        self.config = config or AgentConfig()
        self._cache: dict[str, str] = {}
        self._state = RunState()
        self.console = Console()
        self._perf_logger = PerfLogger(executor.workspace)
        self._ui = AgentUI(console=self.console, verbose=self.config.verbose, perf_logger=self._perf_logger)
        # Loading tiktoken's rank table costs tens of MiB and is unnecessary
        # for session recovery and chat-only runs. count_tokens initializes it
        # on the first context-budget calculation.
        self._encoding = _TOKEN_ENCODING_UNINITIALIZED
        self._current_step = 0
        # Last resolved user-facing language for this session; used as the
        # fallback when a new request has no detectable language.
        self._last_response_language: str | None = None
        self.current_session = load_active_session(self)
        self._session_history: list[dict[str, Any]] = []
        load_session(self)

    def _add_to_session_history(self, user_goal: str, role: str, result: str) -> None:
        """Record a completed run in session history for context."""
        # Collect key technical outputs from this run
        outputs: list[str] = []
        for log_entry in self._state.step_logs[-10:]:
            if log_entry.get("status") == "ok":
                action_type = log_entry.get("command", {}).get("action", {}).get("type", "")
                if action_type == "run_shell":
                    stdout = log_entry.get("feedback", {}).get("data", {}).get("stdout", "")
                    if stdout:
                        outputs.append(stdout[:300])
                elif action_type == "final_answer":
                    outputs.append(log_entry.get("message", "")[:200])

        entry = {
            "goal": user_goal,
            "role": role,
            "result": result[:200],
        }
        if outputs:
            entry["outputs"] = outputs
        safe_entry = redact_value(entry)
        assert isinstance(safe_entry, dict)
        self._session_history.append(safe_entry)
        # Keep last 10 entries
        if len(self._session_history) > 10:
            self._session_history = self._session_history[-10:]
        self.save_session()

    def new_session(self, name: str | None = None) -> None:
        """Reset to a fresh session — saves current, clears state, chat history, and checkpoint."""
        if name is not None and not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if name is not None:
            if name == self.current_session:
                raise ValueError(f"Session {name!r} already exists")
            self.save_session()
            if not self._create_session(name):
                raise ValueError(f"Session {name!r} already exists")
            self._save_active_session(name)
            self.current_session = name
            self._reset_run_state()
            self._restore_session_document({})
            return

        self._reset_run_state()
        self._session_history = []
        self._last_response_language = None
        if hasattr(self, "_chat_history"):
            del self._chat_history
        self._clear_checkpoint()
        self.save_session()

    def _save_session_marker(self) -> None:
        """Keep compatibility with callers that create an empty session marker."""
        self.save_session()

    def switch_session(self, name: str) -> None:
        """Save current session and switch to another named session."""
        if not is_valid_session_name(name):
            raise ValueError(f"Invalid session name: {name!r}")
        if name == self.current_session:
            return
        target_document = self._read_session_document(name)
        self.save_session()
        if not target_document and self._create_session(name):
            target_document = {}
        elif not target_document:
            target_document = self._read_session_document(name)
        self._save_active_session(name)
        self.current_session = name
        self._reset_run_state()
        self._restore_session_document(target_document)

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        self._reset_run_state()
        self._state.task_language = detect_request_language(user_goal)
        # User-facing response language is resolved by the runtime layer
        # before any model call; the JSON protocol itself stays English.
        self._state.response_language = resolve_response_language(
            user_goal, previous_language=self._last_response_language
        )
        self._last_response_language = self._state.response_language
        self._state.system_os = current_os_context()
        self._state.system_shell = current_shell_context()

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
                # A cancelled turn produced no answer, so it is not history.
                return self._report_cancelled_turn()
            self._add_to_session_history(user_goal, "chat", result)
            return result

        # ── Standard path (coder / devops): single run_loop ──────
        self._state.plan_info = self._create_plan(user_goal)
        if not self._state.selected_action_model:
            self._select_action_model_for_run()
        self._state.dependency_versions = detect_dependency_versions(self.executor.workspace)
        self._ui.print_sandbox_notice(self.executor.sandbox_note)
        self._ui.print_plan(self._state.plan_info)
        result = self._run_loop(user_goal, start_step=1)
        self._add_to_session_history(user_goal, self._state.current_agent_role, result)
        return result

    def resume(self) -> str | None:
        self._reset_run_state()
        result = self._load_checkpoint()
        if result is None:
            return None
        user_goal, last_step = result
        # Environment facts are recomputed for the current machine rather than
        # restored: run() sets them, resume() must too or the prompt shows blanks.
        self._state.system_os = current_os_context()
        self._state.system_shell = current_shell_context()
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
        result = self._run_loop(user_goal, start_step=last_step + 1)
        self._add_to_session_history(user_goal, self._state.current_agent_role, result)
        return result

    _reset_run_state = reset_run_state
    _run_loop = run_loop
    _finalize_run = finalize_run

    _create_plan = create_plan
    _fallback_plan = fallback_plan
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
    _update_progress = update_progress
    _register_final_answer = register_final_answer
    _plan_progress = plan_progress
    _refresh_completion_requirements = refresh_completion_requirements
    _record_error = record_error
    _feedback_to_line = feedback_to_line
    _feedback_to_detail = feedback_to_detail
    _render_feedback = render_feedback
    _build_early_stop_message = build_early_stop_message
    _build_limit_reached_message = build_limit_reached_message
    _build_auto_completion_message = build_auto_completion_message
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
    save_session = save_session
    _load_session = load_session
    _read_session_document = read_session_document
    _restore_session_document = restore_session_document
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
        from agent_model import _load_role_prompt, safe_extract_completion_content
        from context_utils import trim_history
        from llm_clients import fallback_candidates_for_model

        system_prompt = _load_role_prompt("chat")

        # Initialize chat history if it doesn't exist
        if not hasattr(self, "_chat_history"):
            self._chat_history = [{"role": "system", "content": system_prompt}]

        # Add new call messages (from user, system, and function calls)
        # Inject session history context
        session_context = ""
        if hasattr(self, "_session_history") and self._session_history:
            history_lines = [
                "(Before you respond: here is what happened earlier in this session. "
                "Use this context to understand what the user is referring to. "
                "The 'outputs' field contains actual command results, NOT descriptions.)",
            ]
            for entry in self._session_history[-5:]:
                history_lines.append(
                    f"- [{entry['role']}] Asked: \"{entry['goal']}\""
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
        self._chat_history.append(
            {"role": "user", "content": language_note + model_note + session_context + user_goal}
        )

        # Trim history to keep only last 10 messages + system prompt
        self._chat_history = trim_history(self._chat_history, max_recent=10)

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

        for attempt, model in enumerate(models_to_try):
            try:
                if self.config.stream:
                    content = self._stream_chat_response(self._chat_history, model=model)
                else:
                    safe_history = redact_value(self._chat_history)
                    assert isinstance(safe_history, list)
                    response = self.client.chat.completions.create(
                        model=model,
                        messages=safe_history,
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
                # Ordered before ``Exception`` so a cancelled turn is never
                # retried against a fallback model.
                self._chat_history = history_before
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

    def _report_cancelled_turn(self) -> str:
        message = (
            "Запрос отменён."
            if self._state.response_language == "ru"
            else "Request cancelled."
        )
        self.console.print(Text(message, style="yellow"))
        return message

    def _stream_chat_response(self, messages: list, model: str | None = None) -> str:
        """Stream chat response as markdown directly to console."""
        stream_model = model or self.config.fast_model
        safe_messages = redact_value(messages)
        assert isinstance(safe_messages, list)
        try:
            stream = self.client.chat.completions.create(
                model=stream_model,
                messages=safe_messages,
                stream=True,
            )
        except KeyboardInterrupt as error:
            raise ChatRequestCancelled from error
        try:
            return self._consume_chat_stream(stream)
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

        import time
        last_update = time.monotonic()
        
        with self._ui.live_markdown() as update_markdown:
            for chunk in stream:
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

                if text:
                    chunks.append(text)
                    
                    # Throttle rendering to ~10 FPS to prevent CPU choke
                    now = time.monotonic()
                    if now - last_update > 0.1:
                        update_markdown("".join(chunks))
                        last_update = now

            # Final update to guarantee the last parts are rendered
            update_markdown("".join(chunks))
                    
        return "".join(chunks)
