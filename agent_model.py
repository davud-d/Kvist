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
    estimate_section_tokens,
    split_prompt_into_sections,
)
from context_utils import trim_history
from llm_clients import (
    fallback_candidates_for_model,
    is_custom_provider,
    extract_custom_provider_name,
    provider_supports_json_mode,
    OPENAI_PROVIDER,
    ANTHROPIC_PROVIDER,
    OPENROUTER_PROVIDER,
)
from redaction import redact_text, redact_value
from render_throttle import ThrottledRenderer

try:
    import tiktoken
except ImportError:
    tiktoken = None  # type: ignore[assignment]

_TOKEN_ENCODING_UNINITIALIZED = object()
_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

# ── Safety: detect stale prompts/prompts/ duplicate ──────────────────────
_STALE_PROMPT_DIR = _PROMPT_DIR / "prompts"
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
}

# Per-provider API request interval (seconds).
# Top-tier providers get 2.5 s; all others get the default from config (5.0 s).
# Override with AGENT_API_INTERVAL env var for all providers.
_API_PROVIDER_INTERVALS: dict[str, float] = {
    OPENAI_PROVIDER: 2.5,
    ANTHROPIC_PROVIDER: 2.5,
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
    provider = getattr(self.config, "provider", "")
    return _API_PROVIDER_INTERVALS.get(provider, self.config.api_request_interval)


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
    role = getattr(self._state, "current_agent_role", "coder")
    system_prompt = _load_role_prompt(role)
    self._retries_exhausted = False
    try:
        return self._call_model(
            model=preferred_model,
            system_prompt=system_prompt,
            user_prompt=prompt,
            usage_kind="step",
        )
    except Exception:
        self._retries_exhausted = True
        raise


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
    """
    callback = getattr(getattr(self, "_ui", None), "print_usage", None)
    if callback is None:
        return
    callback(
        {
            "input_tokens": max(0, int(input_tokens)),
            "output_tokens": max(0, int(output_tokens)),
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
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()

        if self.config.cache_enabled and cache_key in self._cache:
            return self._cache[cache_key]

        for attempt in range(1, self.config.max_retries + 1):
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
                
                # interval API request
                interval = _get_api_interval(self)
                if interval > 0:
                    time.sleep(interval)

                return content
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
    thought_shown = False
    stream_start = time.monotonic()
    first_token_time: float | None = None
    reasoning_start_time: float | None = None
    reasoning_end_time: float | None = None
    chunk_count = 0
    total_chunk_bytes = 0
    renderer = ThrottledRenderer(max_fps=10)
    for chunk in stream:
        delta = safe_extract_stream_delta(chunk)
        reasoning_text = safe_extract_reasoning_delta(delta)
        if reasoning_text and reasoning_start_time is None:
            reasoning_start_time = time.monotonic()
        if delta and delta.content:
            # The first answer token ends the thinking phase.
            if reasoning_start_time is not None and reasoning_end_time is None:
                reasoning_end_time = time.monotonic()
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
    # flush any remaining buffered output
    if self.config.verbose and not thought_shown:
        leftover = renderer.flush()
        if leftover and '"thought"' in leftover and len(leftover) > 80:
            print(f"  [streaming] {leftover[:120]}...", flush=True)
    stream_end = time.monotonic()
    stream_duration = stream_end - stream_start

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


def _create_json_completion(
    self,
    model: str,
    messages: list[ChatCompletionMessageParam],
    stream: bool = False,
):
    safe_messages = redact_value(messages)
    assert isinstance(safe_messages, list)
    request_kwargs: dict[str, Any] = {
        "model": model,
        "messages": safe_messages,
        "temperature": self.config.model_temperature,
        "timeout": self.config.api_timeout,
    }
    if provider_supports_json_mode(self.config.provider):
        request_kwargs["response_format"] = {"type": "json_object"}
    if stream:
        request_kwargs["stream"] = True
        request_kwargs["stream_options"] = {"include_usage": True}

    first_error: Exception | None = None
    try:
        return self.client.chat.completions.create(**request_kwargs)
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
    max_tokens = 11000 if self.config.provider == "freeflow" else self.config.max_context_tokens
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
            truncated = _truncate_section_text(text, target_tok)
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
            truncated = _truncate_section_text(text, target_tok)
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
            truncated = _truncate_section_text(text, target_tok)
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
            truncated = _truncate_section_text(text, target_tok)
            sections[i] = (header, truncated, priority)
            section_tokens[i] = self._count_tokens(truncated)

    trimmed = rebuild(sections, section_tokens)
    if not trimmed:
        trimmed = user_prompt[:max(400, available * 4)]

    trimmed += "\n\n[CONTEXT TRIMMED BY PRIORITY]"

    kept_count = sum(1 for t in section_tokens if t > 0)
    self._ui.debug_print(
        f"Prompt trimmed: sys={system_tokens}, user={prompt_tokens} "
        f"-> {sum(section_tokens)} tokens in {kept_count}/{len(sections)} sections "
        f"(limit {max_tokens}, available {available})"
    )
    return trimmed


def _truncate_section_text(text: str, target_tokens: int, chars_per_token: int = 4) -> str:
    """Truncate *text* from the end so it fits within ~*target_tokens*.

    The header line (everything up to the first newline) is always preserved.
    Subsequent lines are kept until the estimated token budget is exhausted,
    then a truncation marker is appended.
    """
    if not text:
        return text

    target_chars = target_tokens * chars_per_token

    # If the token target would allow more chars than we actually have,
    # force a meaningful reduction (at least 10 %, at most keep 2/3).
    if target_chars >= len(text):
        target_chars = max(len(text) // 10, len(text) * 2 // 3)

    if len(text) <= target_chars:
        return text

    lines = text.split("\n")
    # Always keep the header (first line).
    if len(lines) <= 1:
        return text[:target_chars] + "\n[... truncated]"

    header = lines[0]
    body_lines = lines[1:]

    result_lines = [header]
    used = len(header)

    for line in body_lines:
        if used + len(line) + 1 > target_chars:
            remaining = len(body_lines) - body_lines.index(line)
            if remaining > 0:
                result_lines.append(f"[... {remaining} more lines truncated]")
            break
        result_lines.append(line)
        used += len(line) + 1

    return "\n".join(result_lines)


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
