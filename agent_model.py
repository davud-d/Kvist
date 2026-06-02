# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import time
from hashlib import sha256
from pathlib import Path
from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from context_utils import trim_history
from llm_clients import fallback_candidates_for_model
from render_throttle import ThrottledRenderer

try:
    import tiktoken
except ImportError:
    tiktoken = None  # type: ignore[assignment]

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"

_PROMPT_CACHE: dict[str, str] = {}

_ROLE_PROMPT_FILES: dict[str, str] = {
    "coder": "coder_prompt.txt",
    "devops": "devops_prompt.txt",
    "chat": "chat_prompt.txt",
}


def _load_role_prompt(role: str) -> str:
    """Load and cache system prompt for the given role."""
    if role in _PROMPT_CACHE:
        return _PROMPT_CACHE[role]
    filename = _ROLE_PROMPT_FILES.get(role, "coder_prompt.txt")
    text = (_PROMPT_DIR / filename).read_text(encoding="utf-8").strip()
    _PROMPT_CACHE[role] = text
    return text


# Backward-compatible alias: loads coder prompt lazily.
def _get_system_prompt() -> str:
    return _load_role_prompt("coder")


# Keep module-level name for any external imports; evaluated lazily.
class _LazyPrompt:
    """Lazy proxy so SYSTEM_PROMPT resolves on first access, not import time."""
    _value: str | None = None
    def __str__(self) -> str:
        if self._value is None:
            self._value = _get_system_prompt()
        return self._value


SYSTEM_PROMPT: str | _LazyPrompt = _LazyPrompt()


def call_action_model(self, prompt: str) -> str:
    preferred_model = str(getattr(self._state, "selected_action_model", "")).strip()
    if not preferred_model:
        preferred_model = self._select_action_model_for_run()
    role = getattr(self._state, "current_agent_role", "coder")
    system_prompt = _load_role_prompt(role)
    return self._call_model(
        model=preferred_model,
        system_prompt=system_prompt,
        user_prompt=prompt,
    )


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


def call_model(self, model: str, system_prompt: str, user_prompt: str) -> str:
    trimmed_prompt = self._trim_prompt_to_token_limit(system_prompt, user_prompt)
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": trimmed_prompt},
    ]
    messages = trim_history(messages, max_recent=4)

    # ── context metrics (computed once) ──────────────────────────
    _prompt_chars = len(system_prompt) + len(trimmed_prompt)
    _input_tokens = self._count_tokens(system_prompt) + self._count_tokens(trimmed_prompt)
    _estimated_tokens = _prompt_chars // 4
    _history_size_kb = sys.getsizeof(trimmed_prompt) / 1024.0

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
                    content = response.choices[0].message.content
                total_duration = time.monotonic() - request_start
                if not content:
                    raise RuntimeError("Model returned an empty response")
                if self.config.cache_enabled:
                    self._cache[cache_key] = content
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
                if self.config.api_request_interval > 0:
                    time.sleep(self.config.api_request_interval)

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
                    if any(m in error_msg_lowered for m in ("rate limit", "too many requests", "429", "quota", "high demand", "exhausted", "503")):
                        delay = max(delay, 15.0 * attempt)
                    self._ui.debug_print(f"API error. Retrying in {delay:.1f}s")
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

    raise RuntimeError(f"Failed to get model response: {last_error}")


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
    chunk_count = 0
    total_chunk_bytes = 0
    renderer = ThrottledRenderer(max_fps=10)
    for chunk in stream:
        delta = chunk.choices[0].delta if chunk.choices else None
        if delta and delta.content:
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
    request_kwargs = {
        "model": model,
        "messages": messages,
        "temperature": self.config.model_temperature,
        "response_format": {"type": "json_object"},
    }
    if stream:
        request_kwargs["stream"] = True

    first_error: Exception | None = None
    try:
        return self.client.chat.completions.create(**request_kwargs)
    except Exception as error:
        if not _supports_plain_json_fallback(error):
            raise
        first_error = error

    fallback_kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
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
    if self._encoding is None:
        return len(text) // 4
    return len(self._encoding.encode(text, disallowed_special=()))


def trim_prompt_to_token_limit(self, system_prompt: str, user_prompt: str) -> str:
    # Hard-limit for free APIs (11000 tokens) leaves room for the model's response
    max_tokens = 11000 if self.config.provider == "freeflow" else self.config.max_context_tokens
    
    system_tokens = self._count_tokens(system_prompt)
    prompt_tokens = self._count_tokens(user_prompt)

    # If system and user fit - return as is
    if system_tokens + prompt_tokens <= max_tokens:
        return user_prompt

    # Calculate the remainder. If the system prompt took everything, give the user a hard minimum of 600 tokens,
    # so that the request flies even if we slightly exceed the limit (retry will work).
    available = max_tokens - system_tokens
    if available < 600:
        available = 600

    ratio = available / prompt_tokens
    target_chars = int(len(user_prompt) * ratio * 0.95)
    
    head_chars = max(400, target_chars // 2)
    tail_chars = max(400, target_chars - head_chars)
    
    if head_chars + tail_chars >= len(user_prompt):
        trimmed = user_prompt[:target_chars]
    else:
        trimmed = (
            user_prompt[:head_chars]
            + "\n\n[...PROMPT MIDDLE TRUNCATED TO FIT PROVIDER LIMITS...]\n\n"
            + user_prompt[-tail_chars:]
        )
    
    msg = "\n\n[CONTEXT TRUNCATED]"
    
    self._ui.debug_print(f"Prompt trimmed: sys={system_tokens}, user={prompt_tokens} -> available {available} (limit {max_tokens})")
    return trimmed + msg


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
    )
    return not any(marker in message for marker in no_retry_markers)