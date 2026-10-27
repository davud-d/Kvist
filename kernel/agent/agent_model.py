# -*- coding: utf-8 -*-
from __future__ import annotations

import contextlib
import json
import os
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

from kernel.agent.agent_context import (
    SECTION_PRIORITIES,
    split_prompt_into_sections,
)
from kernel.context.context_meter import begin_call, context_usage, end_call, mute_generation, note_generated
from kernel.agent.narrator import narrating
from kernel.context.context_budget import (
    derived_prompt_budget,
    env_prompt_budget,
    learned_budget_for,
    note_input_overflow,
)
from kernel.context.context_utils import trim_history
from kernel.llm.llm_clients import (
    fallback_candidates_for_model,
    FREEFLOW_PROVIDER,
    is_custom_provider,
    extract_custom_provider_name,
    provider_supports_json_mode,
    OPENAI_PROVIDER,
    ANTHROPIC_PROVIDER,
    ANYMODEL_PROVIDER,
    DEEPSEEK_PROVIDER,
    CHATGPT_PROVIDER,
    OPENROUTER_PROVIDER,
    MANUAL_MODEL_PROVIDERS,
)
from kernel.plan import end_session
from kernel.agent import image_generation
from kernel.plan import plan_mode
from kernel.agent.agent_state import RunCancelled
from kernel.agent.cancellable_call import call_cancellable, iterate_cancellable
from kernel.protocol.parser import speakable_prefix
from kernel.agent.native_tools import (
    NativeToolsRejected,
    NativeTurn,
    ToolCall,
    compact_history,
    decode_arguments,
    native_role_prompt,
)
from kernel.llm.reasoning_effort import (
    is_reasoning_error,
    merge_reasoning_kwargs,
    normalize_effort,
    strip_reasoning_kwargs,
)
from kernel.context.system_context import workspace_instructions_context
from kernel.sandbox.redaction import redact_text, redact_value
from kernel.protocol.render_throttle import ThrottledRenderer

try:
    import tiktoken
except ImportError:
    tiktoken = None  # type: ignore[assignment]

_TOKEN_ENCODING_UNINITIALIZED = object()
_PROMPTS_ROOT = Path(__file__).resolve().parent.parent / "prompts"
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

# Not a role, so it can never collide with a _ROLE_PROMPT_FILES key.
_CONSTITUTION_KEY = "\x00constitution"
_PLAN_MODE_KEY = "\x00plan_mode"

_ROLE_PROMPT_FILES: dict[str, str] = {
    "coder": "coder_prompt.txt",
    "devops": "devops_prompt.txt",
    "reader": "reader_prompt.txt",
    "chat": "chat_prompt.txt",
    "universal": "universal_prompt.txt",
    "report": "report_prompt.txt",
    "commit": "commit_prompt.txt",
    "complete": "complete_prompt.txt",
    "complete_replace": "complete_replace_prompt.txt",
}

