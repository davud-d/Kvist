# -*- coding: utf-8 -*-
from __future__ import annotations

"""Cross-provider reasoning-effort control.

Kvist exposes one global knob (``/effort``) on a fixed six-level scale.  Every
provider names and slices "how hard should the model think" differently, so this
module owns the translation from the Kvist scale to whatever the active provider
actually accepts, plus the human-readable advisory shown when a level does not
map cleanly.

The Kvist scale, coarsest first:

    false  — do not reason at all (or the provider's lowest possible setting)
    low
    medium
    high
    xhigh
    max    — reason as much as the provider allows

Only OpenAI-SDK-backed providers receive kwargs from here directly
(``reasoning_effort`` / ``extra_body``).  The Anthropic and FreeFlow wrapper
clients translate the neutral ``reasoning_effort`` marker themselves, because
they need to touch other request fields (temperature, ``max_tokens``) while
doing it.
"""

from typing import Any

from llm_clients import (
    ANTHROPIC_PROVIDER,
    DEEPSEEK_PROVIDER,
    FREEFLOW_PROVIDER,
    OMNIROUTE_PROVIDER,
    OPENAI_PROVIDER,
    OPENROUTER_PROVIDER,
    is_custom_provider,
    normalize_provider,
)

EFFORT_LEVELS: tuple[str, ...] = (
    "false",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)

DEFAULT_EFFORT = "medium"

EFFORT_DESCRIPTIONS: dict[str, str] = {
    "false": "No reasoning — fastest, cheapest, weakest on hard problems",
    "low": "Light reasoning — quick answers, shallow multi-step work",
    "medium": "Balanced reasoning — the default trade-off",
    "high": "Deep reasoning — slower, better on complex tasks",
    "xhigh": "Extended reasoning — noticeably slower, for the hardest tasks",
    "max": "Maximum reasoning the provider allows — slowest",
}


def normalize_effort(value: str | None) -> str:
    """Coerce ``value`` to a valid level, falling back to the default."""
    candidate = str(value or "").strip().casefold()
    if candidate in EFFORT_LEVELS:
        return candidate
    return DEFAULT_EFFORT


# ── Kvist level → provider-native value ──────────────────────────────────────

# OpenAI Chat Completions: none|minimal|low|medium|high|xhigh|max, subset per
# model. "none" is rejected by several models, so "false" maps to the always-safe
# "minimal"; OpenAI has no "max", so it folds into "xhigh".
_OPENAI_SCALE: dict[str, str] = {
    "false": "minimal",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "xhigh",
}

# OpenRouter's unified `reasoning.effort`: near 1:1 with the Kvist scale.
_OPENROUTER_SCALE: dict[str, str] = {
    "false": "none",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "xhigh",
    "max": "max",
}

# DeepSeek V4: only "high" and "max" are distinct; low/medium/xhigh are raised to
# "high" server-side. "false" is not an effort value — it disables thinking mode.
_DEEPSEEK_SCALE: dict[str, str] = {
    "low": "high",
    "medium": "high",
    "high": "high",
    "xhigh": "high",
    "max": "max",
}

# Groq (FreeFlow backend), gpt-oss models only. Qwen models reject everything but
# none/default and are caught by the runtime fallback.
_GROQ_SCALE: dict[str, str] = {
    "false": "low",
    "low": "low",
    "medium": "medium",
    "high": "high",
    "xhigh": "high",
    "max": "high",
}

# Anthropic extended-thinking token budgets. "false" omits thinking entirely.
_ANTHROPIC_BUDGET: dict[str, int] = {
    "low": 1024,
    "medium": 4096,
    "high": 8192,
    "xhigh": 16000,
    "max": 24000,
}


def _custom_protocol(provider: str) -> str:
    if not is_custom_provider(provider):
        return ""
    from llm_clients import extract_custom_provider_name
    from user_config import get_custom_provider_by_name

    info = get_custom_provider_by_name(extract_custom_provider_name(provider))
    return str((info or {}).get("protocol", "openai")).strip().casefold()


def uses_anthropic_protocol(provider: str) -> bool:
    """Whether the provider's client is the Anthropic-compatible wrapper."""
    normalized = normalize_provider(provider)
    if normalized == ANTHROPIC_PROVIDER:
        return True
    return _custom_protocol(normalized) == "anthropic"


def anthropic_thinking_config(level: str) -> dict[str, Any] | None:
    """Anthropic ``thinking`` block for ``level``, or ``None`` to omit it.

    ``false`` asks for the minimum, never for none. With thinking off, Anthropic
    models write a tool call into the visible text instead of emitting it as a
    call: the turn succeeds, nothing runs, and no error is raised — which in
    Kvist's action loop is the response the parser throws away, billing the user
    for content twice. The lowest budget costs less than one discarded step.
    """
    normalized = normalize_effort(level)
    if normalized == "false":
        normalized = "low"
    return {"type": "enabled", "budget_tokens": _ANTHROPIC_BUDGET[normalized]}


