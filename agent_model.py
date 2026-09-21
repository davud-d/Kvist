# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessageParam
from openai import (
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
    BadRequestError,
    AuthenticationError,
    PermissionDeniedError,
    NotFoundError,
    RateLimitError,
    InternalServerError,
)

from agent_context import (
    SECTION_PRIORITIES,
    split_prompt_into_sections,
)
from context_budget import (
    derived_prompt_budget,
    env_prompt_budget,
    learned_budget_for,
    note_input_overflow,
)
from context_utils import trim_history
from llm_clients import (
    fallback_candidates_for_model,
    FREEFLOW_PROVIDER,
    is_custom_provider,
    extract_custom_provider_name,
    provider_supports_json_mode,
    OPENAI_PROVIDER,
    ANTHROPIC_PROVIDER,
    DEEPSEEK_PROVIDER,
    OPENROUTER_PROVIDER,
)
from agent_state import RunCancelled
from reasoning_effort import (
    is_reasoning_error,
    merge_reasoning_kwargs,
    normalize_effort,
    strip_reasoning_kwargs,
)
from system_context import workspace_instructions_context
from redaction import redact_text, redact_value
from render_throttle import ThrottledRenderer

try:
    import tiktoken
except ImportError:
    tiktoken = None  # type: ignore[assignment]

_TOKEN_ENCODING_UNINITIALIZED = object()
_PROMPTS_ROOT = Path(__file__).resolve().parent / "prompts"
# Role prompts and user-selectable styles are different kinds of text and live
# apart: prompts/system/ is ours, prompts/styles/ is the user's choice.
_PROMPT_DIR = _PROMPTS_ROOT / "system"
_STYLES_DIR = _PROMPTS_ROOT / "styles"

# ── Safety: detect stale prompts/prompts/ duplicate ──────────────────────
_STALE_PROMPT_DIR = _PROMPTS_ROOT / "prompts"
if _STALE_PROMPT_DIR.is_dir():
    import sys as _sys

    print(
        f"[kvist] WARNING: Stale prompt directory detected at {_STALE_PROMPT_DIR}.",
        "Code loads prompts from", _PROMPT_DIR,
        "— the duplicate is NOT used. Remove it to avoid confusion.",
        file=_sys.stderr,
    )

_PROMPT_CACHE: dict[str, str] = {}

_ROLE_PROMPT_FILES: dict[str, str] = {
    "coder": "coder_prompt.txt",
    "devops": "devops_prompt.txt",
    "reader": "reader_prompt.txt",
    "chat": "chat_prompt.txt",
    "report": "report_prompt.txt",
    "commit": "commit_prompt.txt",
}

# Direct official-API providers (OpenAI, Anthropic, DeepSeek) are reached
# straight at the vendor endpoint and answer fast, so every artificial
# mechanism is pure overhead for them: no request pacing and no
# perceived-latency prefetch pipeline — clean sequential speed. Relay and
# aggregator providers (OpenRouter, FreeFlow, OmniRoute, custom endpoints)
# keep the paced defaults for rate-limit politeness.
_FAST_DIRECT_PROVIDERS: frozenset[str] = frozenset(
    {OPENAI_PROVIDER, ANTHROPIC_PROVIDER, DEEPSEEK_PROVIDER}
)

# Per-provider API request interval overrides (seconds) on top of the config
# default (AgentConfig.api_request_interval, 5.0 s). _FAST_DIRECT_PROVIDERS
# are always zeroed (no spacing) unless the user picked an interval in
# /settings-api. Override everything with the AGENT_API_INTERVAL env var.
_API_PROVIDER_INTERVALS: dict[str, float] = {
    OPENROUTER_PROVIDER: 2.5,
}


def _get_api_interval(self) -> float:
    """Return the API request interval for the current provider."""
    from os import environ as _environ

    env_override = _environ.get("AGENT_API_INTERVAL")
    if env_override is not None:
        try:
            return float(env_override)
        except ValueError:
            pass
    # An explicit interval of 0 (config or env var) disables all artificial
    # spacing; the fast-direct zeroing below never overrides that "off".
    if self.config.api_request_interval <= 0:
        return 0.0
    # An interval the user chose in /settings-api is an instruction, not a
    # default: it applies to every provider, including the direct ones this
    # table would otherwise zero.
    if getattr(self.config, "api_interval_user_set", False):
        return self.config.api_request_interval
    provider = getattr(self.config, "provider", "")
    if provider in _FAST_DIRECT_PROVIDERS:
        return 0.0
    return _API_PROVIDER_INTERVALS.get(provider, self.config.api_request_interval)


def _throttle_request_rate(self) -> None:
    """Keep consecutive API request *starts* at least one interval apart.

    Rate-limit protection only needs a minimum gap between when requests start.
    The model call itself already spends several seconds streaming its response,
    so measuring the gap from the previous request start collapses the wait to
    whatever the call did not already cover — usually nothing. The old code
    instead slept the full interval *after* every call, stacking a fixed
    2.5–5 s pause on top of each step; that dead time is the tedious wait
    between steps. Fast calls (or the first call) still get spaced apart.
    """
    interval = _get_api_interval(self)
    now = time.monotonic()
    last_start = getattr(self, "_last_request_start_ts", None)
    if interval > 0 and last_start is not None:
        remaining = interval - (now - last_start)
        if remaining > 0:
            time.sleep(remaining)
            now = time.monotonic()
    self._last_request_start_ts = now


# ── Perceived-latency pipeline ───────────────────────────────────────────────
# While the current step is on screen, the *next* step's action call is computed
# off the main loop so it is ready (or nearly so) when the loop asks for it. Only
# the pure LLM call runs ahead — never the executor — so journaling, checkpoints,
# and crash-consistency are untouched. Correctness is guaranteed because a
# prefetched response is used only when its prompt and model match exactly what
# the foreground would send anyway; any mismatch is discarded and re-called.
_PIPELINE_DWELL_MIN = 0.5
_PIPELINE_DWELL_CAP = 2.5


def _pipeline_allowed(self) -> bool:
    """Whether the perceived-latency prefetch pipeline may run for this run.

    Fast direct providers (OpenAI, Anthropic, DeepSeek) never pipeline: they
    run strictly sequential, with no next step computed in the background —
    clean speed. The pipeline is also off in verbose mode, whose debug stream
    must stay strictly sequential, and whenever request pacing is disabled
    (tests, ``api_request_interval=0``).
    """
    if self.config.verbose:
        return False
    provider = getattr(self.config, "provider", "")
    if provider in _FAST_DIRECT_PROVIDERS:
        return False
    return _get_api_interval(self) > 0