# Direct official-API providers (OpenAI, ChatGPT, Anthropic, DeepSeek) and
# AnyModel skip default request pacing. Other relay and aggregator providers
# (OpenRouter, FreeFlow, OmniRoute, custom endpoints) keep the paced defaults
# for rate-limit politeness.
_FAST_DIRECT_PROVIDERS: frozenset[str] = frozenset(
    {OPENAI_PROVIDER, CHATGPT_PROVIDER, ANTHROPIC_PROVIDER, DEEPSEEK_PROVIDER,
     ANYMODEL_PROVIDER}
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

    from kernel.config.user_config import get_saved_style

    return get_saved_style(style_type)


def _read_style_prompt(self, style_type: str) -> str:
    """Text of this session's style for that family, "" when none.

    The prompt for each style lives in its own file under
    `prompts/styles/<chat|agents>/<slug>.txt`; a missing or empty file means
    "no style instruction". The file is read per call rather than cached, so an
    edit to the selected file lands on the next step; which file is selected is
    fixed for the session.
    """
    from kernel.config.user_config import style_slug

    slug = style_slug(_session_style_name(self, style_type))
    path = _STYLES_DIR / style_type / f"{slug}.txt"
    try:
        return path.read_text(encoding="utf-8").strip()
    except (FileNotFoundError, NotADirectoryError):
        return ""


def _style_context(self, role: str) -> str:
    """Shared voice, this session's style, and its conversational accent.

    Read per call, like the style files. Chat inherits only the working
    style's voice section, never its instructions for carrying out work.
    Sits after the role prompt and before workspace instructions.
    """
    common = (_STYLES_DIR / "common.txt").read_text(encoding="utf-8").strip()
    prompt = _read_style_prompt(self, "chat" if role == "chat" else "agents")
    parts = [common, prompt]
    if role == "chat":
        working_style = _read_style_prompt(self, "agents")
        _, _, voice = working_style.partition("== HOW IT SOUNDS ==")
        voice = voice.split("\n== ", 1)[0].strip()
        if voice:
            parts.append(
                "== SELECTED STYLE'S CONVERSATION ACCENT ==\n\n"
                "This adds the selected working style's emphasis to conversation. "
                "It shapes explanation, not access or permission to act.\n\n"
                + voice
            )
    return "## RESPONSE STYLE\n\n" + "\n\n".join(part for part in parts if part)


def _load_plan_mode_section() -> str:
    """The PLAN MODE guidance shared by every role that changes files."""
    if _PLAN_MODE_KEY not in _PROMPT_CACHE:
        _PROMPT_CACHE[_PLAN_MODE_KEY] = (_PROMPT_DIR / "plan_mode.txt").read_text(encoding="utf-8").strip()
    return _PROMPT_CACHE[_PLAN_MODE_KEY]


_VOICE_MODE_KEY = "\x00voice_mode"
_NARRATOR_KEY = "\x00narrator"


def _load_voice_mode_section() -> str:
    """The VOICE MODE guidance for the roles whose answers are spoken."""
    if _VOICE_MODE_KEY not in _PROMPT_CACHE:
        _PROMPT_CACHE[_VOICE_MODE_KEY] = (_PROMPT_DIR / "voice_mode.txt").read_text(encoding="utf-8").strip()
    return _PROMPT_CACHE[_VOICE_MODE_KEY]


def load_narrator_prompt() -> str:
    """The narrator's own system prompt: it is not a role and never sees the role prompts."""
    if _NARRATOR_KEY not in _PROMPT_CACHE:
        _PROMPT_CACHE[_NARRATOR_KEY] = (_PROMPT_DIR / "narrator_prompt.txt").read_text(encoding="utf-8").strip()
    return _PROMPT_CACHE[_NARRATOR_KEY]


def _load_constitution() -> str:
    """The layer above every role: what the work is, what outranks what.

    Cached like a role prompt. The commit prompt, which `_load_role_prompt`
    serves directly, deliberately goes without it: it turns a diff into a
    commit message and never speaks to the person. The reporter does speak to
    them and gets it through `build_report_system_prompt`.
    """
    if _CONSTITUTION_KEY in _PROMPT_CACHE:
        return _PROMPT_CACHE[_CONSTITUTION_KEY]
    text = (_PROMPT_DIR / "constitution.txt").read_text(encoding="utf-8").strip()
    _PROMPT_CACHE[_CONSTITUTION_KEY] = text
    return text


def _load_native_channel_section() -> str:
    if _NATIVE_CHANNEL_KEY not in _PROMPT_CACHE:
        _PROMPT_CACHE[_NATIVE_CHANNEL_KEY] = (_PROMPT_DIR / "native_tools.txt").read_text(encoding="utf-8").strip()
    return _PROMPT_CACHE[_NATIVE_CHANNEL_KEY]


_NATIVE_CHANNEL_KEY = "__native_tools__"
_EXTENSIONS_KEY = "__extensions__"


def _load_extensions_section() -> str:
    """Skills, notes, past conversations, helpers, pause and questions: shared by the action roles."""
    if _EXTENSIONS_KEY not in _PROMPT_CACHE:
        _PROMPT_CACHE[_EXTENSIONS_KEY] = (_PROMPT_DIR / "extensions.txt").read_text(encoding="utf-8").strip()
    return _PROMPT_CACHE[_EXTENSIONS_KEY]


def build_role_system_prompt(
    self, role: str, *, native_tools: bool = False, offer_end_session: bool = True
) -> str:
    """The constitution, the role prompt, the user's response style, then this
    workspace's own instruction file — narrowing, each read per call so edits
    land on the next step.

    ``native_tools`` swaps the role prompt's text-mode protocol sections for the
    tool-call channel (``native_tools.native_role_prompt``). ``offer_end_session``
    false leaves the end_session block out even where the tool is available:
    chat's answer after the tool already acted in the turn.
    """
    role_prompt = _load_role_prompt(role)
    if offer_end_session and end_session.available(self, role):
        role_prompt = end_session.offer_in_prompt(role_prompt, role)
    if image_generation.available(self, role):
        role_prompt = image_generation.with_tool_in_prompt(role_prompt)
    if native_tools:
        role_prompt = native_role_prompt(role_prompt, _load_native_channel_section())
    from kernel.protocol.slash_commands import slash_commands_context
    base = f"{_load_constitution()}\n\n{role_prompt}\n\n{slash_commands_context()}"
    if plan_mode.offer_available(self) or self._state.plan_phase:
        base = f"{base}\n\n{_load_plan_mode_section()}"
    if role != "chat":
        base = f"{base}\n\n{_load_extensions_section()}"
        if self.config.voice_mode:
            base = f"{base}\n\n{_load_voice_mode_section()}"
    style = _style_context(self, role)
    if style:
        base = f"{base}\n\n{style}"
    from kernel.vision.vision import vision_instruction
    base = f"{base}\n\n{vision_instruction(self)}"
    project = workspace_instructions_context(self.executor.workspace, role)
    return f"{base}\n\n{project}" if project else base


def build_report_system_prompt(self) -> str:
    """The reporter's system prompt: the constitution, the report prompt, this
    session's working style, in build_role_system_prompt's order.

    The report is the person's account of the run, so it is written by the same
    character and in the same voice as the run: a fast-and-dirty prototype label
    has to survive into it. The workspace instruction file stays out, because
    the report prompt carries none of the rules that bound it.
    """
    from kernel.protocol.slash_commands import slash_commands_context
    base = f"{_load_constitution()}\n\n{_load_role_prompt('report')}\n\n{slash_commands_context()}"
    if self.config.voice_mode:
        base = f"{base}\n\n{_load_voice_mode_section()}"
    style = _style_context(self, "report")
    return f"{base}\n\n{style}" if style else base


def resolve_action_model(self) -> str:
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
    return preferred_model or self._select_action_model_for_run()


def call_action_model(self, prompt: str) -> str:
    preferred_model = resolve_action_model(self)
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
            json_mode=False,
        )
    except RunCancelled:
        # Not a model error: don't mark retries exhausted, just propagate.
        raise
    except Exception:
        self._retries_exhausted = True
        raise
    self._last_model_duration = time.monotonic() - started
    return content


