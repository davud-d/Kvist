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
    # Model used for background session-title generation (session_titler.py).
    # Empty means "derive": the fast tier is already the cheapest configured
    # model for the active provider, so it is the default rather than a
    # fourth model id the user has to set explicitly.
    title_model: str = ""
    vision_model: str = ""
    max_steps: int = 150
    stagnation_limit: int = 6
    max_consecutive_errors: int = 6
    repeat_action_limit: int = 4
    summary_recent_events: int = 8
    recent_feedback_limit: int = 4
    # How many *full* action payloads stay in the prompt, one per distinct
    # action. At 3 a file read fell out of context three steps later and the
    # model re-read it; at 8, once a response could carry a batch of 8 actions,
    # one batch of listings pushed every file read out before the model could
    # edit from it. Reads are not capped per response, so 35 leaves room for a
    # response of a few dozen reads. The prompt budget (context_budget.py) is what actually caps
    # the size.
    detailed_feedback_limit: int = 35
    # Char caps on one action payload and on one string inside it. They exist to
    # stop a single runaway value from filling the window, not to summarize: a
    # file the executor already capped at max_read_chars must reach the model
    # whole, so prompt_value_char_limit is never below that cap.
    prompt_result_char_limit: int = 400_000
    prompt_value_char_limit: int = 200_000
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
    # 0 means "derive from the active model's real context window"
    # (context_budget.py). A non-zero value is an explicit override, as is the
    # AGENT_MAX_CONTEXT_TOKENS env var, which wins over both.
    max_context_tokens: int = 0
    runtime_model_fallbacks_enabled: bool = False
    stream: bool = True
    verbose: bool = False
    api_request_interval: float = 5.0
    # True when the interval above is the user's own choice (/settings-api), so
    # it outranks the per-provider pacing table in agent_model — a number the
    # user typed for OpenAI must not be silently zeroed by that table.
    api_interval_user_set: bool = False
    api_timeout: float = 60.0
    # Set only by the interactive frontends (classic terminal, stdio-multi,
    # ACP): end_session needs a person reading the conversation.
    end_session_enabled: bool = False


PLAN_OFFERED = "offered"
PLAN_PLANNING = "planning"
PLAN_APPROVED = "approved"


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
    system_workspace: str = ""
    system_sandbox: str = ""
    system_host: str = ""
    user_goal: str = ""
    create_goal: bool = False
    modify_goal: bool = False
    # Writes are hard-blocked, so only an authoritative source sets it: the
    # reader role or an explicit "do not modify" request.
    read_only_goal: bool = False
    # A keyword guess about a short check task; it only relaxes completion
    # accounting and must never block a write or end a run by itself.
    verify_only_goal: bool = False
    # The user asked for a substantive spoken answer (explain/describe/list/
    # "what would you change"), not just a file mutation or a yes/no check.
    # Such a task must end with the model's own final_answer, never with the
    # synthetic auto-completion stub.
    report_goal: bool = False
    # Plan mode (plan_mode.py). "" until the model offers a plan, then
    # "offered" (the person declined it; work continues), "planning" (read-only
    # exploration until propose_plan is accepted) or "approved".
    plan_phase: str = ""
    # The accepted plan, kept whole: it is model-visible input, so it is also
    # in the checkpoint and the prompt.
    approved_plan: str = ""
    # User clarifications remain part of the task across stops and resumes.
    task_updates: list[str] = field(default_factory=list)
    task_cancelled: bool = False
    # How plan mode ended for this turn ("declined", "not_accepted",
    # "approved", or ""); recorded in session history for the next turn.
    plan_outcome: str = ""
    # The number of the person's message this turn answers (end_session.py);
    # 0 for a resumed turn, which has no new message.
    message_id: int = 0
    # Set by end_session when it hands the model END_SESSION REVIEW; the
    # decision call is accepted only while it is set, so only in this turn.
    end_session_review: dict[str, Any] | None = None
    # end_session closed the session in this turn.
    session_closed: bool = False
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
    # The user stopped this run (Ctrl+C, Escape, the GUI's cancel). Recorded in
    # session history so later turns know the work was cut short on purpose.
    stopped_by_user: bool = False
    # The model ended the run with outcome "incomplete": seals as "blocked"
    # however clean the evidence looks, because nothing was claimed as done.
    finished_incomplete: bool = False
    # Exit code of the latest run_shell that actually ran (None until one
    # does). A "no change needed" finish is accepted only after a run that
    # exited cleanly, so a failing reproduction cannot be called "absent".
    last_shell_returncode: int | None = None
    # The model ended this attempt with outcome "no_change_needed" and the
    # run holds the evidence for it; the change-required blockers stand down.
    no_change_declared: bool = False
    # Ends of a turn (a response without calls) the runtime refused this run.
    # After one refusal the next end is taken as an honest "not done".
    finish_refusals: int = 0
    # Blank cells in CSV/TSV files read this run, and whether the model was
    # already asked to account for them before finishing (asked once, never twice).
    empty_data_cells: list[str] = field(default_factory=list)
    empty_cells_flagged: bool = False
    security_removal_flagged: bool = False
    # Rule texts of hard environment denials seen this run (e.g. a sandbox
    # security refusal). Logged only; a denial never obliges the model to
    # try other approaches before finishing.
    hard_denial_rules: list[str] = field(default_factory=list)
    # Distinct action signatures attempted since the most recent hard denial.
    # Until two exist the denial still counts as an unresolved error.
    recovery_signatures: set[str] = field(default_factory=set)
    recovery_pending: bool = False
    recent_feedback: list[str] = field(default_factory=list)
    recent_events: list[str] = field(default_factory=list)
    recent_result_details: list[str] = field(default_factory=list)
    # The action signature each detail came from, index-aligned with
    # recent_result_details ("" when unknown, e.g. an older checkpoint).
    recent_result_signatures: list[str] = field(default_factory=list)
    # Native tool calling (native_tools.py): None until the first step decides.
    # The conversation is not checkpointed; a resumed run starts a new one from
    # the runtime prompt, which already carries the recent results.
    native_tools: bool | None = None
    native_messages: list[dict[str, Any]] = field(default_factory=list)
    native_tool_defs: list[dict[str, Any]] = field(default_factory=list)
    native_note_sent: str = ""
    # Last inspection streak for which a sufficiency checkpoint was sent.
    # Shared by text and native calls; advisory, never a forced-write gate.
    inspection_note_streak: int = 0
    # (provider, model) pairs already warned about this run for a dropped
    # reasoning-control kwarg / dropped JSON mode (agent_model.py). Warned once
    # per run rather than once per process: the rejection itself is cached at
    # process scope, but a fresh run is a fresh chance to notice it.
    warned_reasoning_unsupported: set[tuple[str, str]] = field(default_factory=set)
    warned_json_mode_unsupported: set[tuple[str, str]] = field(default_factory=set)
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