def _speculative_action_call(
    self, prompt: str, model: str, system_prompt: str
) -> tuple[str, int, int, float]:
    """Compute one action-model response off the main loop, side-effect free.

    Touches none of the shared per-call state (``self._last_*``, usage, perf
    logs): the foreground step emits usage when it consumes the result, so
    accounting stays attributed to the right step. Returns
    ``(content, input_tokens, output_tokens, duration)``.
    """
    trimmed = self._trim_prompt_to_token_limit(system_prompt, prompt)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": trimmed},
    ]
    messages = trim_history(messages, max_recent=4)
    input_tokens = self._count_tokens(system_prompt) + self._count_tokens(trimmed)

    _throttle_request_rate(self)
    started = time.monotonic()
    response = self._create_json_completion(model=model, messages=messages)
    content = safe_extract_completion_content(response, self.config.provider, model)
    duration = time.monotonic() - started
    if not content:
        raise RuntimeError("Model returned an empty response")
    return content, input_tokens, self._count_tokens(content), duration


def _launch_action_prefetch(self, prompt: str) -> None:
    """Start computing *prompt*'s action response in a daemon thread.

    At most one prefetch is in flight: a still-running worker is left alone; a
    finished-but-unconsumed one (a prior mispredict) is dropped first. The
    consuming side guarantees a single request is ever active by joining a stale
    worker before falling back to a normal call.
    """
    import threading

    holder = getattr(self, "_prefetch", None)
    if holder is not None:
        if holder["thread"].is_alive():
            return
        self._prefetch = None

    model = str(getattr(self._state, "selected_action_model", "")).strip()
    if not model:
        return
    system_prompt = build_role_system_prompt(
        self, getattr(self._state, "current_agent_role", "coder")
    )
    holder = {
        "prompt": prompt,
        "model": model,
        "content": None,
        "error": None,
        "input_tokens": 0,
        "output_tokens": 0,
        "duration": 0.0,
    }

    def _worker() -> None:
        try:
            content, input_tokens, output_tokens, duration = _speculative_action_call(
                self, prompt, model, system_prompt
            )
            holder["content"] = content
            holder["input_tokens"] = input_tokens
            holder["output_tokens"] = output_tokens
            holder["duration"] = duration
        except Exception as error:
            holder["error"] = error

    thread = threading.Thread(target=_worker, name="kvist-prefetch", daemon=True)
    holder["thread"] = thread
    self._prefetch = holder
    thread.start()


def _consume_action_prefetch(self, prompt: str, model: str) -> str | None:
    """Return a matching prefetched response, or ``None`` to call normally.

    A mismatch joins and drops the stale worker so at most one API request is
    ever in flight (a rare mispredict may briefly stall here — acceptable). A
    prefetch that errored also returns ``None`` so the foreground retries.
    """
    holder = getattr(self, "_prefetch", None)
    if holder is None:
        return None
    self._prefetch = None
    holder["thread"].join()
    if holder["prompt"] != prompt or holder["model"] != model or holder["error"] is not None:
        return None
    _emit_usage(
        self,
        model=model,
        input_tokens=holder["input_tokens"],
        output_tokens=holder["output_tokens"],
        kind="step",
        duration_seconds=holder["duration"],
    )
    self._last_reasoning_seconds = 0.0
    self._last_model_duration = holder["duration"]
    # The prefetch is a non-streaming call, so it produced no reasoning: drop
    # the previous step's thinking instead of leaving it on the live panel.
    _note_thinking(self, "")
    return holder["content"]


def _pipeline_dwell_seconds(self) -> float:
    """On-screen dwell for the current step ≈ last model latency, clamped.

    Matching the dwell to the last call's duration means the prefetched next
    step is usually ready exactly as the dwell ends, so the pipeline adds no net
    wall-clock: latency that used to be a dead spinner is spent reading the
    current step instead.
    """
    last = float(getattr(self, "_last_model_duration", 0.0) or 0.0)
    return max(_PIPELINE_DWELL_MIN, min(last, _PIPELINE_DWELL_CAP))


def _join_pending_prefetch(self) -> None:
    """Wait out any in-flight prefetch so no worker thread outlives the run."""
    holder = getattr(self, "_prefetch", None)
    if holder is None:
        return
    thread = holder.get("thread")
    if thread is not None:
        thread.join(timeout=self.config.api_timeout)
    self._prefetch = None