# (provider, model) -> whether it answered the probe with a real tool call.
# Per process: a launch asks once, at its first task on that model.
_NATIVE_TOOL_SUPPORT: dict[tuple[str, str], bool] = {}

_PROBE_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": "ping",
        "description": "Confirm that the tool channel works.",
        "parameters": {
            "type": "object",
            "properties": {"ok": {"type": "boolean"}},
            "required": ["ok"],
            "additionalProperties": False,
        },
    },
}


def native_tools_supported(self, model: str) -> bool:
    """Whether this provider and model call tools natively, probed once per process.

    ``AGENT_NATIVE_TOOLS=0`` keeps every run on the text protocol (<tool_call> blocks).
    """
    if os.environ.get("AGENT_NATIVE_TOOLS", "").strip() == "0":
        return False
    key = (self.config.provider, model)
    if key in _NATIVE_TOOL_SUPPORT:
        return _NATIVE_TOOL_SUPPORT[key]
    verdict = _probe_native_tools(self, model)
    # Only the provider's own answer is remembered. A probe lost to a timeout
    # or the network says nothing about tool calling: this run uses the text
    # protocol and the next run asks again.
    if verdict is not None:
        _NATIVE_TOOL_SUPPORT[key] = verdict
    outcome = {True: "supported", False: "not supported", None: "probe failed, asking again next run"}[verdict]
    self._ui.debug_print(f"[native tools] {self.config.provider}/{model}: {outcome}")
    return bool(verdict)


def forget_native_tools(self, model: str) -> None:
    _NATIVE_TOOL_SUPPORT[(self.config.provider, model)] = False


def _probe_native_tools(self, model: str) -> bool | None:
    """One tiny request that must come back as a well-formed call of ``ping``.

    None when the request never got the provider's answer (a timeout, the
    network, a rate limit or an outage): no verdict, nothing to remember.

    ``tool_choice`` names ``ping`` explicitly so the probe measures whether the
    provider can emit a call at all, not whether a bare one-line prompt with no
    system context happens to make the model choose to. Left on the default
    ``auto``, a model that answers correctly with prose (a fair choice given how
    little the probe tells it) failed the probe and was wrongly written off as
    "no native tools" for the rest of the process, falling back to the more
    fragile text protocol for the whole run. A provider that rejects the
    ``tool_choice`` parameter itself (but still takes ``tools``) gets one retry
    without it, so it is not penalized for a parameter this probe does not
    need. A wrapper client that drops ``tools`` entirely still fails the same
    way.
    """
    request: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Call the ping tool with ok set to true."}],
        "tools": [_PROBE_TOOL],
        "tool_choice": {"type": "function", "function": {"name": "ping"}},
        "timeout": self.config.api_timeout,
    }
    merge_reasoning_kwargs(request, self.config.provider, self.config.reasoning_effort)
    try:
        response = _create_completion_with_reasoning_fallback(self, request)
    except Exception as error:
        if "tool_choice" not in str(error).casefold():
            return _failed_probe_verdict(self, error)
        request.pop("tool_choice", None)
        try:
            response = _create_completion_with_reasoning_fallback(self, request)
        except Exception as retry_error:
            return _failed_probe_verdict(self, retry_error)
    choices = getattr(response, "choices", None) or []
    message = getattr(choices[0], "message", None) if choices else None
    calls = getattr(message, "tool_calls", None) or []
    if not calls:
        return False
    function = getattr(calls[0], "function", None)
    if getattr(function, "name", "") != "ping":
        return False
    try:
        arguments = decode_arguments(getattr(function, "arguments", ""))
    except json.JSONDecodeError:
        return False
    return isinstance(arguments, dict)


def _failed_probe_verdict(self, error: Exception) -> bool | None:
    self._ui.debug_print(f"[native tools] probe request failed: {redact_text(str(error))}")
    return None if _is_transient_error(error) else False


_TRANSIENT_ERROR_TYPES = (
    APITimeoutError,
    APIConnectionError,
    RateLimitError,
    InternalServerError,
    TimeoutError,
    ConnectionError,
)
# Wrapper clients (Anthropic, FreeFlow) raise their own types; their text is
# all that tells a lost request from a provider's answer.
_TRANSIENT_ERROR_MARKERS = (
    "timed out", "timeout", "connection", "network", "temporarily unavailable",
    "rate limit", "too many requests", "429", "502", "503", "504",
)


def _is_transient_error(error: Exception) -> bool:
    """The request never got the provider's answer: nothing it says is a verdict."""
    if isinstance(error, _TRANSIENT_ERROR_TYPES):
        return True
    text = str(error).casefold()
    return any(marker in text for marker in _TRANSIENT_ERROR_MARKERS)


def is_tools_rejection(error: Exception) -> bool:
    """A provider error that refuses tool calling itself, not one request."""
    text = str(error).casefold()
    names_tools = "tool" in text or "function call" in text or "function_call" in text
    refuses = any(
        marker in text
        for marker in ("not support", "unsupported", "not allowed", "unrecognized", "unknown parameter", "not enabled")
    )
    return names_tools and refuses


