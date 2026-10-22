# -*- coding: utf-8 -*-
"""Per-model input limits, and the prompt budget one call may occupy.

Kvist used to enforce one flat 120k budget for every provider. That number was
below what any current model accepts, so the section trimmer
(``agent_model.trim_prompt_to_token_limit``) fired on almost every step of a
non-trivial run: file contents were dropped, and the action model re-read the
same files a few steps later because it no longer had them.

The table holds **max input tokens**, not the advertised context window, because
the two differ per vendor and only the input side bounds a prompt:

* OpenAI reserves max-output inside the window, so a 400k-window GPT-5 model
  rejects any prompt over 272k ("Input tokens exceed the configured limit").
* Anthropic bounds the input alone; on Claude 4.5+ a request whose input plus
  ``max_tokens`` overflows is accepted and the *reply* is cut short instead.

Values checked against vendor docs in September 2026. A static table cannot be
the whole answer, though: Kvist discovers model ids live from ``/v1/models`` on
every launch (``llm_clients.fetch_available_models``) and neither OpenAI nor
Anthropic report a context size there, so an id newer than this file is normal
rather than exceptional. Three things cover that: the ``-mini``/``-nano`` rule,
the family fallbacks, and ``note_input_overflow`` — a guess that turns out too
high is corrected from the provider's own rejection instead of failing the run.
"""
from __future__ import annotations

from kernel.config.env_utils import read_int_env

# Input limit assumed for a model no rule below recognises at all.
DEFAULT_INPUT_LIMIT = 256_000

# FreeFlow is neither a window nor a model limit but a hard per-request input
# limit on the free Gemini/Groq backends.
FREEFLOW_PROMPT_BUDGET = 11_000

# Every modern OpenAI tier reserves 128k of max output inside its window.
_OPENAI_OUTPUT_RESERVE = 128_000

# The small-window tier of a modern OpenAI family: a 400k window, so 272k input.
_OPENAI_SMALL_TIER_INPUT = 400_000 - _OPENAI_OUTPUT_RESERVE

# Matched as a prefix of the normalized model id, longest prefix first, after
# any vendor prefix ("anthropic/claude-…") is stripped.
MODEL_INPUT_LIMITS: dict[str, int] = {
    # OpenAI — window minus the max output it reserves inside that window.
    # developers.openai.com/api/docs/models. The -mini/-nano tiers are handled
    # by a rule, not by entries, so a future "gpt-6-astra-mini" cannot inherit
    # its full-size sibling's limit.
    "gpt-6.1-sol": 1_050_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-6-astra": 1_050_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-5.6-sol": 1_050_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-5.6-terra": 1_050_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-5.6-luna": 1_050_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-5.5": 1_000_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-5.4-pro": 1_050_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-5.4": 1_050_000 - _OPENAI_OUTPUT_RESERVE,
    "gpt-5.3": 1_000_000 - _OPENAI_OUTPUT_RESERVE,
    # Trailing hyphen on purpose. A bare "gpt-5" prefix also matches
    # "gpt-5.6-luna" and every future gpt-5.x, sizing a 1M model at 272k and
    # keeping the family fallback from ever running for that line. Being short
    # is silent and permanent; being generous is corrected by the first
    # rejection (note_input_overflow), so the fallback is the safer landing.
    "gpt-5-": _OPENAI_SMALL_TIER_INPUT,
    # Legacy OpenAI, reachable through a custom endpoint. Listed so the modern
    # family fallback below cannot hand them a limit they never had.
    "gpt-4.1": 1_000_000,
    "gpt-4o": 112_000,
    "gpt-4": 112_000,
    # Anthropic — the input alone must fit the window, so the window *is* the
    # input limit. 1M is the default on these: no beta header, standard pricing.
    # platform.claude.com/docs/en/build-with-claude/context-windows
    "claude-fable-5-1": 1_000_000,
    "claude-fable-5": 1_000_000,
    "claude-mythos-5-1": 1_000_000,
    "claude-mythos-5": 1_000_000,
    "claude-opus-5": 1_000_000,
    "claude-opus-4-8": 1_000_000,
    "claude-opus-4-7": 1_000_000,
    "claude-opus-4-6": 1_000_000,
    "claude-sonnet-5": 1_000_000,
    "claude-sonnet-4-6": 1_000_000,
    "claude-sonnet-4-5": 200_000,
    "claude-haiku": 200_000,
    # DeepSeek — api-docs.deepseek.com/quick_start/pricing lists the 1M context
    # and the 384k max output as separate limits, like Anthropic.
    "deepseek-v4": 1_000_000,
    "deepseek-flash": 1_000_000,
    "deepseek-chat": 128_000,
    "deepseek-reasoner": 128_000,
    # Google (reachable through OpenRouter / a custom endpoint)
    "gemini-3": 1_000_000,
    "gemini-2.5": 1_000_000,
    # Open models commonly served by gateways
    "llama-4": 1_000_000,
    "llama-3.3": 131_072,
    "llama-3.1": 131_072,
    "qwen3": 131_072,
    "gpt-oss": 131_072,
    "kimi-k2": 256_000,
}