def reasoning_request_kwargs(provider: str, level: str) -> dict[str, Any]:
    """OpenAI-style ``create()`` kwargs that request reasoning effort ``level``.

    Returns ``{}`` when nothing needs to be added.  The Anthropic and FreeFlow
    wrapper clients read the neutral ``reasoning_effort`` marker and translate it
    on their own, so for those providers this returns the Kvist level unchanged
    (FreeFlow) or the pre-mapped Groq value.
    """
    normalized_level = normalize_effort(level)
    normalized_provider = normalize_provider(provider)

    if uses_anthropic_protocol(normalized_provider):
        # Consumed by _AnthropicCompletionsNamespace; kept out of the OpenAI
        # namespace so it can never collide with a real Anthropic field.
        return {"reasoning_effort": normalized_level}

    if normalized_provider == FREEFLOW_PROVIDER:
        return {"reasoning_effort": _GROQ_SCALE[normalized_level]}

    if normalized_provider == OPENROUTER_PROVIDER:
        return {
            "extra_body": {
                "reasoning": {"effort": _OPENROUTER_SCALE[normalized_level]}
            }
        }

    if normalized_provider == DEEPSEEK_PROVIDER:
        if normalized_level == "false":
            return {"extra_body": {"thinking": {"type": "disabled"}}}
        return {"reasoning_effort": _DEEPSEEK_SCALE[normalized_level]}

    # openai, omniroute, and custom OpenAI-protocol providers.
    if normalized_provider in (OPENAI_PROVIDER, OMNIROUTE_PROVIDER) or is_custom_provider(
        normalized_provider
    ):
        return {"reasoning_effort": _OPENAI_SCALE[normalized_level]}

    # Unknown provider: best effort, OpenAI-style.
    return {"reasoning_effort": _OPENAI_SCALE[normalized_level]}


def merge_reasoning_kwargs(
    request_kwargs: dict[str, Any], provider: str, level: str
) -> None:
    """Merge reasoning kwargs into ``request_kwargs`` in place.

    ``extra_body`` is merged key-wise so an existing entry is not dropped.
    """
    extra = reasoning_request_kwargs(provider, level)
    for key, value in extra.items():
        if key == "extra_body" and isinstance(value, dict):
            body = request_kwargs.setdefault("extra_body", {})
            if isinstance(body, dict):
                body.update(value)
            else:
                request_kwargs["extra_body"] = dict(value)
        else:
            request_kwargs[key] = value


_REASONING_KWARG_KEYS: tuple[str, ...] = ("reasoning_effort", "thinking")


def strip_reasoning_kwargs(request_kwargs: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of ``request_kwargs`` with every reasoning control removed."""
    cleaned = {
        key: value
        for key, value in request_kwargs.items()
        if key not in _REASONING_KWARG_KEYS
    }
    extra_body = cleaned.get("extra_body")
    if isinstance(extra_body, dict):
        pruned = {
            key: value
            for key, value in extra_body.items()
            if key not in ("reasoning", "thinking")
        }
        if pruned:
            cleaned["extra_body"] = pruned
        else:
            cleaned.pop("extra_body", None)
    return cleaned


_REASONING_ERROR_MARKERS: tuple[str, ...] = (
    "reasoning_effort",
    "reasoning effort",
    "reasoning.effort",
    "reasoning_tokens",
    "thinking",
    "budget_tokens",
    "extended thinking",
)


def is_reasoning_error(error: Exception) -> bool:
    """Whether ``error`` looks like the provider rejecting a reasoning control."""
    text = str(error).casefold()
    if not any(marker in text for marker in _REASONING_ERROR_MARKERS):
        return False
    # A bare mention inside a long unrelated message is unlikely; require it to
    # co-occur with a rejection verb so a stray token does not trigger a retry.
    return any(
        verb in text
        for verb in (
            "unsupported",
            "not supported",
            "does not support",
            "invalid",
            "unknown",
            "unexpected",
            "not permitted",
            "cannot be used",
            "must be",
            "no longer",
            "unrecognized",
            "400",
        )
    )


# ── advisories ──────────────────────────────────────────────────────────────


def effort_advisory(provider: str, level: str) -> str | None:
    """A one-line warning about how ``level`` behaves on ``provider``.

    ``None`` when the level maps cleanly and needs no caveat.
    """
    normalized_level = normalize_effort(level)
    normalized_provider = normalize_provider(provider)

    if is_custom_provider(normalized_provider):
        return (
            "Custom provider: reasoning-effort support is not guaranteed. "
            "If the endpoint rejects it, Kvist retries the request without it "
            "and warns each time."
        )

    if normalized_provider == FREEFLOW_PROVIDER:
        return (
            "FreeFlow: Gemini models ignore reasoning effort entirely. For Groq "
            "models it applies only to gpt-oss; Qwen rejects it (Kvist then "
            "retries without it)."
        )

    if uses_anthropic_protocol(normalized_provider):
        if normalized_level == "false":
            return (
                "Anthropic: 'false' is raised to the lowest thinking level, not "
                "off. With thinking off these models tend to write an action as "
                "plain text instead of emitting it, which costs a discarded step."
            )
        return (
            f"Anthropic: '{normalized_level}' sets an extended-thinking token "
            "budget; temperature is not applied while thinking is on."
        )

    if normalized_provider == DEEPSEEK_PROVIDER:
        if normalized_level == "false":
            return "DeepSeek: 'false' turns thinking mode off."
        if normalized_level == "max":
            return "DeepSeek: 'max' is the deepest level."
        return (
            f"DeepSeek: '{normalized_level}' is raised to 'high' server-side — "
            "only 'false', 'high' and 'max' are actually distinct."
        )

    if normalized_provider == OPENROUTER_PROVIDER:
        return None

    # OpenAI / OmniRoute.
    if normalized_level == "max":
        return "OpenAI: no 'max' level — 'xhigh' is used instead."
    if normalized_level == "false":
        return (
            "OpenAI: 'false' maps to 'minimal' (a few models reject a true "
            "'none'; Kvist retries without the parameter if so)."
        )
    return None