def _format_llm_error(error: Exception, provider: str | None = None) -> str:
    """Format LLM API errors into user-friendly messages."""
    raw_error_msg = str(error)
    error_msg = redact_text(raw_error_msg)
    error_lower = raw_error_msg.casefold()

    # Helper: build a custom provider label from the provider string
    def _custom_label(p: str) -> str:
        if is_custom_provider(p):
            name = extract_custom_provider_name(p)
            return f"Custom provider '{name}'"
        return None

    # Connection errors
    if isinstance(error, APIConnectionError):
        if provider == "omniroute":
            return (
                "OmniRoute endpoint is unavailable. Check that OmniRoute is running "
                "and base URL is http://localhost:20128/v1."
            )
        if provider == "openrouter":
            return (
                "Cannot reach OpenRouter API. Check your network and "
                "https://openrouter.ai/status."
            )
        if is_custom_provider(provider):
            return f"{_custom_label(provider)}: cannot reach the endpoint. Check base URL and network."
        return f"Connection error: {error_msg}"

    # Timeout errors
    if isinstance(error, APITimeoutError):
        if provider == "omniroute":
            return "OmniRoute request timed out. Check if OmniRoute is responsive."
        if provider == "openrouter":
            return "OpenRouter request timed out. Check if the service is responsive."
        if is_custom_provider(provider):
            return f"{_custom_label(provider)}: request timed out. Check if the endpoint is responsive."
        return f"Request timed out: {error_msg}"

    # Authentication errors (401)
    if isinstance(error, AuthenticationError):
        if provider == "omniroute":
            return "OmniRoute authentication failed. Check API key from Dashboard -> Endpoints."
        if provider == "openrouter":
            return "OpenRouter authentication failed. Check your API key at https://openrouter.ai/keys."
        if is_custom_provider(provider):
            return f"{_custom_label(provider)}: authentication failed. Check your API key."
        return f"Authentication failed: {error_msg}"

    # Permission errors (403)
    if isinstance(error, PermissionDeniedError):
        if provider == "omniroute":
            return "OmniRoute permission denied. Check API key permissions."
        if provider == "openrouter":
            return "OpenRouter permission denied. Check API key permissions at https://openrouter.ai/keys."
        if is_custom_provider(provider):
            return f"{_custom_label(provider)}: permission denied. Check API key permissions."
        return f"Permission denied: {error_msg}"

    # Not found errors (404)
    if isinstance(error, NotFoundError):
        if provider == "omniroute":
            return (
                "OmniRoute model or endpoint was not found. Check exact model id, "
                "for example kr/claude-sonnet-4.5."
            )
        if provider == "openrouter":
            return (
                "OpenRouter model not found. Check exact model id in provider/model "
                "format (e.g. openai/gpt-5.4-mini). See https://openrouter.ai/models."
            )
        if is_custom_provider(provider):
            return (
                f"{_custom_label(provider)}: model or endpoint not found. "
                "Check the base URL and model ID."
            )
        return f"Resource not found: {error_msg}"

    # Rate limit errors (429)
    if isinstance(error, RateLimitError):
        if provider == "omniroute":
            return "OmniRoute or upstream provider hit rate limit/quota."
        if provider == "openrouter":
            return (
                "OpenRouter rate limit exceeded. Check your plan and credits at "
                "https://openrouter.ai/settings."
            )
        if is_custom_provider(provider):
            return f"{_custom_label(provider)}: rate limit exceeded."
        return f"Rate limit exceeded: {error_msg}"

    # Bad request errors (400)
    if isinstance(error, BadRequestError):
        # Check for OmniRoute-specific bad request patterns
        if provider == "omniroute" and any(
            marker in error_lower
            for marker in ("improperly formed", "bad_request", "invalid_request_error")
        ):
            return (
                "OmniRoute returned 400 bad_request. This usually means bad model id, "
                "unsupported request shape, broken provider transform, or upstream provider "
                "rejected the request. Try another exact OmniRoute model id or check OmniRoute logs."
            )
        if provider == "openrouter":
            return (
                "OpenRouter returned 400 bad request. This usually means invalid model id, "
                "unsupported parameters, or the upstream provider rejected the request. "
                "Verify the model id format (provider/model) and check OpenRouter docs."
            )
        if is_custom_provider(provider):
            return (
                f"{_custom_label(provider)}: bad request. This usually means an invalid "
                "model ID, unsupported parameters, or the upstream provider rejected "
                "the request."
            )
        return f"Bad request: {error_msg}"

    # Server errors (500+)
    if isinstance(error, InternalServerError):
        if provider == "omniroute":
            return "OmniRoute or upstream provider failed. Check OmniRoute logs."
        if provider == "openrouter":
            return "OpenRouter or upstream provider failed. Check https://openrouter.ai/status."
        if is_custom_provider(provider):
            return f"{_custom_label(provider)}: endpoint returned a server error."
        return f"Server error: {error_msg}"

    # Generic APIStatusError
    if isinstance(error, APIStatusError):
        status_code = getattr(error, "status_code", None)
        if status_code and status_code >= 500:
            if provider == "omniroute":
                return f"OmniRoute or upstream provider failed (HTTP {status_code}). Check OmniRoute logs."
            if provider == "openrouter":
                return f"OpenRouter or upstream provider failed (HTTP {status_code}). Check https://openrouter.ai/status."
            if is_custom_provider(provider):
                return f"{_custom_label(provider)}: endpoint failed (HTTP {status_code})."
            return f"Server error (HTTP {status_code}): {error_msg}"

    # Fallback
    return error_msg


