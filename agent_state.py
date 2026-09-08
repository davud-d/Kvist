# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


class RunCancelled(Exception):
    """The user interrupted an action-loop run while the model was generating.

    Raised from the streaming action call when a cancellation was requested, so
    the loop stops on its normal safe-stop path instead of running the model
    call to completion. Cancellation surfaces the work already done; it never
    rolls anything back and never aborts an action that is already executing.
    """


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
    logs_dirname: str = ".kvist/logs"
    state_filename: str = ".kvist/state.json"
    model_temperature: float = 0.1
    # Global reasoning-effort level on the Kvist scale (reasoning_effort.py):
    # false|low|medium|high|xhigh|max. Translated per provider at call time.
    reasoning_effort: str = "medium"
    max_retries: int = 2
    retry_base_delay: float = 2.0
    max_context_tokens: int = 120000
    runtime_model_fallbacks_enabled: bool = False
    stream: bool = True
    verbose: bool = False
    # When true, the LLM planning round-trip is skipped: the run starts the
    # action loop immediately with a minimal self-orient plan. Trades the
    # planner's guidance for a faster time-to-first-action.
    skip_planning: bool = False
    api_request_interval: float = 5.0
    api_timeout: float = 60.0


@dataclass(slots=True)
class PlanInfo:
    goal: str
    complexity: str
    plan: list[str]
    success_criteria: list[str]
    risks: list[str]
    # "report" when the user expects a substantive spoken answer/analysis,
    # "action" for pure mutation/run tasks. Classified by the planner (which
    # already reads task intent); a keyword heuristic is the fallback only.
    task_kind: str = "action"


@dataclass(slots=True)
class RunState:
    current_agent_role: str = "coder"
    task_language: str = ""
    # User-facing response language ("ru"/"en") resolved by language_policy
    # before any model call. Protocol JSON stays English regardless.
    response_language: str = "en"
    system_os: str = ""
    system_shell: str = ""
    system_project: str = ""
    system_git: str = ""
    system_sandbox: str = ""
    user_goal: str = ""
    create_goal: bool = False
    modify_goal: bool = False
    read_only_goal: bool = False
    verify_only_goal: bool = False
    # The user asked for a substantive spoken answer (explain/describe/list/
    # "what would you change"), not just a file mutation or a yes/no check.
    # Such a task must end with the model's own final_answer, never with the
    # synthetic auto-completion stub.
    report_goal: bool = False
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
    # Kind slug per blocker, same order. The UI labels these; the blocker
    # texts themselves are written for the model and are never shown.
    completion_blocker_kinds: list[str] = field(default_factory=list)
    required_next_actions: list[str] = field(default_factory=list)
    completion_ready: bool = False
    # Sealed outcome of the run: "in_progress" until finalize, then "completed"
    # or "blocked". Derived from the same state as progress/summary, never from
    # the model's own claim of being done.
    run_status: str = "in_progress"
    # Rule texts of hard environment denials seen this run (e.g. a sandbox
    # security refusal). Recovery policy: after a denial the model must try at
    # least two distinct alternative approaches before it may finish.
    hard_denial_rules: list[str] = field(default_factory=list)
    # Distinct action signatures attempted since the most recent hard denial.
    # Reset on each new denial; the policy is satisfied at len() >= 2.
    recovery_signatures: set[str] = field(default_factory=set)
    recovery_pending: bool = False
    recent_feedback: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    recent_result_details: list[str] = field(default_factory=list)
    error_history: list[str] = field(default_factory=list)
    error_patterns: dict[str, int] = field(default_factory=dict)
    seen_actions: dict[str, int] = field(default_factory=dict)
    # Actions rejected by the corrective guard. Kept separate from
    # seen_actions so that a refused action never consumes the repeat budget
    # of an action the runtime itself declined to execute.
    blocked_actions: dict[str, int] = field(default_factory=dict)
    # Read-only inspections still allowed while a corrective action is
    # required. Prevents the guard from forcing a blind mutation.
    corrective_read_budget: int = 0
    # Set when git_diff reveals a mass deletion. Blocks completion.
    destructive_diff: str = ""
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
    # Steps this run may still use. Seeded from config.max_steps and
    # extended only when the user answers the budget question.
    step_budget: int = 0
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
