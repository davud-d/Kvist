# -*- coding: utf-8 -*-
from __future__ import annotations

from openai import OpenAI
from rich.console import Console

from agent_context import (
    build_runtime_prompt,
    build_summary,
    compact_for_prompt,
    detect_request_language,
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
    count_tokens,
    init_tiktoken_encoding,
    prefer_smart_model,
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
    push_bounded,
    push_unique,
    record_error,
    record_user_message,
    register_feedback,
    register_final_answer,
    register_language_error,
    register_parse_error,
    render_feedback,
    reset_run_state,
    run_loop,
    update_progress,
    update_state_from_success,
)
from deps import detect_dependency_versions
from agent_state import AgentConfig, PlanInfo, RunState
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
from ui import AgentUI

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
        self._state.task_language = detect_request_language(user_goal)
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
        if not self._state.task_language:
            self._state.task_language = detect_request_language(user_goal)
        self._ui.debug_print(
            f"Restored from checkpoint: goal='{user_goal}', step={last_step}, progress={self._state.progress}%"
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
    _init_tiktoken_encoding = staticmethod(init_tiktoken_encoding)
    _count_tokens = count_tokens
    _trim_prompt_to_token_limit = trim_prompt_to_token_limit
    _prefer_smart_model = prefer_smart_model

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
    _record_error = record_error
    _feedback_to_line = feedback_to_line
    _feedback_to_detail = feedback_to_detail
    _render_feedback = render_feedback
    _build_early_stop_message = build_early_stop_message
    _build_limit_reached_message = build_limit_reached_message
    _is_repeat_blocked = is_repeat_blocked
    _action_signature = action_signature
    _record_user_message = record_user_message
    _push_unique = push_unique
    _push_bounded = push_bounded

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