# Families whose unlisted members are newer than this file rather than unknown.
# Discovery returns ids only, so a new flagship arrives with no size attached;
# collapsing it to DEFAULT_INPUT_LIMIT would quietly cut the budget of every
# model released after this table was written.
_MODERN_OPENAI_FAMILIES = ("gpt-5", "gpt-6", "gpt-7")
_MODERN_OPENAI_FAMILY_INPUT = 1_000_000 - _OPENAI_OUTPUT_RESERVE

# Kvist measures prompts with tiktoken, which under-counts for the Anthropic and
# DeepSeek tokenizers (most on code). This margin absorbs that error, and on the
# vendors that bill output from the same window it also leaves room for the
# reply and its thinking tokens.
_SAFETY_FRACTION = 0.85

# Below this the trimmer has nothing useful to keep, so never derive less.
_MIN_PROMPT_BUDGET = 8_000

# Budgets a provider has actually rejected this session, keyed by model id.
# Session-local on purpose: it corrects one process's guess, and persisting it
# would outlive the model change that caused it.
_LEARNED_BUDGETS: dict[str, int] = {}

# How much of a rejected prompt to keep when retrying it. The rejection proves
# the limit is lower than we thought but never says by how much, and Kvist's own
# count is an estimate, so the step back is deliberately large.
_OVERFLOW_BACKOFF = 0.6


def _normalize_model(model: str) -> str:
    normalized = (model or "").strip().lower()
    if "/" in normalized:
        normalized = normalized.rsplit("/", 1)[1]
    return normalized


def _is_small_openai_tier(normalized: str) -> bool:
    """A -mini/-nano variant of a modern OpenAI family.

    Checked before the table so "gpt-5.5-mini" cannot prefix-match "gpt-5.5"
    and inherit a limit four times its own.
    """
    if not normalized.startswith(_MODERN_OPENAI_FAMILIES):
        return False
    return "-mini" in normalized or "-nano" in normalized


def _family_fallback(normalized: str) -> int:
    """Limit for a model id no table entry matches."""
    if normalized.startswith(_MODERN_OPENAI_FAMILIES):
        return _MODERN_OPENAI_FAMILY_INPUT
    if normalized.startswith("claude-"):
        # Haiku is the only current 200k tier; every other line is 1M.
        return 200_000 if "haiku" in normalized else 1_000_000
    if normalized.startswith("deepseek"):
        return 1_000_000
    if normalized.startswith("gemini-"):
        return 1_000_000
    return DEFAULT_INPUT_LIMIT


def input_limit_for(model: str) -> int:
    """Documented max input tokens for *model*."""
    normalized = _normalize_model(model)
    if _is_small_openai_tier(normalized):
        return _OPENAI_SMALL_TIER_INPUT
    for prefix in sorted(MODEL_INPUT_LIMITS, key=len, reverse=True):
        if normalized.startswith(prefix):
            return MODEL_INPUT_LIMITS[prefix]
    return _family_fallback(normalized)


def env_prompt_budget() -> int:
    """``AGENT_MAX_CONTEXT_TOKENS``, or 0 when it is unset.

    The escape hatch for a model no rule here sizes correctly, or one whose
    limit the user has raised on their own gateway.
    """
    return read_int_env(
        "AGENT_MAX_CONTEXT_TOKENS",
        default=0,
        min_value=0,
        max_value=2_000_000,
    )


def derived_prompt_budget(provider: str, model: str) -> int:
    """Tokens one request's prompt may occupy on *model* per the docs.

    What a call actually uses is ``agent_model.context_token_limit``, which
    applies the overrides and any budget learned from a rejection.
    """
    if provider == "freeflow":
        return FREEFLOW_PROMPT_BUDGET

    return max(_MIN_PROMPT_BUDGET, int(input_limit_for(model) * _SAFETY_FRACTION))


def note_input_overflow(model: str, attempted_tokens: int) -> int:
    """Record that *model* rejected a prompt of *attempted_tokens*.

    Returns the budget to use for it from here on. This is what makes a
    generous guess safe for a model discovered at launch: the provider's own
    rejection sizes the model, and the step is retried instead of failing.
    """
    key = _normalize_model(model)
    # Always strictly below what was refused. _MIN_PROMPT_BUDGET is a floor for
    # the *derived* guess; applying it here would answer a rejected 5k prompt
    # with an 8k budget and the retry would resend something even bigger.
    reduced = max(1, min(int(attempted_tokens * _OVERFLOW_BACKOFF), attempted_tokens - 1))
    previous = _LEARNED_BUDGETS.get(key)
    if previous is not None:
        reduced = min(reduced, previous)
    _LEARNED_BUDGETS[key] = reduced
    return reduced


def learned_budget_for(model: str) -> int | None:
    """The budget learned from a rejection this session, if there was one."""
    return _LEARNED_BUDGETS.get(_normalize_model(model))


def reset_learned_budgets() -> None:
    _LEARNED_BUDGETS.clear()
