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

import re
from typing import Any

from kernel.llm.llm_clients import (
    ANTHROPIC_PROVIDER,
    DEEPSEEK_PROVIDER,
    FREEFLOW_PROVIDER,
    KIMI_PROVIDER,
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
    "false": "Reasoning off where supported — less inference overhead",
    "low": "Light reasoning — a smaller reasoning budget",
    "medium": "Balanced reasoning — the default trade-off",
    "high": "Deep reasoning — more inference work for complex tasks",
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

# DeepSeek V4 accepts low/high/max.  Kvist's intermediate levels are folded to
# the nearest supported value; "false" is represented by disabling thinking.
_DEEPSEEK_SCALE: dict[str, str] = {
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "high",
    "max": "max",
}

# Kimi Code accepts low/high/max. Its K3.1 Pro models reject thinking-off,
# so Kvist's "false" means the cheapest supported thinking, not a model change.
_KIMI_SCALE: dict[str, str] = {
    "false": "low",
    "low": "low",
    "medium": "high",
    "high": "high",
    "xhigh": "max",
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

# Only the older Claude models still take the legacy ``thinking: {type:
# "enabled", budget_tokens}`` block: Claude 3.x and 4.0-4.5 (haiku 4.5 included),
# plus the opus/sonnet lines before 4.6. Every other Claude model, a name Kvist
# has never seen included, wants adaptive thinking plus ``output_config.effort``
# and rejects the legacy block. The list of what is old is closed and the list of
# what is new keeps growing, so the old one is the one spelled out here.
_CLAUDE_NAME_RE = re.compile(r"claude|fable|mythos", re.IGNORECASE)
_CLAUDE_3_FIRST_RE = re.compile(r"claude[-.]3(?!\d)", re.IGNORECASE)
# "<family>-<major>[-<minor>]"; a dated id ("sonnet-4-20250514") has no minor, the
# date is more than two digits.
_FAMILY_VERSION_RE = re.compile(
    r"(?:haiku|sonnet|opus)[-.](\d+)(?:[-.](\d{1,2})(?!\d))?", re.IGNORECASE
)
_LAST_LEGACY_MINOR = 5


def anthropic_uses_adaptive_thinking(model: str) -> bool:
    """Return whether an Anthropic model requires adaptive thinking syntax.

    Gateway prefixes (``cc/``, ``anthropic/``) and cloud prefixes
    (``us.anthropic.``) do not matter: the search is over the whole model id.
    A model that is not a Claude at all, reached through an Anthropic-compatible
    gateway, keeps the legacy block it was always sent.
    """
    model_id = str(model or "").strip().rsplit("/", 1)[-1]
    if not _CLAUDE_NAME_RE.search(model_id):
        return False
    if _CLAUDE_3_FIRST_RE.search(model_id):
        return False
    version = _FAMILY_VERSION_RE.search(model_id)
    if version is None:
        return True
    major = int(version.group(1))
    minor = int(version.group(2) or 0)
    return (major, minor) > (4, _LAST_LEGACY_MINOR)


THINKING_STYLE_KEY = "thinking_style"
THINKING_ADAPTIVE = "adaptive"
THINKING_LEGACY = "legacy"


def anthropic_request_config(
    model: str, level: str, style: str | None = None
) -> dict[str, Any]:
    """Build Anthropic thinking/effort fields for a model and Kvist level.

    New Claude models expose effort through ``output_config`` and only accept
    adaptive thinking. Older models continue to use a bounded enabled block.
    ``style`` overrides the guess made from the model name: the name list cannot
    know a model that did not exist yet, so a rejection of the guess is answered
    by trying the other form (see ``other_thinking_style``).
    """
    normalized = normalize_effort(level)
    adaptive = (
        anthropic_uses_adaptive_thinking(model)
        if style is None
        else style == THINKING_ADAPTIVE
    )
    if adaptive:
        # Modern Claude models do not expose a thinking-off effort. Treat the
        # Kvist ``false`` level as the lowest supported effort, matching the
        # legacy translation and avoiding a request that the API rejects.
        effort = "low" if normalized == "false" else normalized
        return {
            "thinking": {"type": "adaptive"},
            "output_config": {"effort": effort},
        }
    return {"thinking": anthropic_thinking_config(normalized)}


def other_thinking_style(model: str, tried: str | None = None) -> str:
    """The form of thinking that ``tried`` (or the name-based guess) is not."""
    if tried is None:
        tried = (
            THINKING_ADAPTIVE
            if anthropic_uses_adaptive_thinking(model)
            else THINKING_LEGACY
        )
    return THINKING_LEGACY if tried == THINKING_ADAPTIVE else THINKING_ADAPTIVE


def _custom_protocol(provider: str) -> str:
    if not is_custom_provider(provider):
        return ""
    from kernel.llm.llm_clients import extract_custom_provider_name
    from kernel.config.user_config import get_custom_provider_by_name

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

    if normalized_provider == "chatgpt":
        return {"reasoning_effort": "low" if normalized_level == "false" else normalized_level}

    if normalized_provider == KIMI_PROVIDER:
        return {"reasoning_effort": _KIMI_SCALE[normalized_level]}

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
        # DeepSeek's OpenAI-compatible Chat Completions endpoint expects the
        # thinking toggle alongside the effort control.  V4 defaults to
        # thinking today, but making it explicit keeps the request valid across
        # gateways and prevents ``reasoning_effort`` from being treated as an
        # unsupported standalone parameter.
        return {
            "reasoning_effort": _DEEPSEEK_SCALE[normalized_level],
            "extra_body": {"thinking": {"type": "enabled"}},
        }

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


_REASONING_KWARG_KEYS: tuple[str, ...] = ("reasoning_effort", "thinking", THINKING_STYLE_KEY)


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
    # Anthropic's modern effort control lives under output_config.  Remove
    # only its effort member on a fallback so unrelated output settings remain
    # intact for a provider that rejected just the reasoning control.
    output_config = cleaned.get("output_config")
    if isinstance(output_config, dict) and "effort" in output_config:
        output_config = dict(output_config)
        output_config.pop("effort", None)
        if output_config:
            cleaned["output_config"] = output_config
        else:
            cleaned.pop("output_config", None)
    return cleaned


_REASONING_ERROR_MARKERS: tuple[str, ...] = (
    "reasoning_effort",
    "reasoning effort",
    "reasoning.effort",
    "reasoning_tokens",
    "output_config.effort",
    "thinking",
    "budget_tokens",
    "extended thinking",
)


# Errors that name thinking but are about the thinking blocks of the replayed
# history (a bad signature, an edited block, a missing leading block), not about
# the reasoning control. Dropping the control does not repair the history; the
# request would silently run without thinking and the broken history would stay.
_THINKING_HISTORY_MARKERS: tuple[str, ...] = (
    "signature",
    "cannot be modified",
    "redacted_thinking",
    "prefix mismatch",
    "block_binding",
    "must start with a thinking block",
)


def is_reasoning_error(error: Exception) -> bool:
    """Whether ``error`` looks like the provider rejecting a reasoning control."""
    text = str(error).casefold()
    # DeepSeek answers a forced tool_choice in thinking mode with "Thinking mode
    # does not support this tool_choice": it names thinking but rejects the tool
    # parameter, and dropping the effort would not fix it.
    if "tool_choice" in text:
        return False
    if any(marker in text for marker in _THINKING_HISTORY_MARKERS):
        return False
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

    if normalized_provider == "chatgpt":
        return ("ChatGPT: false uses low reasoning / ChatGPT: false использует low."
                if normalized_level == "false" else None)

    if normalized_provider == KIMI_PROVIDER:
        mapped = _KIMI_SCALE[normalized_level]
        if mapped != normalized_level:
            return f"Kimi Code: '{normalized_level}' is sent as '{mapped}'."
        return None

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
            f"Anthropic: '{normalized_level}' sets the model's reasoning effort. "
            "Modern Claude uses adaptive thinking; older models use a thinking "
            "token budget, and temperature is omitted while it is active."
        )

    if normalized_provider == DEEPSEEK_PROVIDER:
        if normalized_level == "false":
            return "DeepSeek: 'false' turns thinking mode off."
        if _DEEPSEEK_SCALE[normalized_level] == normalized_level:
            return None
        return (
            f"DeepSeek: '{normalized_level}' is sent as "
            f"'{_DEEPSEEK_SCALE[normalized_level]}' — the API supports 'low', 'high' and 'max'."
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