def call_native_action_model(
    self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> NativeTurn:
    """One step of a native-tools run: the whole conversation in, one turn out."""
    model = resolve_action_model(self)
    self._retries_exhausted = False
    started = time.monotonic()
    self._last_reasoning_seconds = 0.0
    for attempt in range(1, self.config.max_retries + 1):
        compact_history(messages, self._count_tokens, context_token_limit(self))
        input_tokens = sum(self._count_tokens(json.dumps(message, ensure_ascii=False)) for message in messages)
        input_tokens += self._count_tokens(json.dumps(tools, ensure_ascii=False))
        begin_call(self, input_tokens)
        _throttle_request_rate(self)
        try:
            with narrating(self, model):
                turn = _request_native_turn(self, model, messages, tools)
        except RunCancelled:
            raise
        except Exception as error:
            if is_tools_rejection(error):
                raise NativeToolsRejected(redact_text(str(error))) from error
            if "temperature" in str(error).casefold() and _sends_temperature(self, model):
                # A reasoning model the id pattern does not know yet: learned
                # once, and the same turn is asked again without it.
                _TEMPERATURE_UNSUPPORTED.add((self.config.provider, model))
                continue
            if is_context_overflow_error(error):
                budget = note_input_overflow(model, input_tokens)
                self._ui.print_error(
                    f"{model}: провайдер не принял разговор на ~{input_tokens} токенов. "
                    f"Старые выводы инструментов сокращаются до {budget}."
                    if self._state.response_language == "ru"
                    else f"{model}: the provider refused a conversation of ~{input_tokens} tokens. "
                    f"Older tool output is being shortened to fit {budget}."
                )
                continue
            if not _should_retry_model_error(error) or attempt == self.config.max_retries:
                self._retries_exhausted = True
                raise
            time.sleep(self.config.retry_base_delay * (2 ** (attempt - 1)))
            continue
        self._last_model_duration = time.monotonic() - started
        output_text = turn.reasoning + turn.content + "".join(call.arguments for call in turn.tool_calls)
        _emit_usage(
            self,
            model=model,
            input_tokens=turn.input_tokens if turn.input_tokens is not None else input_tokens,
            output_tokens=turn.output_tokens if turn.output_tokens is not None else self._count_tokens(output_text),
            kind="step",
            duration_seconds=self._last_model_duration,
        )
        return turn
    self._retries_exhausted = True
    raise RuntimeError("Failed to get model response: the conversation does not fit the model")


def _request_native_turn(
    self, model: str, messages: list[dict[str, Any]], tools: list[dict[str, Any]]
) -> NativeTurn:
    request: dict[str, Any] = {
        "model": model,
        "messages": redact_value(messages),
        "tools": tools,
        "timeout": self.config.api_timeout,
    }
    if _sends_temperature(self, model):
        request["temperature"] = self.config.model_temperature
    merge_reasoning_kwargs(request, self.config.provider, self.config.reasoning_effort)
    key = (self.config.provider, model)
    if not self.config.stream or key in _STREAM_LOSES_ARGUMENTS:
        return _request_whole_turn(self, request)
    streamed = {**request, "stream": True, "stream_options": {"include_usage": True}}
    # The thinking clock starts with the request: a provider may hold the
    # connection silently for the whole time the model thinks.
    started = time.monotonic()
    _note_thinking(self, "")
    response = call_cancellable(
        self._cancel_event, lambda: _create_completion_with_reasoning_fallback(self, streamed)
    )
    try:
        return _native_turn_from_stream(self, response, started)
    except StreamedArgumentsLost:
        # Learned once per process: the same gateway route would lose them again.
        _STREAM_LOSES_ARGUMENTS.add(key)
        self._ui.debug_print("The stream carried no arguments for a tool call; asking again without streaming")
        return _request_whole_turn(self, request)


def _request_whole_turn(self, request: dict[str, Any]) -> NativeTurn:
    started = time.monotonic()
    _note_thinking(self, "")
    response = call_cancellable(
        self._cancel_event, lambda: _create_completion_with_reasoning_fallback(self, request)
    )
    turn = _native_turn_from_message(response.choices[0].message, getattr(response, "usage", None))
    self._last_reasoning_seconds = _thinking_seconds(time.monotonic() - started, bool(turn.reasoning))
    return turn


# Reasoning models fix temperature at its default and refuse any other value
# ("Unsupported value: 'temperature' does not support 0.1"), and the native
# path had no way back from that refusal: every turn of the run failed.
_REASONING_MODEL_RE = re.compile(r"^(?:o\d+(?:-|$)|gpt-5|gpt-6|deepseek-reasoner)")


def is_reasoning_model(model: str) -> bool:
    """A model id from a family that reasons before answering (vendor prefix ignored)."""
    return bool(_REASONING_MODEL_RE.match(model.rsplit("/", 1)[-1].strip().casefold()))


def _sends_temperature(self, model: str) -> bool:
    if is_reasoning_model(model):
        return False
    return (self.config.provider, model) not in _TEMPERATURE_UNSUPPORTED


def _usage_token_count(usage: Any, key: str) -> int | None:
    value = usage.get(key) if isinstance(usage, dict) else getattr(usage, key, None)
    return value if type(value) is int and value >= 0 else None


def _native_turn_from_message(message: Any, usage: Any = None) -> NativeTurn:
    reasoning = getattr(message, "reasoning_content", None)
    calls = [
        ToolCall(call_id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
        for call in (getattr(message, "tool_calls", None) or [])
    ]
    return NativeTurn(
        content=getattr(message, "content", None) or "",
        reasoning=reasoning or safe_extract_reasoning_delta(message),
        echo_reasoning=isinstance(reasoning, str) and bool(reasoning),
        tool_calls=calls,
        input_tokens=_usage_token_count(usage, "prompt_tokens"),
        output_tokens=_usage_token_count(usage, "completion_tokens"),
    )


_STREAM_LOSES_ARGUMENTS: set[tuple[str, str]] = set()


class StreamedArgumentsLost(Exception):
    """A streamed call ended with no argument text at all.

    AnyModel's GPT routes stream parallel calls as bare headers (name, empty
    arguments) and drop the rest; the same request without streaming is whole.
    A real call always sends at least ``{}``.
    """


def _native_turn_from_stream(self, stream: Any, started: float) -> NativeTurn:
    """Collect a streamed turn; tool-call fragments arrive keyed by index.

    Thinking ends at the first output — text or a call — not at the end of the
    stream: the minutes spent writing a whole-file edit are not thinking.
    """
    content: list[str] = []
    after_calls: list[str] = []
    reasoning: list[str] = []
    echo_reasoning = False
    input_tokens: int | None = None
    output_tokens: int | None = None
    calls: dict[int, dict[str, str]] = {}
    first_output: float | None = None
    last_push = 0.0
    last_call_push = 0.0
    spoken = _SpokenStream(self)
    try:
        for chunk in iterate_cancellable(self._cancel_event, stream):
            if self._cancel_event.is_set():
                raise RunCancelled
            # The final usage-only chunk has no choices/delta. Capture totals
            # before skipping it, and never add reasoning on top of that total.
            usage = getattr(chunk, "usage", None)
            prompt_count = _usage_token_count(usage, "prompt_tokens")
            completion_count = _usage_token_count(usage, "completion_tokens")
            if prompt_count is not None:
                input_tokens = prompt_count
            if completion_count is not None:
                output_tokens = completion_count
            delta = safe_extract_stream_delta(chunk)
            if delta is None:
                continue
            fragment = safe_extract_reasoning_delta(delta)
            if fragment:
                echo_reasoning = echo_reasoning or bool(getattr(delta, "reasoning_content", None))
                append_reasoning_delta(reasoning, fragment)
                if first_output is None and time.monotonic() - last_push > 0.1:
                    _note_thinking(self, "".join(reasoning))
                    last_push = time.monotonic()
            pieces = getattr(delta, "tool_calls", None) or []
            if first_output is None and (getattr(delta, "content", None) or pieces):
                first_output = time.monotonic()
                _seal_thinking(self, reasoning)
            if getattr(delta, "content", None):
                (after_calls if calls else content).append(delta.content)
                if not calls:
                    spoken.feed("".join(content))
            for piece in pieces:
                _merge_tool_call_delta(calls, piece)
            if calls and _final_answer_words(calls):
                spoken.feed_words(_spoken_with_answer("".join(content), calls))
            elif calls and _writes_a_call(calls) and time.monotonic() - last_call_push > 0.5:
                _note_tool_call_progress(self, calls[max(calls)])
                last_call_push = time.monotonic()
            note_generated(
                self,
                lambda: "".join(reasoning)
                + "".join(content)
                + "".join(call["arguments"] for call in calls.values()),
            )
    finally:
        spoken.close(_spoken_with_answer("".join(content), calls))
        close = getattr(stream, "close", None)
        if close is not None:
            try:
                close()
            except Exception as error:
                self._ui.debug_print(f"Native stream close failed: {redact_text(str(error))}")
    if first_output is None:
        _seal_thinking(self, reasoning)
    self._last_reasoning_seconds = _thinking_seconds(
        (first_output or time.monotonic()) - started, bool(reasoning)
    )
    if after_calls:
        self._ui.debug_print(
            f"Dropped {len(''.join(after_calls))} characters the model wrote after its tool call began"
        )
    if any(not entry["arguments"] for entry in calls.values()):
        raise StreamedArgumentsLost
    return NativeTurn(
        content="".join(content),
        text_after_calls="".join(after_calls),
        reasoning="".join(reasoning),
        echo_reasoning=echo_reasoning,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        tool_calls=[
            ToolCall(call_id=entry["id"], name=entry["name"], arguments=entry["arguments"] or "{}")
            for _index, entry in sorted(calls.items())
        ],
    )


# Below this, a wait with no visible reasoning is ordinary latency, and
# reporting it would put a "thought for 1 s" card on every quick step.
_SILENT_THINKING_FLOOR = 3.0


def _thinking_seconds(elapsed: float, reasoning_shown: bool) -> float:
    """How long the model thought before its first output.

    With visible reasoning, all of it: some providers send the reasoning in one
    piece at the end. Without, only a wait long enough to be thinking at all.
    """
    if reasoning_shown or elapsed >= _SILENT_THINKING_FLOOR:
        return elapsed
    return 0.0


def _seal_thinking(self, reasoning: list[str]) -> None:
    if not reasoning:
        return
    _note_thinking(self, "".join(reasoning))
    _note_thinking_end(self)


_PATH_IN_ARGUMENTS_RE = re.compile(r'"path"\s*:\s*"([^"]*)"')
_MESSAGE_IN_ARGUMENTS_RE = re.compile(r'"message"\s*:\s*"((?:[^"\\]|\\.)*)')
# Calls that carry the person's words, not work: the words stream as an answer
# and a "Writing ..." status would only describe the same text twice.
_ANSWER_TOOLS = frozenset({"final_answer", "report_to_user", "refuse"})


def _writes_a_call(calls: dict[int, dict[str, str]]) -> bool:
    return calls[max(calls)]["name"] not in _ANSWER_TOOLS


def _final_answer_words(calls: dict[int, dict[str, str]]) -> str:
    """The part of a ``final_answer`` message that has arrived so far."""
    entry = calls[max(calls)]
    if entry["name"] != "final_answer":
        return ""
    found = _MESSAGE_IN_ARGUMENTS_RE.search(entry["arguments"])
    if found is None:
        return ""
    raw = found.group(1)
    # A chunk can end inside an escape (a lone backslash or half a \uXXXX).
    for cut in range(len(raw), max(len(raw) - 6, -1), -1):
        try:
            return json.loads(f'"{raw[:cut]}"')
        except json.JSONDecodeError:
            continue
    return ""


def _spoken_with_answer(content: str, calls: dict[int, dict[str, str]]) -> str:
    """Words said before the call, then the answer being written."""
    opening = speakable_prefix(content)
    answer = _final_answer_words(calls) if calls else ""
    return f"{opening}\n\n{answer}" if opening and answer else opening or answer


def _note_tool_call_progress(self, entry: dict[str, str]) -> None:
    """Show a call the model is still writing.

    Arguments stream like any other text, and a whole-file edit takes minutes
    of it with nothing else moving on screen — a run stopped as frozen while it
    was writing its first change.
    """
    target = _PATH_IN_ARGUMENTS_RE.search(entry["arguments"])
    subject = f"{entry['name']} → {target.group(1)}" if target else entry["name"]
    size = len(entry["arguments"])
    if self._state.response_language == "ru":
        message = f"Пишет {subject} · {size} символов"
    else:
        message = f"Writing {subject} · {size} characters"
    self._ui.update_live(
        user_message=message,
        status="writing a tool call",
        step=getattr(self, "_current_step", 0),
        progress=self._state.progress,
        plan_step="tool call",
    )


def _merge_tool_call_delta(calls: dict[int, dict[str, str]], piece: Any) -> None:
    index = getattr(piece, "index", None)
    index = len(calls) if index is None else index
    entry = calls.setdefault(index, {"id": f"call_{index}", "name": "", "arguments": ""})
    if getattr(piece, "id", None):
        entry["id"] = piece.id
    function = getattr(piece, "function", None)
    if function is None:
        return
    # Some providers repeat the name on every fragment; only arguments stream.
    if getattr(function, "name", None) and not entry["name"]:
        entry["name"] = function.name
    if getattr(function, "arguments", None):
        entry["arguments"] += function.arguments


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


# Call kinds whose prompt *is* the run's conversation context. A report
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
    ``getattr`` so UIs without a usage channel keep working. Native calls prefer
    provider usage totals, including reasoning; when unavailable, local token
    counts estimate the payload. Other call paths supply local counts.

    Two different quantities travel here and must not be confused. ``input_tokens``
    /``output_tokens`` are *spend*: every step resends the whole conversation, so
    a UI summing them across a run reaches hundreds of thousands of tokens while
    the conversation itself is a fraction of that. ``context_tokens`` is that
    conversation — the size of this one prompt. Both ``context_*`` values come
    from ``context_meter.context_usage`` and are always current, so a UI
    assigns them as they are.
    """
    normalized_input = max(0, int(input_tokens))
    if kind in _CONTEXT_USAGE_KINDS:
        end_call(self, normalized_input, max(0, int(output_tokens)))
    callback = getattr(getattr(self, "_ui", None), "print_usage", None)
    if callback is None:
        return
    context = context_usage(self)
    callback(
        {
            "input_tokens": normalized_input,
            "output_tokens": max(0, int(output_tokens)),
            "context_tokens": context.used,
            "context_limit": context.limit,
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
    json_mode: bool = True,
) -> str:
    """One model call. ``json_mode`` asks a provider that supports it for a
    JSON object; the action loop turns it off, since a v2 response is text
    with <tool_call> blocks, which JSON mode would make impossible."""
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
            if usage_kind in _CONTEXT_USAGE_KINDS:
                begin_call(self, _input_tokens)
            else:
                mute_generation(self)
            _throttle_request_rate(self)
            request_start = time.monotonic()
            try:
                with narrating(self, current_model) if usage_kind == "step" else contextlib.nullcontext():
                    if self.config.stream:
                        content = self._call_model_streaming(current_model, messages, json_mode)
                    else:
                        response = call_cancellable(
                            self._cancel_event,
                            lambda: self._create_json_completion(
                                model=current_model,
                                messages=messages,
                                json_mode=json_mode,
                            ),
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


def words_are_streamed(agent: Any) -> bool:
    """Whether a response's words reach the person while the response arrives."""
    ui_streams = bool(getattr(agent._ui, "streams_words", False))
    return ui_streams and not end_session.available(agent, agent._state.current_agent_role)


class _SpokenStream:
    """Shows the words of a streaming response to a UI that takes them live.

    Only the words are shown: ``speakable_prefix`` drops tool-call tags and a
    JSON-protocol object. Disabled where the response may still be replaced by
    the end_session tool, whose words never reach the person.
    """

    def __init__(self, agent: Any) -> None:
        self._ui = agent._ui
        self._enabled = words_are_streamed(agent)
        self._stack = contextlib.ExitStack()
        self._update: Any = None
        self._shown = ""
        self._last_push = 0.0

    def feed(self, content: str) -> None:
        self.feed_words(speakable_prefix(content))

    def feed_words(self, words: str) -> None:
        if not self._enabled or not words or words == self._shown:
            return
        now = time.monotonic()
        if self._update is None:
            self._update = self._stack.enter_context(self._ui.live_markdown())
        elif now - self._last_push < 0.1:
            return
        self._last_push = now
        self._shown = words
        self._update(words)

    def close(self, words: str) -> None:
        if self._update is not None and words != self._shown:
            self._update(words)
        self._stack.close()


def _note_thinking(self, text: str) -> None:
    """Forward streamed reasoning to a UI that shows a live thinking panel.

    Optional capability: UIs without ``set_thinking`` (headless, tests) are
    skipped, exactly like the ``print_usage`` usage channel.
    """
    narrator = getattr(self, "_narrator", None)
    if narrator is not None:
        narrator.note_reasoning(text)
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
    json_mode: bool = True,
) -> str:
    # A new call owns the thinking column: drop the previous step's reasoning
    # so a provider that streams none does not leave stale text on screen. The
    # clock starts with the request, since a provider may think silently.
    _note_thinking(self, "")
    stream_start = time.monotonic()
    stream = call_cancellable(
        self._cancel_event,
        lambda: self._create_json_completion(
            model=model,
            messages=messages,
            stream=True,
            json_mode=json_mode,
        ),
    )
    chunks: list[str] = []
    reasoning_chunks: list[str] = []
    last_reasoning_push = 0.0
    preview_shown = False
    first_token_time: float | None = None
    reasoning_start_time: float | None = None
    reasoning_end_time: float | None = None
    chunk_count = 0
    total_chunk_bytes = 0
    renderer = ThrottledRenderer(max_fps=10)
    spoken = _SpokenStream(self)
    try:
        for chunk in iterate_cancellable(self._cancel_event, stream):
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
                append_reasoning_delta(reasoning_chunks, reasoning_text)
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
                spoken.feed("".join(chunks))
                renderer.feed(delta.content)
                note_generated(self, lambda: "".join(reasoning_chunks) + "".join(chunks))
                if self.config.verbose and not preview_shown:
                    rendered = renderer.maybe_render()
                    if rendered and len(rendered) > 80:
                        preview_shown = True
                        print(f"  [streaming] {rendered[:120]}...", flush=True)
    finally:
        spoken.close(speakable_prefix("".join(chunks)))
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
    if self.config.verbose and not preview_shown:
        leftover = renderer.flush()
        if leftover and len(leftover) > 80:
            print(f"  [streaming] {leftover[:120]}...", flush=True)
    stream_end = time.monotonic()
    stream_duration = stream_end - stream_start

    # Reasoning that never yielded a content token was never sealed in the loop.
    if reasoning_chunks and reasoning_end_time is None:
        _note_thinking(self, "".join(reasoning_chunks))
        _note_thinking_end(self)

    # Thinking runs from the request to the first answer token; reasoning that
    # never yielded content still counts up to stream end.
    thinking_end = reasoning_end_time or first_token_time or stream_end
    self._last_reasoning_seconds = _thinking_seconds(
        thinking_end - stream_start, reasoning_start_time is not None
    )

    # store streaming metrics for the parent call_model to pick up
    self._last_ttft = (first_token_time - stream_start) if first_token_time is not None else None
    self._last_stream_end = stream_duration
    self._last_chunk_count = chunk_count
    self._last_avg_chunk_size = (total_chunk_bytes / chunk_count) if chunk_count > 0 else None
    self._last_chunks_per_second = (chunk_count / stream_duration) if stream_duration > 0 else None

    return "".join(chunks)


def _warn_reasoning_unsupported(self, error: Exception | None, key: tuple[str, str]) -> None:
    """Surface that reasoning effort was dropped, once per (provider, model) per run.

    The rejection itself is cached at process scope (``_REASONING_UNSUPPORTED``)
    so the request is not repeated, but a fresh run is a fresh chance to notice
    the warning — de-duplication instead lives on ``RunState``, which a new run
    replaces. ``error`` is ``None`` when the drop came from the cache rather
    than a fresh rejection this call.
    """
    warned = getattr(getattr(self, "_state", None), "warned_reasoning_unsupported", None)
    if warned is not None:
        if key in warned:
            if error is not None:
                self._ui.debug_print(f"[reasoning] dropped after: {redact_text(str(error))}")
            return
        warned.add(key)
    level = normalize_effort(self.config.reasoning_effort)
    message = (
        f"Provider rejected reasoning effort '{level}' — retrying this request "
        "without it. Pick a level the provider supports with /effort."
    )
    # Manual-tier endpoints are arbitrary OpenAI-compatible gateways.  They
    # often reject the optional reasoning parameter even when the underlying
    # model can reason; dropping it is capability negotiation, not a run error.
    # Keep the diagnostic in debug logs without painting the whole run red.
    printer = getattr(self._ui, "print_error", None)
    if self.config.provider not in MANUAL_MODEL_PROVIDERS and not is_custom_provider(
        self.config.provider
    ) and callable(printer):
        printer(message)
    if error is not None:
        self._ui.debug_print(f"[reasoning] dropped after: {redact_text(str(error))}")


# (provider, model) -> the provider has already rejected a reasoning-control
# kwarg once this process. This only stops resending the same rejected kwarg
# on every remaining step of a run; the warning's own de-dup is separate (see
# `_warn_reasoning_unsupported`) and resets every run.
_REASONING_UNSUPPORTED: set[tuple[str, str]] = set()


def _create_completion_with_reasoning_fallback(self, request_kwargs: dict[str, Any]):
    """Create a completion; on a reasoning-control rejection, warn and retry
    once without any reasoning kwargs. Other errors propagate unchanged."""
    stripped = strip_reasoning_kwargs(request_kwargs)
    key = (self.config.provider, str(request_kwargs.get("model", "")))
    if stripped != request_kwargs and key in _REASONING_UNSUPPORTED:
        _warn_reasoning_unsupported(self, None, key)
        return self.client.chat.completions.create(**stripped)
    try:
        return self.client.chat.completions.create(**request_kwargs)
    except Exception as error:
        if not is_reasoning_error(error):
            raise
        _REASONING_UNSUPPORTED.add(key)
        _warn_reasoning_unsupported(self, error, key)
        return self.client.chat.completions.create(**stripped)


def _warn_json_mode_dropped(self, error: Exception | None, key: tuple[str, str]) -> None:
    """Surface that native JSON mode was dropped, once per (provider, model) per run.

    Same contract as `_warn_reasoning_unsupported` and for the same reason: a
    run that quietly lost its format guarantee spends its early steps paying
    for answers the parser throws away — but once the run has noticed, saying
    it again on every remaining step is noise, not information.
    """
    warned = getattr(getattr(self, "_state", None), "warned_json_mode_unsupported", None)
    if warned is not None:
        if key in warned:
            if error is not None:
                self._ui.debug_print(f"[json_mode] dropped after: {redact_text(str(error))}")
            return
        warned.add(key)
    message = (
        f"Provider rejected JSON mode for '{self.config.provider}' — retrying "
        "without it. Responses are no longer format-constrained this run."
    )
    printer = getattr(self._ui, "print_error", None)
    if callable(printer):
        printer(message)
    if error is not None:
        self._ui.debug_print(f"[json_mode] dropped after: {redact_text(str(error))}")


# (provider, model) -> the provider has already rejected response_format
# once this process. This only stops resending the same rejected kwarg on
# every remaining step of a run; the warning's own de-dup is separate (see
# `_warn_json_mode_dropped`) and resets every run.
_JSON_MODE_UNSUPPORTED: set[tuple[str, str]] = set()

# (provider, model) -> the provider has already rejected a non-default
# temperature once this process (reasoning-tier models like o1/o3 fix it to 1
# and error on anything else). Silent, unlike the two caches above: losing
# temperature control changes nothing the model formats or the user parses,
# so there is nothing here worth nagging about every step.
_TEMPERATURE_UNSUPPORTED: set[tuple[str, str]] = set()


def _create_json_completion(
    self,
    model: str,
    messages: list[ChatCompletionMessageParam],
    stream: bool = False,
    json_mode: bool = True,
):
    safe_messages = redact_value(messages)
    assert isinstance(safe_messages, list)
    temperature_key = (self.config.provider, model)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": safe_messages,
        "timeout": self.config.api_timeout,
    }
    if temperature_key not in _TEMPERATURE_UNSUPPORTED:
        request_kwargs["temperature"] = self.config.model_temperature
    json_mode_key = (self.config.provider, model)
    wants_json_mode = json_mode and provider_supports_json_mode(self.config.provider)
    known_unsupported = wants_json_mode and json_mode_key in _JSON_MODE_UNSUPPORTED
    if wants_json_mode and not known_unsupported:
        request_kwargs["response_format"] = {"type": "json_object"}
    if stream:
        request_kwargs["stream"] = True
        request_kwargs["stream_options"] = {"include_usage": True}
    merge_reasoning_kwargs(
        request_kwargs, self.config.provider, self.config.reasoning_effort
    )

    first_error: Exception | None = None
    if not known_unsupported:
        try:
            return _create_completion_with_reasoning_fallback(self, request_kwargs)
        except Exception as error:
            if not _supports_plain_json_fallback(error):
                raise
            first_error = error

    # Losing JSON mode is what lets a reasoning model answer with prose the
    # parser then rejects, so the user pays for the same content twice. Drop
    # only the parameter that was refused before giving up the rest. A known
    # rejection from an earlier step skips straight here without repeating it
    # — but still falls through to the fallback tier below on a further
    # failure (e.g. a fixed-temperature model), same as a fresh rejection.
    if known_unsupported or "response_format" in request_kwargs:
        _JSON_MODE_UNSUPPORTED.add(json_mode_key)
        narrowed = {k: v for k, v in request_kwargs.items() if k != "response_format"}
        _warn_json_mode_dropped(self, first_error, json_mode_key)
        try:
            return _create_completion_with_reasoning_fallback(self, narrowed)
        except Exception as error:
            if not _supports_plain_json_fallback(error):
                raise
            first_error = error

    fallback_kwargs: dict[str, Any] = {
        "model": model,
        "messages": safe_messages,
    }
    # Omit temperature when the first error was temperature-related so that the
    # fallback request does not repeat the same failure, and remember it so the
    # next step does not send temperature at all.
    if first_error is not None and "temperature" in str(first_error).casefold():
        _TEMPERATURE_UNSUPPORTED.add(temperature_key)
    elif temperature_key not in _TEMPERATURE_UNSUPPORTED:
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
        # A sign-in that was refused or is missing stays that way between retries
        # (ChatGPTAuthError: with max_retries=10 the person waited minutes for it).
        "sign in again",
        "continue with chatgpt first",
        # A spent subscription allowance returns only when its window resets.
        "plan usage",
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


def append_reasoning_delta(chunks: list[str], text: str) -> None:
    """Add a reasoning fragment, keeping reasoning-summary parts apart.

    GPT reasoning summaries arrive one ``**Heading**`` part per delta with no
    separator, so a plain join fuses them into ``**A****B**``, which no
    markdown renderer can read. Adjacent bold markers never occur inside one
    part, so that boundary is where a part ends.
    """
    if chunks and chunks[-1].endswith("**") and text.startswith("**"):
        chunks.append("\n\n")
    chunks.append(text)
