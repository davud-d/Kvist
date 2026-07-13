# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class AgentConfig:
    provider: str = "openai"
    fast_model: str = "gpt-5-mini"
    smart_model: str = "gpt-5-mini"
    planner_model: str = "gpt-5-mini"
    max_steps: int = 20
    stagnation_limit: int = 6
    max_consecutive_errors: int = 6
    repeat_action_limit: int = 4
    summary_recent_events: int = 8
    recent_feedback_limit: int = 4
    detailed_feedback_limit: int = 3
    rolling_summary_interval: int = 5
    prompt_result_char_limit: int = 5000
    prompt_value_char_limit: int = 1600
    cache_enabled: bool = True
    persist_logs: bool = True
    logs_dirname: str = ".devagent/logs"
    state_filename: str = ".devagent/state.json"
    model_temperature: float = 0.1
    max_retries: int = 2
    retry_base_delay: float = 2.0
    max_context_tokens: int = 120000
    runtime_model_fallbacks_enabled: bool = False
    stream: bool = True
    verbose: bool = False
    api_request_interval: float = 5.0
    api_timeout: float = 60.0


@dataclass(slots=True)
class PlanInfo:
    goal: str
    complexity: str
    plan: list[str]
    success_criteria: list[str]
    risks: list[str]


@dataclass(slots=True)
class RunState:
    current_agent_role: str = "coder"
    task_language: str = ""
    # User-facing response language ("ru"/"en") resolved by language_policy
    # before any model call. Protocol JSON stays English regardless.
    response_language: str = "en"
    system_os: str = ""
    system_shell: str = ""
    user_goal: str = ""
    create_goal: bool = False
    modify_goal: bool = False
    read_only_goal: bool = False
    verify_only_goal: bool = False
    plan_info: PlanInfo | None = None
    selected_action_role: str = ""
    selected_action_model: str = ""
    progress: int = 0
    summary: str = ""
    files_read: list[str] = field(default_factory=list)
    files_changed: list[str] = field(default_factory=list)
    pending_verification_paths: list[str] = field(default_factory=list)
    security_policy_violations: list[str] = field(default_factory=list)
    required_artifacts: list[str] = field(default_factory=list)
    missing_artifacts: list[str] = field(default_factory=list)
    verification_evidence: list[str] = field(default_factory=list)
    completion_blockers: list[str] = field(default_factory=list)
    required_next_actions: list[str] = field(default_factory=list)
    completion_ready: bool = False
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
    read_only_streak: int = 0
    pending_instruction: str = ""
    rolling_summaries: dict[int, str] = field(default_factory=dict)
    last_action_signature: str = ""
    last_progress: int = 0
    last_web_query: str = ""
    last_web_thought: str = ""
    cached_static_prompt: str = ""
    cached_static_suffix: str = ""
