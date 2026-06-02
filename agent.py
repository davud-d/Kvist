# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any

from rich.console import Console

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
    load_checkpoint,
    persist_logs,
    sanitize_for_log,
    save_checkpoint,
)
from agent_model import (
    call_action_model,
    call_model,
    call_model_streaming,
    _create_json_completion,
    count_tokens,
    init_tiktoken_encoding,
    prefer_smart_model,
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
    command_language_error,
    execute_command,
    feedback_error_for_model,
    feedback_to_detail,
    feedback_to_line,
    is_repeat_blocked,
    normalize_error_for_model,
    plan_progress,
    refresh_completion_requirements,
    push_bounded,
    push_unique,
    record_error,
    record_user_message,
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
from role_router import route_role_with_llm, route_role
from system_context import current_os_context, current_shell_context, current_time_context
from ui import AgentUI
from language_utils import detect_request_language
from rich.live import Live
from rich.markdown import Markdown

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
        self._encoding = self._init_tiktoken_encoding()
        self._current_step = 0

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        self._reset_run_state()
        self._state.task_language = detect_request_language(user_goal)
        self._state.system_os = current_os_context()
        self._state.system_shell = current_shell_context()

        # ── Role routing: either passed from outside or detected locally ─────
        self._state.current_agent_role = routed_role or self._route_role(user_goal)

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
            return self._run_chat(user_goal)

        # ── Standard path (coder / devops): single run_loop ──────
        self._state.plan_info = self._create_plan(user_goal)
        if not self._state.selected_action_model:
            self._select_action_model_for_run()
        self._state.dependency_versions = detect_dependency_versions(self.executor.workspace)
        self._ui.print_plan(self._state.plan_info)
        return self._run_loop(user_goal, start_step=1)

    def resume(self) -> str | None:
        self._reset_run_state()
        result = self._load_checkpoint()
        if result is None:
            return None
        user_goal, last_step = result
        if not self._state.task_language:
            self._state.task_language = detect_request_language(user_goal)
        if not self._state.selected_action_model:
            self._select_action_model_for_run()
        self._ui.debug_print(
            f"Restored from checkpoint: goal='{user_goal}', step={last_step}, progress={self._state.progress}%"
        )
        self._ui.debug_print(
            f"Execution model restored: tier={self._state.selected_action_role}, model={self._state.selected_action_model}"
        )
        return self._run_loop(user_goal, start_step=last_step + 1)

    _reset_run_state = reset_run_state
    _run_loop = run_loop

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
    _is_repeat_blocked = is_repeat_blocked
    _action_signature = action_signature
    _record_user_message = record_user_message
    _required_followup_error = required_followup_error
    _set_required_next_actions = set_required_next_actions
    _update_completed_plan_steps = update_completed_plan_steps
    _push_unique = push_unique
    _push_bounded = push_bounded
    _update_verification_evidence = update_verification_evidence

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
        from agent_model import _load_role_prompt
        from context_utils import trim_history

        system_prompt = _load_role_prompt("chat")

        # Initialize chat history if it doesn't exist
        if not hasattr(self, "_chat_history"):
            self._chat_history = [{"role": "system", "content": system_prompt}]
        
        # Add new call messages (from user, system, and function calls)
        self._chat_history.append({"role": "user", "content": user_goal})

        # Trim history to keep only last 10 messages + system prompt
        self._chat_history = trim_history(self._chat_history, max_recent=10)

        try:
            if self.config.stream:
                content = self._stream_chat_response(self._chat_history)
            else:
                response = self.client.chat.completions.create(
                    model=self.config.fast_model,
                    messages=self._chat_history,
                )
                content = response.choices[0].message.content or ""
                
                # Safe extraction for freeflow (if using non-streaming branch)
                if hasattr(response, "content"):
                    content = response.content
                
                self.console.print(Markdown(content))
            
            # Save model response to history
            self._chat_history.append({"role": "assistant", "content": content})
            return content
            
        except Exception as error:
            error_msg = f"Chat error: {error}"
            self.console.print(f"[red]{error_msg}[/red]")
            return error_msg

    def _stream_chat_response(self, messages: list) -> str:
        """Stream chat response as markdown directly to console."""
        stream = self.client.chat.completions.create(
            model=self.config.fast_model,
            messages=messages,
            stream=True,
        )
        chunks: list[str] = []
        
        import time
        last_update = time.monotonic()
        
        with Live(Markdown(""), console=self.console, refresh_per_second=15) as live:
            for chunk in stream:
                text = ""
                
                # 1. OpenAI variant (strict objects with attributes)
                if hasattr(chunk, "choices") and chunk.choices:
                    delta = chunk.choices[0].delta
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
                        live.update(Markdown("".join(chunks)))
                        last_update = now
                        
            # Final update to guarantee the last parts are rendered
            live.update(Markdown("".join(chunks)))
                    
        return "".join(chunks)