def _load_role_prompt(role: str) -> str:
    """Load and cache system prompt for the given role."""
    if role in _PROMPT_CACHE:
        return _PROMPT_CACHE[role]
    filename = _ROLE_PROMPT_FILES.get(role, "coder_prompt.txt")
    text = (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
    _PROMPT_CACHE[role] = text
    return text


def _session_style_name(self, style_type: str) -> str:
    """The style this session was opened with, not the current global pick.

    One session, one style: `agent_logging.restore_session_document` stamps the
    choice when a session starts, so a plan built on step 0 and the steps that
    follow it always come from the same beliefs.
    """
    styles = getattr(self, "_session_styles", None)
    if isinstance(styles, dict):
        name = styles.get(style_type)
        if isinstance(name, str) and name.strip():
            return name

    from user_config import get_saved_style

    return get_saved_style(style_type)


def _read_style_prompt(self, style_type: str) -> str:
    """Text of this session's style for that family, "" when none.

    The prompt for each style lives in its own file under
    `prompts/styles/<chat|agents>/<slug>.txt`; a missing or empty file means
    "no style instruction". The file is read per call rather than cached, so an
    edit to the selected file lands on the next step; which file is selected is
    fixed for the session.
    """
    from user_config import style_slug

    slug = style_slug(_session_style_name(self, style_type))
    path = _STYLES_DIR / style_type / f"{slug}.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError):
        return ""


def _style_context(self, role: str) -> str:
    """This session's response-style instruction for this role, if any.

    Sits high in the system prompt — right after the role prompt, before
    workspace instructions.
    """
    prompt = _read_style_prompt(self, "chat" if role == "chat" else "agents")
    if not prompt:
        return ""
    return f"## RESPONSE STYLE\n\n{prompt}"


def planner_style_context(self) -> str:
    """The working style, framed for the planner instead of the executor.

    A style file speaks to the agent that will do the work ("you leave it",
    "you fix it"), so handing it over unframed makes the planner answer in
    character instead of planning. The preamble says whose beliefs these are and
    what they are allowed to change; `planner_prompt.txt` carries the matching
    bound after the block.
    """
    prompt = _read_style_prompt(self, "agents")
    if not prompt:
        return ""
    return (
        "## EXECUTOR WORKING STYLE\n\n"
        "The run you are planning will be carried out by an agent that holds "
        "the beliefs below. They address that agent, not you: you never act "
        "them out and never answer in their voice. Plan the run this agent will "
        "actually perform — which steps it takes, what it treats as finished, "
        "what it refuses to do — so the plan and the executor do not pull in "
        "different directions.\n\n"
        f"{prompt}"
    )


def build_role_system_prompt(self, role: str) -> str:
    """Role prompt, the user's response style, then this workspace's own
    instruction file — each read per call so edits land on the next step.
    """
    base = _load_role_prompt(role)
    style = _style_context(self, role)
    if style:
        base = f"{base}\n\n{style}"
    project = workspace_instructions_context(self.executor.workspace, role)
    return f"{base}\n\n{project}" if project else base


def call_action_model(self, prompt: str) -> str:
    # The model is pinned once per run, so the escalation conditions inside
    # _prefer_smart_model (errors, parse failures, stagnation) could never fire
    # after the first step. Escalate once, never downgrade.
    if (
        self._state.selected_action_role != "smart"
        and self.config.smart_model
        and self._prefer_smart_model()
    ):
        self._state.selected_action_role = "smart"
        self._state.selected_action_model = self.config.smart_model

    preferred_model = str(getattr(self._state, "selected_action_model", "")).strip()
    if not preferred_model:
        preferred_model = self._select_action_model_for_run()

    # Consume a pipeline prefetch computed while the previous step was on screen,
    # but only when it matches this exact prompt+model; otherwise call normally.
    prefetched = _consume_action_prefetch(self, prompt, preferred_model)
    if prefetched is not None:
        return prefetched

    role = getattr(self._state, "current_agent_role", "coder")
    system_prompt = build_role_system_prompt(self, role)
    self._retries_exhausted = False
    started = time.monotonic()
    try:
        content = self._call_model(
            model=preferred_model,
            system_prompt=system_prompt,
            user_prompt=prompt,
            usage_kind="step",
        )
    except RunCancelled:
        # Not a model error: don't mark retries exhausted, just propagate.
        raise
    except Exception:
        self._retries_exhausted = True
        raise
    self._last_model_duration = time.monotonic() - started
    return content


def select_action_model_for_run(self) -> str:
    selected_role = str(getattr(self._state, "selected_action_role", "")).strip().lower()
    if selected_role == "smart":
        selected_model = self.config.smart_model
    elif selected_role == "fast":
        selected_model = self.config.fast_model
    else:
        selected_role = "smart" if self._prefer_smart_model() else "fast"
        selected_model = self.config.smart_model if selected_role == "smart" else self.config.fast_model

    self._state.selected_action_role = selected_role
    self._state.selected_action_model = selected_model
    return selected_model


# Call kinds whose prompt *is* the run's conversation context. A plan, report
# or router call sends its own one-off prompt, so it must never be mistaken for
# how full the conversation has grown.
_CONTEXT_USAGE_KINDS = frozenset({"step", "chat"})


def context_token_limit(self) -> int:
    """Tokens one call may occupy — the budget ``_trim_prompt_to_token_limit`` enforces.

    Derived from the *active* model's documented window, so escalating
    fast → smart mid-run re-resolves it. ``AGENT_MAX_CONTEXT_TOKENS`` and an
    explicit ``config.max_context_tokens`` are the two ways to override it.
    """
    active_model = self._state.selected_action_model or self.config.fast_model
    budget = (
        env_prompt_budget()
        or self.config.max_context_tokens
        or derived_prompt_budget(self.config.provider, active_model)
    )
    # A rejection the provider actually returned outranks every guess, an
    # explicit override included — otherwise the override would make the retry
    # resend exactly the prompt that was just refused.
    learned = learned_budget_for(active_model)
    if learned is None:
        return budget
    return min(budget, learned)


def _emit_usage(
    self,
    *,
    model: str,
    input_tokens: int,
    output_tokens: int,
    kind: str,
    duration_seconds: float = 0.0,
) -> None:
    """Report one model call's token usage to UIs exposing ``print_usage``.

    Optional capability (like the streaming reasoning channel): probed with
    ``getattr`` so UIs without a usage channel keep working. Counts are real
    tiktoken measurements of the exact prompt/response text, not estimates.

    Two different quantities travel here and must not be confused. ``input_tokens``
    /``output_tokens`` are *spend*: every step resends the whole conversation, so
    a UI summing them across a run reaches hundreds of thousands of tokens while
    the conversation itself is a fraction of that. ``context_tokens`` is that
    conversation — the size of this one prompt — and is reported only for the
    kinds that carry it (0 otherwise, meaning "leave the last known value").
    """
    callback = getattr(getattr(self, "_ui", None), "print_usage", None)
    if callback is None:
        return
    normalized_input = max(0, int(input_tokens))
    callback(
        {
            "input_tokens": normalized_input,
            "output_tokens": max(0, int(output_tokens)),
            "context_tokens": normalized_input if kind in _CONTEXT_USAGE_KINDS else 0,
            "context_limit": max(0, int(context_token_limit(self))),
            "model": str(model),
            "kind": str(kind),
            "step": int(getattr(self, "_current_step", 0) or 0),
            "duration_seconds": max(0.0, float(duration_seconds)),
        }
    )


def call_model(
    self,
    model: str,
    system_prompt: str,
    user_prompt: str,
    *,
    usage_kind: str = "model",
) -> str:
    trimmed_prompt = self._trim_prompt_to_token_limit(system_prompt, user_prompt)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": trimmed_prompt},
    ]
    messages = trim_history(messages, max_recent=4)

    # Reset so a non-thinking step never inherits the previous step's timing.
    self._last_reasoning_seconds = 0.0

    # ── context metrics (computed once) ──────────────────────────
    _prompt_chars = len(system_prompt) + len(trimmed_prompt)
    _system_tokens = self._count_tokens(system_prompt)
    _prompt_tokens = self._count_tokens(trimmed_prompt)
    _input_tokens = _system_tokens + _prompt_tokens
    _estimated_tokens = _prompt_chars // 4
    _history_size_kb = sys.getsizeof(trimmed_prompt) / 1024.0
    self._ui.debug_print(
        f"[tokens] step={getattr(self, '_current_step', 0)} kind={usage_kind} "
        f"input={_input_tokens} (system={_system_tokens}, prompt={_prompt_tokens}), "
        f"chars={_prompt_chars}"
    )

    models_to_try = list(
        fallback_candidates_for_model(
            self.config.provider,
            model,
            allow_runtime_fallbacks=self.config.runtime_model_fallbacks_enabled,
        )
    )

    last_error: Exception | None = None
    for current_model in models_to_try:
        cache_key = sha256(
            json.dumps(
                {
                    "model": current_model,
                    "messages": messages,
                    "effort": normalize_effort(self.config.reasoning_effort),
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        if self.config.cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        for attempt in range(1, self.config.max_retries + 1):
            _throttle_request_rate(self)
            request_start = time.monotonic()
            try:
                if self.config.stream:
                    content = self._call_model_streaming(current_model, messages)
                else:
                    response = self._create_json_completion(
                        model=current_model,
                        messages=messages,
                    )
                    content = safe_extract_completion_content(
                        response, self.config.provider, current_model
                    )
                total_duration = time.monotonic() - request_start
                if not content:
                    raise RuntimeError("Model returned an empty response")
                if self.config.cache_enabled:
                    self._cache[cache_key] = content
                # Real usage for this API call; cache hits emit nothing (they
                # consume no tokens).
                _emit_usage(
                    self,
                    model=current_model,
                    input_tokens=_input_tokens,
                    output_tokens=self._count_tokens(content),
                    kind=usage_kind,
                    duration_seconds=total_duration,
                )
                # ── log successful API request ───────────────────
                self._perf_logger.log_api_request(
                    step=getattr(self, "_current_step", 0),
                    model=current_model,
                    request_start=request_start,
                    ttft=getattr(self, "_last_ttft", None),
                    stream_end=getattr(self, "_last_stream_end", None),
                    total_duration=total_duration,
                    messages_count=len(messages),
                    input_tokens=_input_tokens,
                    estimated_tokens=_estimated_tokens,
                    prompt_chars=_prompt_chars,
                    history_size_kb=_history_size_kb,
                    chunk_count=getattr(self, "_last_chunk_count", None),
                    avg_chunk_size=getattr(self, "_last_avg_chunk_size", None),
                    chunks_per_second=getattr(self, "_last_chunks_per_second", None),
                    ok=True,
                )

                return content
            except RunCancelled:
                # A cancellation is not a provider failure: never retried,
                # never logged as an API error — it propagates straight to the
                # loop's safe-stop path.
                raise
            except Exception as error:
                total_duration = time.monotonic() - request_start
                # ── log failed API request ───────────────────────
                self._perf_logger.log_api_request(
                    step=getattr(self, "_current_step", 0),
                    model=current_model,
                    request_start=request_start,
                    total_duration=total_duration,
                    messages_count=len(messages),
                    input_tokens=_input_tokens,
                    estimated_tokens=_estimated_tokens,
                    prompt_chars=_prompt_chars,
                    history_size_kb=_history_size_kb,
                    ok=False,
                    error=str(error),
                )
                last_error = error
                if is_context_overflow_error(error):
                    shrunk = _shrink_prompt_after_overflow(
                        self,
                        model=current_model,
                        system_prompt=system_prompt,
                        user_prompt=user_prompt,
                        attempted_tokens=_input_tokens,
                    )
                    # An overflow is deterministic, so a retry is worth paying
                    # for only on a prompt that actually got smaller. It may not
                    # have: the system prompt is never trimmed, and a learned
                    # cap is keyed to the model that was refused, which is not
                    # always the one whose budget the trimmer consults.
                    if len(shrunk) >= len(trimmed_prompt):
                        break
                    trimmed_prompt = shrunk
                    messages = [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": trimmed_prompt},
                    ]
                    _prompt_tokens = self._count_tokens(trimmed_prompt)
                    _input_tokens = _system_tokens + _prompt_tokens
                    _prompt_chars = len(system_prompt) + len(trimmed_prompt)
                    continue
                if not _should_retry_model_error(error):
                    break
                if attempt < self.config.max_retries:
                    delay = self.config.retry_base_delay * (2 ** (attempt - 1))
                    error_msg_lowered = str(error).casefold()
                    if any(m in error_msg_lowered for m in ("rate limit", "too many requests", "429", "quota", "high demand", "exhausted", "503", "timeout")):
                        delay = 5.0
                    self._ui.debug_print(
                        f"API error | provider={self.config.provider} model={current_model} "
                        f"error={type(error).__name__}: {sanitize_error_message(str(error))} "
                        f"attempt={attempt}/{self.config.max_retries} "
                        f"retry_in={delay:.1f}s"
                    )
                    sleep_start = time.monotonic()
                    time.sleep(delay)
                    actual_sleep = (time.monotonic() - sleep_start) * 1000
                    expected_sleep = delay * 1000
                    lag = actual_sleep - expected_sleep
                    self._perf_logger.log_event_loop_lag(
                        lag_ms=lag,
                        step=getattr(self, "_current_step", 0),
                        context="retry_sleep",
                    )

    safe_error = sanitize_error_message(
        str(last_error).strip() if last_error else "unknown provider error"
    )
    raise RuntimeError(f"Failed to get model response: {safe_error}")


def _note_thinking(self, text: str) -> None:
    """Forward streamed reasoning to a UI that shows a live thinking panel.

    Optional capability: UIs without ``set_thinking`` (headless, tests) are
    skipped, exactly like the ``print_usage`` usage channel.
    """
    notify = getattr(self._ui, "set_thinking", None)
    if notify is not None:
        notify(text)


def _note_thinking_end(self) -> None:
    """Tell a UI that the model has stopped thinking and started answering."""
    notify = getattr(self._ui, "end_thinking", None)
    if notify is not None:
        notify()


def call_model_streaming(
    self,
    model: str,
    messages: list[ChatCompletionMessageParam],
) -> str:
    stream = self._create_json_completion(
        model=model,
        messages=messages,
        stream=True,
    )
    chunks: list[str] = []
    reasoning_chunks: list[str] = []
    last_reasoning_push = 0.0
    thought_shown = False
    stream_start = time.monotonic()
    first_token_time: float | None = None
    reasoning_start_time: float | None = None
    reasoning_end_time: float | None = None
    chunk_count = 0
    total_chunk_bytes = 0
    renderer = ThrottledRenderer(max_fps=10)
    # A new call owns the thinking column: drop the previous step's reasoning
    # so a provider that streams none does not leave stale text on screen.
    _note_thinking(self, "")
    try:
        for chunk in stream:
            # Checked each chunk so a cancellation stops generation without
            # waiting for the model to finish. The finally closes the HTTP
            # stream; the loop then unwinds through call_model to run_loop's
            # RunCancelled handler.
            if self._cancel_event.is_set():
                raise RunCancelled
            delta = safe_extract_stream_delta(chunk)
            reasoning_text = safe_extract_reasoning_delta(delta)
            if reasoning_text:
                if reasoning_start_time is None:
                    reasoning_start_time = time.monotonic()
                reasoning_chunks.append(reasoning_text)
                now = time.monotonic()
                if now - last_reasoning_push > 0.1:
                    _note_thinking(self, "".join(reasoning_chunks))
                    last_reasoning_push = now
            if delta and delta.content:
                # The first answer token ends the thinking phase.
                if reasoning_start_time is not None and reasoning_end_time is None:
                    reasoning_end_time = time.monotonic()
                    # Seal thinking with everything buffered: a fast stream can
                    # otherwise end inside the throttle window and drop the tail.
                    _note_thinking(self, "".join(reasoning_chunks))
                    _note_thinking_end(self)
                if first_token_time is None:
                    first_token_time = time.monotonic()
                chunk_count += 1
                total_chunk_bytes += len(delta.content)
                chunks.append(delta.content)
                renderer.feed(delta.content)
                if self.config.verbose and not thought_shown:
                    rendered = renderer.maybe_render()
                    if rendered and '"thought"' in rendered and len(rendered) > 80:
                        thought_shown = True
                        print(f"  [streaming] {rendered[:120]}...", flush=True)
    finally:
        # An abandoned HTTP stream would otherwise hold its connection open;
        # applies to cancellation, provider failure, and normal completion.
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception as error:
                self._ui.debug_print(
                    f"Action stream close failed: {redact_text(str(error))}"
                )
    # flush any remaining buffered output
    if self.config.verbose and not thought_shown:
        leftover = renderer.flush()
        if leftover and '"thought"' in leftover and len(leftover) > 80:
            print(f"  [streaming] {leftover[:120]}...", flush=True)
    stream_end = time.monotonic()
    stream_duration = stream_end - stream_start

    # Reasoning that never yielded a content token was never sealed in the loop.
    if reasoning_chunks and reasoning_end_time is None:
        _note_thinking(self, "".join(reasoning_chunks))
        _note_thinking_end(self)

    # Reasoning that never yielded content still counts up to stream end.
    if reasoning_start_time is not None:
        end = reasoning_end_time if reasoning_end_time is not None else stream_end
        self._last_reasoning_seconds = end - reasoning_start_time
    else:
        self._last_reasoning_seconds = 0.0

    # store streaming metrics for the parent call_model to pick up
    self._last_ttft = (first_token_time - stream_start) if first_token_time is not None else None
    self._last_stream_end = stream_duration
    self._last_chunk_count = chunk_count
    self._last_avg_chunk_size = (total_chunk_bytes / chunk_count) if chunk_count > 0 else None
    self._last_chunks_per_second = (chunk_count / stream_duration) if stream_duration > 0 else None

    return "".join(chunks)


def _warn_reasoning_unsupported(self, error: Exception) -> None:
    """Surface, every time it happens, that reasoning effort was dropped.

    The user asked for a persistent warning rather than a one-time notice, so
    there is deliberately no de-duplication here.
    """
    level = normalize_effort(self.config.reasoning_effort)
    message = (
        f"Provider rejected reasoning effort '{level}' — retrying this request "
        "without it. Pick a level the provider supports with /effort."
    )
    printer = getattr(self._ui, "print_error", None)
    if callable(printer):
        printer(message)
    self._ui.debug_print(f"[reasoning] dropped after: {redact_text(str(error))}")


def _create_completion_with_reasoning_fallback(self, request_kwargs: dict[str, Any]):
    """Create a completion; on a reasoning-control rejection, warn and retry
    once without any reasoning kwargs. Other errors propagate unchanged."""
    try:
        return self.client.chat.completions.create(**request_kwargs)
    except Exception as error:
        if not is_reasoning_error(error):
            raise
        _warn_reasoning_unsupported(self, error)
        return self.client.chat.completions.create(
            **strip_reasoning_kwargs(request_kwargs)
        )


def _create_json_completion(
    self,
    model: str,
    messages: list[ChatCompletionMessageParam],
    stream: bool = False,
    json_mode: bool = True,
):
    safe_messages = redact_value(messages)
    assert isinstance(safe_messages, list)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": safe_messages,
        "temperature": self.config.model_temperature,
        "timeout": self.config.api_timeout,
    }
    if json_mode and provider_supports_json_mode(self.config.provider):
        request_kwargs["response_format"] = {"type": "json_object"}
    if stream:
        request_kwargs["stream"] = True
        request_kwargs["stream_options"] = {"include_usage": True}
    merge_reasoning_kwargs(
        request_kwargs, self.config.provider, self.config.reasoning_effort
    )

    first_error: Exception | None = None
    try:
        return _create_completion_with_reasoning_fallback(self, request_kwargs)
    except Exception as error:
        if not _supports_plain_json_fallback(error):
            raise
        first_error = error

    fallback_kwargs: dict[str, Any] = {
        "model": model,
        "messages": safe_messages,
    }
    # Omit temperature when the first error was temperature-related so that the
    # fallback request does not repeat the same failure.
    if first_error is None or "temperature" not in str(first_error).casefold():
        fallback_kwargs["temperature"] = self.config.model_temperature
    if stream:
        fallback_kwargs["stream"] = True
    return self.client.chat.completions.create(**fallback_kwargs)


def init_tiktoken_encoding():
    if tiktoken is None:
        return None
    try:
        return tiktoken.encoding_for_model("gpt-4o")
    except Exception:
        try:
            return tiktoken.get_encoding("cl100k_base")
        except Exception:
            return None


def count_tokens(self, text: str) -> int:
    encoding = self._encoding
    if encoding is _TOKEN_ENCODING_UNINITIALIZED:
        encoding = self._init_tiktoken_encoding()
        self._encoding = encoding
    if encoding is None:
        return len(text) // 4
    return len(encoding.encode(text, disallowed_special=()))


def trim_prompt_to_token_limit(self, system_prompt: str, user_prompt: str) -> str:
    """Trim *user_prompt* to fit within the provider token budget.

    Instead of blindly chopping the middle of the prompt (which can discard
    critical context like verification evidence or completion blockers), this
    implementation parses the prompt into logical sections, assigns each a
    priority tier, and trims or drops sections from the lowest tier upward.

    Priority tiers (see ``agent_context.SECTION_PRIORITIES``):
      0 – critical (never touched)
      1 – high     (truncated to ~50% when space is tight)
      2 – medium   (truncated to ~30% when space is tight)
      3 – low      (dropped first, truncated to ~20% if kept)
    """
    max_tokens = context_token_limit(self)
    system_tokens = self._count_tokens(system_prompt)
    prompt_tokens = self._count_tokens(user_prompt)

    if system_tokens + prompt_tokens <= max_tokens:
        return user_prompt

    available = max_tokens - system_tokens
    if available < 600:
        available = 600

    # ── Parse into sections ──────────────────────────────────────────
    sections = split_prompt_into_sections(user_prompt)
    if not sections or (len(sections) == 1 and not sections[0][0]):
        # Fallback: single blob with no recognisable headers.
        # Use simple proportional truncation from the end (still beats middle-chop).
        ratio = available / max(prompt_tokens, 1)
        target_chars = max(400, int(len(user_prompt) * ratio * 0.90))
        if target_chars < len(user_prompt):
            trimmed = user_prompt[:target_chars] + "\n[... truncated]"
        else:
            trimmed = user_prompt
        self._ui.debug_print(
            f"Prompt trimmed (fallback): sys={system_tokens}, user={prompt_tokens} "
            f"-> available {available} (limit {max_tokens})"
        )
        return trimmed + "\n\n[CONTEXT TRIMMED BY PRIORITY]"

    # ── Token-count each section ──────────────────────────────────────
    section_tokens: list[int] = []
    for _header, text, _priority in sections:
        section_tokens.append(self._count_tokens(text))

    total_user_tokens = sum(section_tokens)

    # ── Helper: rebuild final prompt from current section list ────────
    def rebuild(secs: list[tuple[str, str, int]], stoks: list[int]) -> str:
        parts: list[str] = []
        for (_h, text, _p), tok in zip(secs, stoks):
            if text is not None and tok > 0:
                parts.append(text)
        return "\n".join(parts)

    # ── Tier 3: drop low-priority sections entirely ───────────────────
    if total_user_tokens > available:
        for i, (_header, text, priority) in enumerate(sections):
            if priority != 3:
                continue
            if text is None:
                continue
            total_user_tokens -= section_tokens[i]
            section_tokens[i] = 0
            sections[i] = (_header, None, priority)
            if total_user_tokens <= available:
                break

    # ── Tier 2: truncate medium-priority sections ─────────────────────
    if total_user_tokens > available:
        for i, (header, text, priority) in enumerate(sections):
            if priority != 2:
                continue
            if text is None:
                continue
            # Keep ~30% of the original token budget for this section
            target_tok = max(60, int(section_tokens[i] * 0.30))
            truncated = _truncate_section_text(text, target_tok, self._count_tokens)
            sections[i] = (header, truncated, priority)
            new_tok = self._count_tokens(truncated)
            total_user_tokens = total_user_tokens - section_tokens[i] + new_tok
            section_tokens[i] = new_tok
            if total_user_tokens <= available:
                break

    # ── Tier 1: truncate high-priority sections (keep ~50%) ───────────
    if total_user_tokens > available:
        for i, (header, text, priority) in enumerate(sections):
            if priority != 1:
                continue
            if text is None:
                continue
            target_tok = max(100, int(section_tokens[i] * 0.50))
            truncated = _truncate_section_text(text, target_tok, self._count_tokens)
            sections[i] = (header, truncated, priority)
            new_tok = self._count_tokens(truncated)
            total_user_tokens = total_user_tokens - section_tokens[i] + new_tok
            section_tokens[i] = new_tok
            if total_user_tokens <= available:
                break

    # ── Tier 2 aggressive: if still over, squeeze medium sections harder ──
    if total_user_tokens > available:
        for i, (header, text, priority) in enumerate(sections):
            if priority != 2:
                continue
            if text is None:
                continue
            target_tok = max(40, int(section_tokens[i] * 0.15))
            truncated = _truncate_section_text(text, target_tok, self._count_tokens)
            sections[i] = (header, truncated, priority)
            new_tok = self._count_tokens(truncated)
            total_user_tokens = total_user_tokens - section_tokens[i] + new_tok
            section_tokens[i] = new_tok
            if total_user_tokens <= available:
                break

    # ── Last resort: proportional cut across ALL remaining sections ───
    if total_user_tokens > available:
        ratio = available / max(total_user_tokens, 1)
        for i, (header, text, priority) in enumerate(sections):
            if text is None or section_tokens[i] <= 0:
                continue
            target_tok = max(50, int(section_tokens[i] * ratio * 0.90))
            truncated = _truncate_section_text(text, target_tok, self._count_tokens)
            sections[i] = (header, truncated, priority)
            section_tokens[i] = self._count_tokens(truncated)

    trimmed = rebuild(sections, section_tokens)
    if not trimmed:
        trimmed = user_prompt[:max(400, available * 4)]

    trimmed += "\n\n[CONTEXT TRIMMED BY PRIORITY]"

    # Priority-0 sections are never trimmed and section targets are per-section,
    # so the tiers alone cannot promise the total fits. A request over the real
    # limit is refused outright, so a degraded prompt beats a hard 400.
    if self._count_tokens(trimmed) > available:
        trimmed = _truncate_section_text(trimmed, available, self._count_tokens)

    kept_count = sum(1 for t in section_tokens if t > 0)
    self._ui.debug_print(
        f"Prompt trimmed: sys={system_tokens}, user={prompt_tokens} "
        f"-> {sum(section_tokens)} tokens in {kept_count}/{len(sections)} sections "
        f"(limit {max_tokens}, available {available})"
    )
    return trimmed


def trim_chat_messages_to_token_limit(
    self,
    messages: list[ChatCompletionMessageParam],
) -> list[ChatCompletionMessageParam]:
    """Keep a chat request inside the active model's prompt budget.

    This used to run for FreeFlow only, so on every other provider the chat
    history was bounded by message *count* alone — five turns, however large.
    The budget is now the same per-model one the action loop uses, so a long
    conversation is cut when it actually gets long.

    Every retained/truncated message is rebuilt with ``{**message, ...}``, so
    sibling keys such as ``reasoning_content`` survive — a reasoning provider
    that ever reaches this path must still get that field back verbatim.
    """
    max_tokens = context_token_limit(self)
    normalized = [
        message for message in messages
        if isinstance(message, dict) and isinstance(message.get("content"), str)
    ]
    total_tokens = sum(self._count_tokens(message["content"]) for message in normalized)
    if total_tokens <= max_tokens:
        return normalized

    def truncate_content(text: str, token_budget: int) -> str:
        marker = "\n[... context trimmed ...]\n"
        if token_budget <= self._count_tokens(marker):
            return ""

        low = 0
        high = len(text)
        while low < high:
            kept_chars = (low + high + 1) // 2
            head_chars = kept_chars // 2
            candidate = text[:head_chars] + marker + text[-(kept_chars - head_chars):]
            if self._count_tokens(candidate) <= token_budget:
                low = kept_chars
            else:
                high = kept_chars - 1

        head_chars = low // 2
        return text[:head_chars] + marker + text[-(low - head_chars):]

    system = normalized[:1] if normalized and normalized[0].get("role") == "system" else []
    tail = normalized[1:] if system else normalized
    if system:
        system_budget = max_tokens - 600
        if self._count_tokens(system[0]["content"]) > system_budget:
            system = [{**system[0], "content": truncate_content(system[0]["content"], system_budget)}]
    system_tokens = sum(self._count_tokens(message["content"]) for message in system)
    available = max_tokens - system_tokens

    kept_reversed: list[ChatCompletionMessageParam] = []
    used = 0
    for message in reversed(tail):
        tokens = self._count_tokens(message["content"])
        if used + tokens <= available:
            kept_reversed.append(message)
            used += tokens
            continue
        remaining = available - used
        if remaining > 0:
            content = truncate_content(message["content"], remaining)
            if content:
                kept_reversed.append({**message, "content": content})
        break

    trimmed = system + list(reversed(kept_reversed))
    self._ui.debug_print(
        f"Chat history trimmed: {total_tokens} -> "
        f"{sum(self._count_tokens(message['content']) for message in trimmed)} tokens "
        f"(limit {max_tokens})"
    )
    return trimmed


_TRUNCATION_MARKER = "\n[... truncated]"


def _truncate_section_text(text: str, target_tokens: int, count_tokens) -> str:
    """Truncate *text* from the end so it really fits within *target_tokens*.

    Measured with the caller's tokenizer rather than a chars-per-token guess.
    The old fixed ratio of 4 assumed prose; source code runs closer to two
    characters per token, so a code-heavy section was left several times over
    its target — and source code is most of what Kvist puts in a prompt.

    Keeping a prefix keeps the section header, which is always the first line.
    """
    if not text or target_tokens <= 0:
        return ""
    if count_tokens(text) <= target_tokens:
        return text

    budget = target_tokens - count_tokens(_TRUNCATION_MARKER)
    if budget <= 0:
        return ""

    low, high = 0, len(text)
    while low < high:
        kept = (low + high + 1) // 2
        if count_tokens(text[:kept]) <= budget:
            low = kept
        else:
            high = kept - 1

    # Prefer a line boundary, but never at the cost of the header line.
    boundary = text.rfind("\n", 0, low)
    if boundary > text.find("\n"):
        low = boundary

    return text[:low] + _TRUNCATION_MARKER


def prefer_smart_model(self) -> bool:
    if not self._state.plan_info:
        return True
    if self._state.plan_info.complexity == "complex":
        return True
    if self._state.consecutive_errors > 0:
        return True
    if self._state.parse_errors > 0:
        return True
    if self._state.stagnation_count > 0:
        return True
    return False


def _supports_plain_json_fallback(error: Exception) -> bool:
    message = str(error).casefold()
    markers = (
        "response_format",
        "json_object",
        "unsupported",
        "not supported",
        "unknown parameter",
        "extra inputs are not permitted",
    )
    return any(marker in message for marker in markers)


# A prompt the provider refused as too large. Kvist sizes models from a static
# table plus name rules (context_budget.py) while the catalog itself is
# discovered live, so this is the signal that sizes a model Kvist guessed wrong.
_CONTEXT_OVERFLOW_MARKERS = (
    "context_length_exceeded",
    "maximum context length",
    "input tokens exceed",
    "prompt is too long",
    "reduce the length of the messages",
    "exceeds the context window",
    "context window exceeded",
)

# A rate limit is also "about tokens" and is phrased alarmingly close to an
# overflow, but it is transient and the cure is the opposite one: wait, do not
# shrink. Mistaking it for an overflow both cripples the model's budget for the
# rest of the session and skips the backoff sleep, hammering the provider.
_RATE_LIMIT_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "tokens per min",
    "quota",
    "retry after",
    "try again in",
)


def is_context_overflow_error(error: Exception) -> bool:
    """Whether the provider rejected the request for prompt size alone."""
    message = str(error).casefold()
    if any(marker in message for marker in _RATE_LIMIT_MARKERS):
        return False
    return any(marker in message for marker in _CONTEXT_OVERFLOW_MARKERS)


def _shrink_prompt_after_overflow(
    self,
    *,
    model: str,
    system_prompt: str,
    user_prompt: str,
    attempted_tokens: int,
) -> str:
    """Learn the model's real ceiling from its rejection and re-trim to it."""
    budget = note_input_overflow(model, attempted_tokens)
    self._ui.print_error(
        f"{model}: the provider refused a prompt of ~{attempted_tokens} tokens. "
        f"Retrying within {budget}; set AGENT_MAX_CONTEXT_TOKENS to pin a budget."
    )
    return self._trim_prompt_to_token_limit(system_prompt, user_prompt)


def _should_retry_model_error(error: Exception) -> bool:
    message = str(error).casefold()

    # Never retry these errors
    no_retry_markers = (
        # Non-transient configuration/model errors — retrying never helps.
        "no healthy backend available",
        "unsupported value",
        "does not support",
        "invalid api key",
        "authentication",
        "unauthorized",
        "does not exist",
        "not found",
        # An empty account stays empty between retries.
        "insufficient balance",
        "insufficient credit",
        "payment required",
        "error code: 402",
        # OmniRoute-specific non-retryable errors
        "bad_request",
        "invalid_request_error",
        "improperly formed",
        # Malformed API response — retrying won't fix empty choices or missing message
        "no choices",
        "malformed response",
        "missing message",
    )

    if any(marker in message for marker in no_retry_markers):
        return False

    # Check specific exception types
    if isinstance(error, (AuthenticationError, PermissionDeniedError, NotFoundError, BadRequestError)):
        return False

    # Retry transient errors
    return True


def sanitize_error_message(message: str) -> str:
    """Mask secrets (API keys, tokens, auth headers) from error messages."""
    return redact_text(message)


# ── safe response extraction ────────────────────────────────────────────────────

def safe_extract_completion_content(
    response: Any,
    provider: str | None = None,
    model: str | None = None,
) -> str:
    """Safely extract message content from a non-streaming chat completion response.

    Handles malformed API responses: empty choices list, missing message attribute,
    or empty content. Raises a descriptive RuntimeError instead of IndexError
    so the error message includes provider/model context.

    Returns:
        str: Message content string (may be empty string if the model returned
             empty content — callers should check with ``if not content``).

    Raises:
        RuntimeError: If choices is empty or the message is missing.
    """
    ctx_parts: list[str] = []
    if provider:
        ctx_parts.append(f"provider={provider}")
    if model:
        ctx_parts.append(f"model={model}")
    ctx = f" ({', '.join(ctx_parts)})" if ctx_parts else ""

    choices = getattr(response, "choices", None)
    if not choices:
        raise RuntimeError(
            f"Model returned an empty response — no choices in completion{ctx}"
        )

    choice = choices[0]
    message = getattr(choice, "message", None)
    if message is None:
        raise RuntimeError(
            f"Model returned a malformed response — choice is missing message{ctx}"
        )

    content = getattr(message, "content", None)
    if content is None:
        return ""
    return content


def safe_extract_stream_delta(chunk: Any) -> Any | None:
    """Safely extract delta from a streaming chunk.

    Returns the delta object if present, or None if the chunk has no choices
    (e.g. a final usage-only chunk).
    """
    choices = getattr(chunk, "choices", None)
    if not choices:
        return None
    return getattr(choices[0], "delta", None)


def safe_extract_reasoning_delta(delta: Any) -> str:
    """Extract a reasoning-token fragment from a stream delta, or "".

    Providers expose thinking on different fields: ``reasoning`` (OpenRouter)
    or ``reasoning_content`` (Moonshot/Kimi, DeepSeek). Both object and dict
    delta shapes are handled; a model that does not think yields "".
    """
    if delta is None:
        return ""
    for field in ("reasoning", "reasoning_content"):
        if isinstance(delta, dict):
            value = delta.get(field)
        else:
            value = getattr(delta, field, None)
        if isinstance(value, str) and value:
            return value
    return ""
