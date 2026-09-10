# -*- coding: utf-8 -*-
"""
This module configures LLM clients and model lists for Kvist.

Model availability is discovered live from each provider's ``/v1/models``
endpoint on every launch (see :func:`fetch_available_models`). The static
lists below are only offline fallbacks used when discovery is unavailable.
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from typing import Any

from language_utils import choose_localized_text
from openai import OpenAI
from redaction import redact_text

# Provider names.
OPENAI_PROVIDER = "openai"
FREEFLOW_PROVIDER = "freeflow"
OMNIROUTE_PROVIDER = "omniroute"
OPENROUTER_PROVIDER = "openrouter"
ANTHROPIC_PROVIDER = "anthropic"
DEEPSEEK_PROVIDER = "deepseek"
SUPPORTED_LLM_PROVIDERS = (OPENAI_PROVIDER, ANTHROPIC_PROVIDER, DEEPSEEK_PROVIDER, FREEFLOW_PROVIDER, OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER)

# API key environment keys.
OPENAI_API_KEY_KEY = "openai_api_key"
GEMINI_API_KEY_KEY = "gemini_api_key"
GROQ_API_KEY_KEY = "groq_api_key"
OMNIROUTE_API_KEY_KEY = "omniroute_api_key"
OMNIROUTE_BASE_URL_KEY = "omniroute_base_url"
OMNIROUTE_DEFAULT_BASE_URL = "http://localhost:20128/v1"
OPENROUTER_API_KEY_KEY = "openrouter_api_key"
OPENROUTER_BASE_URL_KEY = "openrouter_base_url"
OPENROUTER_DEFAULT_BASE_URL = "https://openrouter.ai/api/v1"
ANTHROPIC_API_KEY_KEY = "anthropic_api_key"
ANTHROPIC_BASE_URL_KEY = "anthropic_base_url"
ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"
DEEPSEEK_API_KEY_KEY = "deepseek_api_key"
DEEPSEEK_BASE_URL_KEY = "deepseek_base_url"
DEEPSEEK_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
CUSTOM_PROVIDER_PREFIX = "custom:"
CUSTOM_PROVIDER_PLACEHOLDER = "custom"
MODEL_ROLES = ("fast", "smart", "planner")

# Default models per provider.
_MODEL_DEFAULTS: dict[str, dict[str, str]] = {
    OPENAI_PROVIDER: {
        # Use the latest stable GPT-5.4 mini across roles for better quality and lower cost.
        # See OpenAI chat completions model list for available GPT-5.4 variants.
        "fast": "gpt-5.4-mini",
        "smart": "gpt-5.4-mini",
        "planner": "gpt-5.4-mini",
    },
    FREEFLOW_PROVIDER: {
        # Default all roles to Gemini 3.5 Flash - Google's newest Flash model with long context.
        "fast": "gemini-3.5-flash",
        "smart": "gemini-3.5-flash",
        "planner": "gemini-3.5-flash",
    },
    ANTHROPIC_PROVIDER: {
        # Claude models — fast tier uses Haiku, smart/planner use Sonnet.
        "fast": "claude-haiku-4-5",
        "smart": "claude-sonnet-4-6",
        "planner": "claude-sonnet-4-6",
    },
    DEEPSEEK_PROVIDER: {
        # DeepSeek models — fast tier uses Flash, smart/planner use Pro.
        "fast": "deepseek-v4-flash",
        "smart": "deepseek-v4-pro",
        "planner": "deepseek-v4-pro",
    },
}

# Environment variable keys for overriding models per role.
_MODEL_ENV_KEYS = {
    "fast": "AGENT_FAST_MODEL",
    "smart": "AGENT_SMART_MODEL",
    "planner": "AGENT_PLANNER_MODEL",
}

# Model options presented to the user when selecting models.
_MODEL_OPTIONS: dict[str, list[dict[str, str]]] = {
    OPENAI_PROVIDER: [
        # Updated OpenAI models: prefer GPT-5.4 variants according to the official API model list.
        {
            "value": "gpt-5.4-nano",
            "label": "GPT-5.4 Nano",
            "description": "The fastest and lightest GPT-5.4 variant",
        },
        {
            "value": "gpt-5.4-mini",
            "label": "GPT-5.4 Mini",
            "description": "Fast GPT-5.4 for everyday tasks",
        },
        {
            "value": "gpt-5.4",
            "label": "GPT-5.4",
            "description": "Current powerful OpenAI model for complex tasks",
        },
        {
            "value": "gpt-5-mini",
            "label": "GPT-5 Mini (legacy)",
            "description": "Previous GPT-5 model; used as fallback",
        },
        {
            "value": "gpt-5-nano",
            "label": "GPT-5 Nano (legacy)",
            "description": "Legacy fast GPT-5 variant; used as fallback",
        },
    ],
    FREEFLOW_PROVIDER: [
        # FreeFlow/Groq/Gemini model options updated from official provider docs.
        {
            "value": "gemini-3.5-flash",
            "label": "Gemini 3.5 Flash",
            "description": "New Google Flash model with 1M context",
        },
        {
            "value": "gemini-3.1-pro-preview",
            "label": "Gemini 3.1 Pro Preview",
            "description": "Complex tasks: advanced reasoning",
        },
        {
            "value": "gemini-3.1-flash-lite",
            "label": "Gemini 3.1 Flash Lite",
            "description": "Lightweight and cost-effective Flash version",
        },
        {
            "value": "gemini-3-flash-preview",
            "label": "Gemini 3 Flash Preview",
            "description": "Previous Flash preview version",
        },
        {
            "value": "llama-3.3-70b-versatile",
            "label": "Llama 3.3 70B Versatile",
            "description": "Deep versatile Groq-hosted Meta model",
        },
        {
            "value": "llama-3.1-8b-instant",
            "label": "Llama 3.1 8B Instant",
            "description": "Fast Groq-hosted variant with low latency",
        },
        {
            "value": "openai/gpt-oss-120b",
            "label": "GPT-OSS 120B",
            "description": "Open source OpenAI model with large context",
        },
        {
            "value": "qwen/qwen3-32b",
            "label": "Qwen 3 32B",
            "description": "Groq-supported Qwen 3 model",
        },
        {
            "value": "deepseek-v4-flash",
            "label": "DeepSeek V4 Flash",
            "description": "New fast DeepSeek variant; successor to DeepSeek Chat",
        },
        {
            "value": "deepseek-v4-pro",
            "label": "DeepSeek V4 Pro",
            "description": "Advanced DeepSeek version; replaces DeepSeek Reasoner",
        },
    ],
    ANTHROPIC_PROVIDER: [
        # Anthropic Claude models — official model IDs.
        {
            "value": "claude-opus-4-8",
            "label": "Claude Opus 4.8",
            "description": "Most capable for complex reasoning and coding",
        },
        {
            "value": "claude-sonnet-4-6",
            "label": "Claude Sonnet 4.6",
            "description": "Balanced performance for everyday tasks",
        },
        {
            "value": "claude-haiku-4-5",
            "label": "Claude Haiku 4.5",
            "description": "Fastest Claude for lightweight tasks",
        },
    ],
    DEEPSEEK_PROVIDER: [
        # DeepSeek models — official model IDs on api.deepseek.com.
        {
            "value": "deepseek-v4-pro",
            "label": "DeepSeek V4 Pro",
            "description": "Advanced reasoning and coding tasks",
        },
        {
            "value": "deepseek-v4-flash",
            "label": "DeepSeek V4 Flash",
            "description": "Fast and cost-effective for everyday tasks",
        },
    ],
    OPENROUTER_PROVIDER: [
        # Popular models available through OpenRouter.
        {
            "value": "anthropic/claude-opus-4-8",
            "label": "Claude Opus 4.8",
            "description": "Most capable Claude model for complex reasoning",
        },
        {
            "value": "anthropic/claude-sonnet-4-6",
            "label": "Claude Sonnet 4.6",
            "description": "Balanced Claude model for everyday tasks",
        },
        {
            "value": "anthropic/claude-haiku-4-5",
            "label": "Claude Haiku 4.5",
            "description": "Fast and cost-effective Claude model",
        },
        {
            "value": "openai/gpt-5.4-mini",
            "label": "GPT-5.4 Mini",
            "description": "Fast GPT-5.4 for everyday tasks via OpenRouter",
        },
        {
            "value": "openai/gpt-5.4",
            "label": "GPT-5.4",
            "description": "Current powerful OpenAI model via OpenRouter",
        },
        {
            "value": "google/gemini-3.5-flash",
            "label": "Gemini 3.5 Flash",
            "description": "Google Flash model with 1M context via OpenRouter",
        },
        {
            "value": "google/gemini-3.5-pro",
            "label": "Gemini 3.5 Pro",
            "description": "Google Pro model for complex tasks via OpenRouter",
        },
        {
            "value": "deepseek/deepseek-v4-pro",
            "label": "DeepSeek V4 Pro",
            "description": "Advanced DeepSeek model via OpenRouter",
        },
        {
            "value": "meta-llama/llama-4-maverick",
            "label": "Llama 4 Maverick",
            "description": "Meta's latest open model via OpenRouter",
        },
    ],
}

# Fallback chains for each role within OpenAI. Try latest models first; fall back to legacy ones.
_OPENAI_ROLE_FALLBACKS: dict[str, tuple[str, ...]] = {
    "fast": ("gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-mini", "gpt-5-nano"),
    "smart": ("gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-mini", "gpt-5-nano"),
    "planner": ("gpt-5.4-mini", "gpt-5.4-nano", "gpt-5-mini", "gpt-5-nano"),
}

# Generic OpenAI fallback chain (used when role-specific fallbacks are unavailable).
_OPENAI_GENERIC_FALLBACKS: tuple[str, ...] = (
    "gpt-5.4-mini",
    "gpt-5.4-nano",
    "gpt-5-mini",
    "gpt-5-nano",
)

# Generic FreeFlow fallback chain. Order matters: try Gemini first, then Llama, then DeepSeek.
_FREEFLOW_GENERIC_FALLBACKS: tuple[str, ...] = (
    "gemini-3.5-flash",
    "gemini-3.1-pro-preview",
    "llama-3.3-70b-versatile",
    "deepseek-v4-flash",
)

# Set of models that are no longer supported by FreeFlow providers.
_DEPRECATED_FREEFLOW_MODELS: set[str] = {
    "mixtral-8x7b-32768",
    "llama-3.1-70b-versatile",
    "allam-2-7b",
    "deepseek-r1-distill-llama-70b",
    # Additional deprecated models based on official deprecation notices.
    "deepseek-chat",
    "deepseek-r1-distill-qwen-32b",
    "qwen-2.5-coder-32b",
    "llama-3.2-3b-preview",
}

# Aliases map common misspellings and legacy names to current model IDs.
_FREEFLOW_MODEL_ALIASES: dict[str, str] = {
    "gemini 2.5 flash": "gemini-3-flash-preview",
    "gemini-2.5 flash": "gemini-3-flash-preview",
    "gemini 3 flash": "gemini-3-flash-preview",
    "gemini-3 flash": "gemini-3-flash-preview",
    "gemini 3.1 pro": "gemini-3.1-pro-preview",
    "gemini 3.1 pro preview": "gemini-3.1-pro-preview",
    "gemini-3.1 pro": "gemini-3.1-pro-preview",
    "gemini-3.1 pro preview": "gemini-3.1-pro-preview",
    "mixtral-8x7b-32768": "llama-3.3-70b-versatile",
    "allam-2-7b": "llama-3.3-70b-versatile",
    "deepseek-r1-distill-llama-70b": "openai/gpt-oss-120b",
    # Map legacy DeepSeek and Qwen aliases to current model IDs.
    "deepseek chat": "deepseek-v4-flash",
    "deepseek-chat": "deepseek-v4-flash",
    "deepseek reasoner": "deepseek-v4-pro",
    "deepseek-reasoner": "deepseek-v4-pro",
    "qwen-3-32b": "qwen/qwen3-32b",
    "qwen 3 32b": "qwen/qwen3-32b",
    "qwen-2.5 coder 32b": "qwen/qwen3-32b",
    "gpt-oss-120b": "openai/gpt-oss-120b",
}

# Prefixes used to determine whether a model is a Groq backend. Namespaced prefixes allow
# for proper routing of OSS and Qwen models. Note: the tuple form allows startswith() checks.
_FREEFLOW_GROQ_PREFIXES: tuple[str, ...] = (
    "llama-",
    "mixtral-",
    "gemma",
    # Accept both bare and namespaced Qwen models.
    "qwen",
    "qwen/",
    # Accept both bare and namespaced OpenAI OSS models.
    "openai/",
    "deepseek",
    "moonshotai/",
)


class UnsupportedProviderFeatureError(RuntimeError):
    """Raised when a requested provider feature is unsupported by the backend."""

    pass


def normalize_provider(provider: str | None) -> str:
    """Return a normalized provider name or default to OpenAI."""
    candidate = str(provider or "").strip().lower()
    if candidate in SUPPORTED_LLM_PROVIDERS:
        return candidate
    # OmniRoute aliases
    if candidate in {"omni", "or", "9router"}:
        return OMNIROUTE_PROVIDER
    # OpenRouter aliases
    if candidate in {"openroute", "openrt", "router"}:
        return OPENROUTER_PROVIDER
    # Anthropic aliases
    if candidate in {"anthropic", "claude", "ant"}:
        return ANTHROPIC_PROVIDER
    # DeepSeek aliases
    if candidate in {"deepseek", "ds"}:
        return DEEPSEEK_PROVIDER
    # Custom provider: "custom" alone is a placeholder that triggers the wizard;
    # "custom:<name>" is a concrete provider and passes through as-is below.
    if candidate.startswith(CUSTOM_PROVIDER_PREFIX):
        return candidate
    if candidate == CUSTOM_PROVIDER_PLACEHOLDER:
        return CUSTOM_PROVIDER_PLACEHOLDER
    return OPENAI_PROVIDER


def provider_label(provider: str) -> str:
    """Return a human‑friendly label for a provider."""
    normalized = normalize_provider(provider)
    if normalized == FREEFLOW_PROVIDER:
        return "FreeFlow LLM"
    if normalized == OMNIROUTE_PROVIDER:
        return "OmniRoute"
    if normalized == OPENROUTER_PROVIDER:
        return "OpenRouter"
    if normalized == ANTHROPIC_PROVIDER:
        return "Anthropic"
    if normalized == DEEPSEEK_PROVIDER:
        return "DeepSeek"
    if is_custom_provider(normalized):
        name = extract_custom_provider_name(normalized)
        return f"Custom: {name}"
    return "OpenAI"


def is_custom_provider(provider: str) -> bool:
    """Return True if the provider is a user-defined custom provider."""
    return str(provider).startswith(CUSTOM_PROVIDER_PREFIX)


def extract_custom_provider_name(provider: str) -> str:
    """Extract the name part from a custom provider id (e.g. 'custom:my-llama' -> 'my-llama')."""
    if not is_custom_provider(provider):
        return ""
    return str(provider)[len(CUSTOM_PROVIDER_PREFIX):]


def normalize_model_name(provider: str, model: str) -> str:
    """Normalize a model name by applying aliases for FreeFlow models."""
    candidate = str(model or "").strip()
    if not candidate:
        return candidate
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OMNIROUTE_PROVIDER:
        # OmniRoute: only strip whitespace, preserve exact model id
        return candidate
    if normalized_provider == OPENROUTER_PROVIDER:
        # OpenRouter: only strip whitespace, preserve exact model id (provider/model format)
        return candidate
    if normalized_provider == ANTHROPIC_PROVIDER:
        # Anthropic: only strip whitespace, preserve exact model id
        return candidate
    if normalized_provider == DEEPSEEK_PROVIDER:
        # DeepSeek: only strip whitespace, preserve exact model id
        return candidate
    if is_custom_provider(normalized_provider):
        # Custom providers: only strip whitespace, preserve exact model id
        return candidate
    if normalized_provider != FREEFLOW_PROVIDER:
        return candidate
    return _FREEFLOW_MODEL_ALIASES.get(candidate.casefold(), candidate)


def infer_freeflow_backend(model: str) -> str | None:
    """Infer the backend (gemini or groq) for a given FreeFlow model."""
    candidate = normalize_model_name(FREEFLOW_PROVIDER, model).casefold()
    if candidate.startswith("gemini-"):
        return "gemini"
    if candidate.startswith(_FREEFLOW_GROQ_PREFIXES):
        return "groq"
    return None


def validate_omniroute_model_id(model: str) -> str | None:
    """Validate OmniRoute model id. Returns error message or None if valid."""
    candidate = str(model or "").strip()

    if not candidate:
        return "Model id cannot be empty"

    # 'auto' is explicitly allowed
    if candidate == "auto":
        return None

    # Check for forbidden characters
    if any(c in candidate for c in ("\n", "\r", "\t", '"', "'", "\\", "`")):
        return "Model id contains forbidden characters"

    # Check for URL patterns
    if candidate.startswith("http://") or candidate.startswith("https://"):
        return "Model id cannot be a URL"

    # Check slash-separated format validity. OmniRoute accepts multi-segment ids
    # like "groq/openai/gpt-oss-120b" (backend/vendor/model), not just provider/model.
    if "/" in candidate:
        parts = candidate.split("/")
        for part in parts:
            if not part:
                return "Model id cannot have empty path segments"
            if part.strip() != part:
                return "Model id segments cannot have leading/trailing whitespace"

    # Combo/router name without slash - just ensure no whitespace
    if " " in candidate:
        return "Model id cannot contain whitespace"

    return None


def is_valid_omniroute_model_id(model: str) -> bool:
    """Check if model id is valid for OmniRoute."""
    return validate_omniroute_model_id(model) is None


def is_model_supported_by_provider(provider: str, model: str) -> bool:
    """Check whether the provider supports the given model."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return True
    if normalized_provider == ANTHROPIC_PROVIDER:
        return True
    if normalized_provider == DEEPSEEK_PROVIDER:
        return True
    if normalized_provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER):
        return is_valid_omniroute_model_id(model)
    if is_custom_provider(normalized_provider):
        # Custom providers: accept any non-empty model id
        return bool(str(model).strip())
    return infer_freeflow_backend(model) is not None


def is_model_deprecated(provider: str, model: str) -> bool:
    """Return True if the given model is marked as deprecated for the provider."""
    normalized_provider = normalize_provider(provider)
    normalized_model = normalize_model_name(normalized_provider, model)
    if normalized_provider != FREEFLOW_PROVIDER:
        return False
    return normalized_model in _DEPRECATED_FREEFLOW_MODELS


def resolve_models_for_provider(
    provider: str,
    saved_models: dict[str, str] | None = None,
    language_hint: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Resolve configured models for each role, applying defaults and deprecation checks."""
    normalized_provider = normalize_provider(provider)

    # OmniRoute, OpenRouter and custom providers have no defaults - only saved models and env overrides
    if normalized_provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER) or is_custom_provider(normalized_provider):
        resolved: dict[str, str] = {}
        warnings: list[str] = []
        persisted = saved_models or {}

        for role, env_key in _MODEL_ENV_KEYS.items():
            configured = os.getenv(env_key, "").strip() or str(persisted.get(role, "")).strip()
            if configured:
                normalized_model = normalize_model_name(normalized_provider, configured)
                if is_model_supported_by_provider(normalized_provider, normalized_model):
                    resolved[role] = normalized_model
                else:
                    validation_error = validate_omniroute_model_id(normalized_model)
                    warnings.append(
                        choose_localized_text(
                            f"Модель '{configured}' для роли {role} невалидна: {validation_error}",
                            f"Model '{configured}' for role {role} is invalid: {validation_error}",
                            language_hint,
                        )
                    )
            # Note: no default fallback for missing roles - this will be caught at startup

        return resolved, warnings

    defaults = _MODEL_DEFAULTS[normalized_provider]
    resolved: dict[str, str] = {}
    warnings: list[str] = []
    persisted = saved_models or {}

    for role, env_key in _MODEL_ENV_KEYS.items():
        configured = os.getenv(env_key, "").strip() or str(persisted.get(role, "")).strip()
        fallback = defaults[role]
        if not configured:
            resolved[role] = fallback
            continue

        normalized_model = normalize_model_name(normalized_provider, configured)
        if is_model_deprecated(normalized_provider, normalized_model):
            warnings.append(
                choose_localized_text(
                    f"Модель '{configured}' больше не поддерживается провайдером {provider_label(normalized_provider)}. Использую '{fallback}' для роли {role}.",
                    f"Model '{configured}' is no longer supported by {provider_label(normalized_provider)}. Using '{fallback}' for role {role}.",
                    language_hint,
                )
            )
            resolved[role] = fallback
            continue
        if is_model_supported_by_provider(normalized_provider, normalized_model):
            resolved[role] = normalized_model
            continue

        warnings.append(
            choose_localized_text(
                f"Модель '{configured}' не поддерживается провайдером {provider_label(normalized_provider)}. Использую '{fallback}' для роли {role}.",
                f"Model '{configured}' is not supported by {provider_label(normalized_provider)}. Using '{fallback}' for role {role}.",
                language_hint,
            )
        )
        resolved[role] = fallback

    return resolved, warnings


# ── Live model discovery (/v1/models) ════════════════════════════════════════

# How long a /v1/models discovery result stays fresh within one process. Every
# fresh launch rediscovers; the cache only prevents duplicate hits when the
# same process asks for the catalog several times (e.g. /models wizards).
_MODEL_DISCOVERY_TTL_SECONDS = 300.0

# Gemini exposes an OpenAI-compatible layer, so both FreeFlow backends can be
# discovered with the same OpenAI client against their /models endpoints.
_GEMINI_OPENAI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
_GROQ_OPENAI_BASE_URL = "https://api.groq.com/openai/v1"

_MODEL_DISCOVERY_CACHE: dict[str, tuple[float, tuple[str, ...]]] = {}


def clear_model_discovery_cache() -> None:
    """Drop cached /v1/models discovery results (tests, key rotation)."""
    _MODEL_DISCOVERY_CACHE.clear()


def _extract_model_ids(data: Any) -> list[str]:
    """Collect non-empty ``id`` fields from a models.list() payload."""
    ids: list[str] = []
    for item in data or []:
        raw = getattr(item, "id", None)
        if raw is None and isinstance(item, dict):
            raw = item.get("id")
        candidate = str(raw or "").strip()
        if candidate:
            ids.append(candidate)
    return ids


def _list_models_via_openai(api_key: str, base_url: str | None) -> list[str]:
    client = _build_openai_client(api_key, base_url=base_url or None)
    return _extract_model_ids(client.models.list().data)


def _list_models_via_anthropic(api_key: str, base_url: str | None) -> list[str]:
    try:
        import anthropic  # type: ignore
    except ImportError as error:
        raise RuntimeError(
            "Anthropic SDK is not installed. Install dependency 'anthropic'."
        ) from error
    client = anthropic.Anthropic(
        api_key=api_key,
        base_url=(base_url or ANTHROPIC_DEFAULT_BASE_URL).strip(),
    )
    return _extract_model_ids(client.models.list(limit=1000).data)


def _discovery_endpoints(normalized_provider: str, credentials: dict[str, str]) -> list[dict[str, str]]:
    """Return the /models-capable endpoints to query for a provider.

    Each entry has ``kind`` (openai|anthropic), ``base_url``, ``api_key`` and
    ``label``. Endpoints without a usable API key are skipped.
    """
    endpoints: list[dict[str, str]] = []

    def add(kind: str, base_url: str | None, api_key: object, label: str) -> None:
        key = str(api_key or "").strip()
        if not key:
            return
        endpoints.append(
            {
                "kind": kind,
                "base_url": str(base_url or "").strip(),
                "api_key": key,
                "label": label,
            }
        )

    if normalized_provider == OPENAI_PROVIDER:
        add("openai", None, credentials.get(OPENAI_API_KEY_KEY), "OpenAI")
    elif normalized_provider == OMNIROUTE_PROVIDER:
        add(
            "openai",
            credentials.get(OMNIROUTE_BASE_URL_KEY) or OMNIROUTE_DEFAULT_BASE_URL,
            credentials.get(OMNIROUTE_API_KEY_KEY),
            "OmniRoute",
        )
    elif normalized_provider == OPENROUTER_PROVIDER:
        add(
            "openai",
            credentials.get(OPENROUTER_BASE_URL_KEY) or OPENROUTER_DEFAULT_BASE_URL,
            credentials.get(OPENROUTER_API_KEY_KEY),
            "OpenRouter",
        )
    elif normalized_provider == ANTHROPIC_PROVIDER:
        add(
            "anthropic",
            credentials.get(ANTHROPIC_BASE_URL_KEY) or ANTHROPIC_DEFAULT_BASE_URL,
            credentials.get(ANTHROPIC_API_KEY_KEY),
            "Anthropic",
        )
    elif normalized_provider == DEEPSEEK_PROVIDER:
        add(
            "openai",
            credentials.get(DEEPSEEK_BASE_URL_KEY) or DEEPSEEK_DEFAULT_BASE_URL,
            credentials.get(DEEPSEEK_API_KEY_KEY),
            "DeepSeek",
        )
    elif is_custom_provider(normalized_provider):
        from user_config import get_custom_provider_by_name
        info = get_custom_provider_by_name(extract_custom_provider_name(normalized_provider)) or {}
        protocol = str(info.get("protocol", "openai")).strip()
        add(
            protocol if protocol in ("openai", "anthropic") else "openai",
            info.get("base_url"),
            info.get("api_key"),
            provider_label(normalized_provider),
        )
    else:
        # FreeFlow: discover each configured backend separately and merge.
        add("openai", _GEMINI_OPENAI_BASE_URL, credentials.get(GEMINI_API_KEY_KEY), "Gemini")
        add("openai", _GROQ_OPENAI_BASE_URL, credentials.get(GROQ_API_KEY_KEY), "Groq")
    return endpoints


def fetch_available_models(
    provider: str,
    credentials: dict[str, str],
    force_refresh: bool = False,
) -> tuple[list[str], str | None]:
    """Ask the provider's /v1/models endpoint which models it currently serves.

    Returns ``(sorted_model_ids, error_message)`` where ``error_message`` is
    ``None`` on success (including an empty but reachable catalog). When every
    endpoint fails, returns an empty list plus a human-readable reason so the
    caller can degrade gracefully to configured/static models.
    """
    normalized_provider = normalize_provider(provider)
    endpoints = _discovery_endpoints(normalized_provider, credentials)
    if not endpoints:
        return [], None

    fingerprint = hashlib.sha1(
        "\n".join(
            f"{item['kind']}|{item['base_url']}|{item['api_key']}" for item in endpoints
        ).encode("utf-8")
    ).hexdigest()
    now = time.monotonic()
    cached = _MODEL_DISCOVERY_CACHE.get(fingerprint)
    if not force_refresh and cached is not None and now - cached[0] < _MODEL_DISCOVERY_TTL_SECONDS:
        return list(cached[1]), None

    collected: set[str] = set()
    errors: list[str] = []
    succeeded = False
    for endpoint in endpoints:
        try:
            if endpoint["kind"] == "anthropic":
                ids = _list_models_via_anthropic(endpoint["api_key"], endpoint["base_url"])
            else:
                ids = _list_models_via_openai(endpoint["api_key"], endpoint["base_url"])
            collected.update(ids)
            succeeded = True
        except Exception as error:
            errors.append(f"{endpoint['label']}: {redact_text(str(error))}")

    if succeeded:
        ordered = tuple(sorted(collected))
        _MODEL_DISCOVERY_CACHE[fingerprint] = (now, ordered)
        return list(ordered), None

    detail = "; ".join(errors) if errors else "unknown error"
    return [], (
        f"Could not fetch the model catalog from {provider_label(normalized_provider)} "
        f"({detail}). Using configured models."
    )


def resolve_accessible_models(
    provider: str,
    requested_models: dict[str, str],
    available_models: list[str] | set[str],
    language_hint: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Reconcile requested role models against the provider's live catalog.

    Models missing from the catalog fall back to the closest available entry
    of the provider fallback chain; if nothing matches, the request is kept
    unchanged with a warning. An empty catalog (discovery unavailable) leaves
    everything untouched.
    """
    normalized_provider = normalize_provider(provider)
    available = {str(item).strip() for item in available_models if str(item).strip()}
    resolved: dict[str, str] = {}
    warnings: list[str] = []
    if not available:
        return dict(requested_models), warnings

    label = provider_label(normalized_provider)
    for role, requested in requested_models.items():
        requested_model = normalize_model_name(normalized_provider, str(requested).strip())
        if requested_model in available:
            resolved[role] = requested_model
            continue
        candidates = [
            normalize_model_name(normalized_provider, item)
            for item in fallback_candidates_for_model(normalized_provider, requested_model, True)
        ]
        selected_model = next((item for item in candidates if item in available), None)
        if selected_model is not None:
            warnings.append(
                choose_localized_text(
                    f"Модель '{requested_model}' недоступна у провайдера {label}. Использую '{selected_model}' для роли {role}.",
                    f"Model '{requested_model}' is unavailable on {label}. Using '{selected_model}' for role {role}.",
                    language_hint,
                )
            )
        else:
            selected_model = requested_model
            warnings.append(
                choose_localized_text(
                    f"Модель '{requested_model}' не найдена в каталоге {label}. Оставляю её для роли {role}.",
                    f"Model '{requested_model}' was not found in the {label} catalog. Keeping it for role {role}.",
                    language_hint,
                )
            )
        resolved[role] = selected_model

    return resolved, warnings


def resolve_openai_accessible_models(
    api_key: str,
    requested_models: dict[str, str],
    language_hint: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Filter requested models down to those actually accessible for the given OpenAI API key."""
    client = OpenAI(api_key=api_key)
    available_models = _extract_model_ids(client.models.list().data)
    return resolve_accessible_models(
        OPENAI_PROVIDER,
        requested_models,
        available_models,
        language_hint=language_hint,
    )


def fallback_models_for_provider(provider: str) -> tuple[str, ...]:
    """Return the generic fallback chain for a provider."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return _OPENAI_GENERIC_FALLBACKS
    if normalized_provider == ANTHROPIC_PROVIDER:
        return ()
    if normalized_provider == DEEPSEEK_PROVIDER:
        return ()
    if normalized_provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER) or is_custom_provider(normalized_provider):
        # OmniRoute, OpenRouter and custom providers have no fallback chain - models are user-specified
        return ()
    return _FREEFLOW_GENERIC_FALLBACKS


def fallback_candidates_for_model(
    provider: str,
    model: str,
    allow_runtime_fallbacks: bool = False,
) -> tuple[str, ...]:
    """Return fallback candidates for a specific model, optionally including runtime fallbacks."""
    normalized_provider = normalize_provider(provider)
    normalized_model = normalize_model_name(normalized_provider, model)
    candidates: list[str] = []

    def add_candidate(value: str) -> None:
        candidate = normalize_model_name(normalized_provider, value)
        if candidate and candidate not in candidates:
            candidates.append(candidate)

    add_candidate(normalized_model)

    if not allow_runtime_fallbacks:
        return tuple(candidates)

    # OmniRoute, OpenRouter and custom providers: no runtime fallbacks - only the selected model
    if normalized_provider in (OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER) or is_custom_provider(normalized_provider):
        return tuple(candidates)

    provider_fallbacks = fallback_models_for_provider(normalized_provider)
    if normalized_provider == FREEFLOW_PROVIDER:
        required_backend = infer_freeflow_backend(normalized_model)
        for fallback in provider_fallbacks:
            fallback_backend = infer_freeflow_backend(fallback)
            if required_backend and fallback_backend != required_backend:
                continue
            add_candidate(fallback)
        return tuple(candidates)

    for fallback in provider_fallbacks:
        add_candidate(fallback)
    return tuple(candidates)


def provider_supports_streaming(provider: str) -> bool:
    """Return True if the provider supports streaming responses.

    Custom providers are unverified third-party endpoints — many implement plain
    chat completions correctly but break on ``stream=true``. Streaming is opt-in
    per provider via its saved ``streaming`` flag, defaulting to off.
    """
    normalized = normalize_provider(provider)
    if is_custom_provider(normalized):
        from user_config import get_custom_provider_by_name
        info = get_custom_provider_by_name(extract_custom_provider_name(normalized))
        return bool(info.get("streaming", False)) if info else False
    return normalized in (OPENAI_PROVIDER, OPENROUTER_PROVIDER, ANTHROPIC_PROVIDER, DEEPSEEK_PROVIDER)


def provider_supports_json_mode(provider: str) -> bool:
    """Return True if the provider supports native JSON mode (response_format)."""
    normalized = normalize_provider(provider)
    return normalized in (OPENAI_PROVIDER, OPENROUTER_PROVIDER, DEEPSEEK_PROVIDER)


def configured_freeflow_backends(credentials: dict[str, str]) -> tuple[str, ...]:
    """Determine which FreeFlow backends have keys configured."""
    backends: list[str] = []
    if str(credentials.get(GEMINI_API_KEY_KEY, "")).strip():
        backends.append("gemini")
    if str(credentials.get(GROQ_API_KEY_KEY, "")).strip():
        backends.append("groq")
    return tuple(backends)


def required_backends_for_models(models: dict[str, str] | list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return the set of backends required by the given models."""
    values: list[str]
    if isinstance(models, dict):
        values = [str(value).strip() for value in models.values()]
    else:
        values = [str(value).strip() for value in models]

    backends: list[str] = []
    for model in values:
        backend = infer_freeflow_backend(model)
        if backend and backend not in backends:
            backends.append(backend)
    return tuple(backends)


def validate_model_selection(
    provider: str,
    models: dict[str, str],
    credentials: dict[str, str],
    language_hint: str | None = None,
) -> list[str]:
    """Validate a set of selected models against the provider and configured backends."""
    normalized_provider = normalize_provider(provider)
    issues: list[str] = []

    for role in MODEL_ROLES:
        model = str(models.get(role, "")).strip()
        if not model:
            issues.append(
                choose_localized_text(
                    f"Для роли {role} не выбрана модель.",
                    f"No model is selected for role {role}.",
                    language_hint,
                )
            )
            continue
        if not is_model_supported_by_provider(normalized_provider, model):
            issues.append(
                choose_localized_text(
                    f"Модель '{model}' не поддерживается провайдером {provider_label(normalized_provider)} для роли {role}.",
                    f"Model '{model}' is not supported by {provider_label(normalized_provider)} for role {role}.",
                    language_hint,
                )
            )
            continue
        if is_model_deprecated(normalized_provider, model):
            issues.append(
                choose_localized_text(
                    f"Модель '{model}' больше не поддерживается провайдером {provider_label(normalized_provider)} для роли {role}.",
                    f"Model '{model}' is no longer supported by {provider_label(normalized_provider)} for role {role}.",
                    language_hint,
                )
            )

    if normalized_provider != FREEFLOW_PROVIDER:
        return issues

    configured_backends = set(configured_freeflow_backends(credentials))
    required_backends = required_backends_for_models(models)
    missing_backends = [backend for backend in required_backends if backend not in configured_backends]
    if missing_backends:
        missing_labels = ", ".join(item.capitalize() for item in missing_backends)
        issues.append(
            choose_localized_text(
                f"Для выбранных моделей не настроены backend: {missing_labels}. Добавьте нужные ключи или выберите модели другого backend.",
                f"Required backends are not configured for the selected models: {missing_labels}. Add the needed keys or choose models from another backend.",
                language_hint,
            )
        )

    return issues


def get_model_options(
    provider: str,
    available_models: list[str] | set[str] | None = None,
) -> list[dict[str, str]]:
    """Return selectable model options for the given provider.

    Pass ``available_models`` (from :func:`fetch_available_models`) to build
    the list from the provider's live /v1/models catalog; when it is empty or
    omitted, the static curated hints are used instead.
    """
    if available_models is not None:
        live_ids = sorted({str(item).strip() for item in available_models if str(item).strip()})
        if live_ids:
            return [
                {"value": item, "label": item, "description": ""}
                for item in live_ids
            ]
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OMNIROUTE_PROVIDER or is_custom_provider(normalized_provider):
        # OmniRoute and custom providers use manual input, no predefined options
        return []
    return [dict(item) for item in _MODEL_OPTIONS.get(normalized_provider, [])]


def required_secret_keys_for_provider(provider: str, models: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return which secret keys must be configured for the given provider and models."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return (OPENAI_API_KEY_KEY,)

    if normalized_provider == OMNIROUTE_PROVIDER:
        return (OMNIROUTE_API_KEY_KEY,)

    if normalized_provider == OPENROUTER_PROVIDER:
        return (OPENROUTER_API_KEY_KEY,)

    if normalized_provider == ANTHROPIC_PROVIDER:
        return (ANTHROPIC_API_KEY_KEY,)

    if normalized_provider == DEEPSEEK_PROVIDER:
        return (DEEPSEEK_API_KEY_KEY,)

    if is_custom_provider(normalized_provider):
        # Custom providers store their API key in the provider config
        return ()

    backends = {
        backend
        for backend in (infer_freeflow_backend(model) for model in models)
        if backend is not None
    }
    if not backends:
        backends.add("gemini")

    required_keys: list[str] = []
    if "gemini" in backends:
        required_keys.append(GEMINI_API_KEY_KEY)
    if "groq" in backends:
        required_keys.append(GROQ_API_KEY_KEY)
    return tuple(required_keys)


def _build_openai_client(api_key: str, base_url: str | None = None, api_timeout: float = 60.0) -> Any:
    """Build an OpenAI-compatible client with explicit timeouts and HTTP-level retry."""
    import httpx

    http_client = httpx.Client(
        timeout=httpx.Timeout(
            connect=10.0,
            read=api_timeout,
            write=30.0,
            pool=10.0,
        ),
    )
    kwargs: dict[str, Any] = {"api_key": api_key, "max_retries": 1, "http_client": http_client}
    if base_url:
        kwargs["base_url"] = base_url
    return OpenAI(**kwargs)


def create_llm_client(provider: str, credentials: dict[str, str], verbose: bool = False) -> Any:
    """Instantiate an LLM client for the given provider using provided credentials."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        api_key = credentials.get(OPENAI_API_KEY_KEY, "").strip()
        if not api_key:
            raise RuntimeError("OpenAI API key is missing.")
        return _build_openai_client(api_key)

    if normalized_provider == OMNIROUTE_PROVIDER:
        api_key = credentials.get(OMNIROUTE_API_KEY_KEY, "").strip()
        if not api_key:
            raise RuntimeError(
                "OmniRoute API key is missing. Copy it from OmniRoute Dashboard -> Endpoints."
            )
        base_url = credentials.get(OMNIROUTE_BASE_URL_KEY, "").strip()
        if not base_url:
            base_url = OMNIROUTE_DEFAULT_BASE_URL
        return _build_openai_client(api_key, base_url=base_url)

    if normalized_provider == OPENROUTER_PROVIDER:
        api_key = credentials.get(OPENROUTER_API_KEY_KEY, "").strip()
        if not api_key:
            raise RuntimeError(
                "OpenRouter API key is missing. Get one at https://openrouter.ai/keys."
            )
        base_url = credentials.get(OPENROUTER_BASE_URL_KEY, "").strip()
        if not base_url:
            base_url = OPENROUTER_DEFAULT_BASE_URL
        return _build_openai_client(api_key, base_url=base_url)

    if normalized_provider == ANTHROPIC_PROVIDER:
        api_key = credentials.get(ANTHROPIC_API_KEY_KEY, "").strip()
        if not api_key:
            raise RuntimeError(
                "Anthropic API key is missing. Get one at https://console.anthropic.com/."
            )
        base_url = credentials.get(ANTHROPIC_BASE_URL_KEY, "").strip()
        if not base_url:
            base_url = ANTHROPIC_DEFAULT_BASE_URL
        return AnthropicCompatibleClient(api_key=api_key, base_url=base_url, verbose=verbose)

    if normalized_provider == DEEPSEEK_PROVIDER:
        api_key = credentials.get(DEEPSEEK_API_KEY_KEY, "").strip()
        if not api_key:
            raise RuntimeError(
                "DeepSeek API key is missing. Get one at https://platform.deepseek.com/."
            )
        base_url = credentials.get(DEEPSEEK_BASE_URL_KEY, "").strip()
        if not base_url:
            base_url = DEEPSEEK_DEFAULT_BASE_URL
        return _build_openai_client(api_key, base_url=base_url)

    if is_custom_provider(normalized_provider):
        name = extract_custom_provider_name(normalized_provider)
        from user_config import get_custom_provider_by_name
        info = get_custom_provider_by_name(name)
        if not info:
            raise RuntimeError(
                f"Custom provider '{name}' is not configured. "
                f"Use /provider to create it."
            )
        api_key = info.get("api_key", "").strip()
        base_url = info.get("base_url", "").strip()
        if not base_url:
            raise RuntimeError(
                f"Custom provider '{name}' has no base URL configured."
            )
        # A custom endpoint may need no authentication (the creation dialog lets
        # the key be skipped). Both SDK clients still demand a non-empty api_key,
        # so pass a placeholder the local endpoint ignores.
        if not api_key:
            api_key = "not-needed"
        protocol = info.get("protocol", "openai").strip()
        if protocol == "anthropic":
            return AnthropicCompatibleClient(api_key=api_key, base_url=base_url, verbose=verbose)
        return _build_openai_client(api_key, base_url=base_url)

    _configure_freeflow_logging(verbose)
    return FreeFlowCompatibleClient(
        gemini_api_key=credentials.get(GEMINI_API_KEY_KEY),
        groq_api_key=credentials.get(GROQ_API_KEY_KEY),
        verbose=verbose,
    )


def _configure_freeflow_logging(verbose: bool) -> None:
    """Configure logging for the FreeFlow client."""
    freeflow_logger = logging.getLogger("freeflow_llm")
    freeflow_logger.setLevel(logging.INFO if verbose else logging.ERROR)
    freeflow_logger.propagate = False
    if verbose and not freeflow_logger.handlers:
        handler = logging.StreamHandler()
        handler.setLevel(logging.INFO)
        freeflow_logger.addHandler(handler)


class _ChatNamespace:
    def __init__(self, owner: "FreeFlowCompatibleClient") -> None:
        self.completions = _CompletionsNamespace(owner)


class _CompletionsNamespace:
    def __init__(self, owner: "FreeFlowCompatibleClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any):
        model = normalize_model_name(FREEFLOW_PROVIDER, str(kwargs.get("model", "")).strip())
        if not model:
            raise RuntimeError("FreeFlow LLM requires a model name.")

        if kwargs.get("response_format") is not None:
            raise UnsupportedProviderFeatureError(
                "response_format=json_object is not supported by FreeFlow LLM"
            )

        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("FreeFlow LLM requires a non-empty messages list.")

        normalized_messages = [
            {
                "role": str(item.get("role", "user")),
                "content": str(item.get("content", "")),
            }
            for item in messages
            if isinstance(item, dict)
        ]
        if not normalized_messages:
            raise RuntimeError("FreeFlow LLM requires serializable chat messages.")

        stream = bool(kwargs.get("stream", False))
        request_kwargs = {
            "messages": normalized_messages,
            "temperature": float(kwargs.get("temperature", 1.0)),
            "max_tokens": kwargs.get("max_tokens"),
            "top_p": float(kwargs.get("top_p", 1.0)),
            "model": model,
        }
        # Reasoning effort reaches only Groq's request body (gpt-oss models);
        # the Gemini backend has no equivalent knob and drops extra kwargs.
        reasoning_effort = str(kwargs.get("reasoning_effort", "")).strip()
        if reasoning_effort and infer_freeflow_backend(model) == "groq":
            request_kwargs["reasoning_effort"] = reasoning_effort
        client = self._owner.build_client_for_model(model)
        try:
            if stream:
                return client.chat_stream(**request_kwargs)
            return client.chat(**request_kwargs)
        except Exception as error:
            self._owner.register_backend_failure(model, error)
            raise


class _AnthropicChatNamespace:
    """Minic the ``client.chat.completions`` attribute chain for Anthropic."""

    def __init__(self, owner: "AnthropicCompatibleClient") -> None:
        self.completions = _AnthropicCompletionsNamespace(owner)


def _anthropic_thinking_from_effort(effort: Any) -> dict[str, Any] | None:
    """Translate Kvist's ``reasoning_effort`` marker to an Anthropic thinking block.

    ``None`` (no marker) and level ``false`` both mean "no extended thinking",
    which is the wrapper's historical default.
    """
    if not effort:
        return None
    from reasoning_effort import anthropic_thinking_config

    return anthropic_thinking_config(str(effort))


class _AnthropicCompletionsNamespace:
    """Translate OpenAI-style ``chat.completions.create(**kwargs)`` to Anthropic Messages API."""

    def __init__(self, owner: "AnthropicCompatibleClient") -> None:
        self._owner = owner

    def create(self, **kwargs: Any):
        model = str(kwargs.get("model", "")).strip()
        if not model:
            raise RuntimeError("Anthropic provider requires a model name.")

        # Anthropic does not support response_format
        if kwargs.get("response_format") is not None:
            raise UnsupportedProviderFeatureError(
                "response_format=json_object is not supported by Anthropic"
            )

        messages = kwargs.get("messages")
        if not isinstance(messages, list) or not messages:
            raise RuntimeError("Anthropic provider requires a non-empty messages list.")

        # ── Extract system prompt ─────────────────────────────────
        system = None
        user_messages: list[dict[str, Any]] = []
        for item in messages:
            if isinstance(item, dict):
                role = str(item.get("role", "")).casefold()
                if role == "system":
                    system = str(item.get("content", ""))
                else:
                    user_messages.append({"role": role, "content": str(item.get("content", ""))})

        if not user_messages:
            # If all messages were system, create a minimal user message
            user_messages = [{"role": "user", "content": "(empty)"}]

        # ── Build request kwargs ──────────────────────────────────
        stream = bool(kwargs.get("stream", False))
        request_kwargs: dict[str, Any] = {
            "model": model,
            "messages": user_messages,
            "max_tokens": kwargs.get("max_tokens") or kwargs.get("max_completion_tokens") or 4096,
        }
        if system:
            request_kwargs["system"] = system

        thinking = _anthropic_thinking_from_effort(kwargs.get("reasoning_effort"))
        if thinking is not None:
            # Extended thinking rejects a non-default temperature / top_p and
            # requires max_tokens to exceed the thinking budget.
            request_kwargs["thinking"] = thinking
            budget = int(thinking["budget_tokens"])
            if request_kwargs["max_tokens"] <= budget:
                request_kwargs["max_tokens"] = budget + 4096
        else:
            if "temperature" in kwargs:
                request_kwargs["temperature"] = float(kwargs["temperature"])
            if "top_p" in kwargs:
                request_kwargs["top_p"] = float(kwargs["top_p"])
        if "stop" in kwargs and kwargs["stop"]:
            request_kwargs["stop_sequences"] = (
                [kwargs["stop"]] if isinstance(kwargs["stop"], str) else list(kwargs["stop"])
            )

        client = self._owner._create_client()
        try:
            if stream:
                return self._stream_response(client, request_kwargs)
            response = client.messages.create(**request_kwargs)
            return self._owner._normalize_response(response)
        except Exception as error:
            # Map Anthropic exceptions to OpenAI types for uniform error handling
            raise self._owner._map_error(error) from error

    def _stream_response(self, client, request_kwargs: dict[str, Any]):
        """Stream Anthropic events, yielding OpenAI-compatible chunk objects."""
        try:
            with client.messages.stream(**request_kwargs) as stream:
                for event in stream:
                    chunk = self._owner._normalize_stream_chunk(event)
                    if chunk is not None:
                        yield chunk
        except Exception as error:
            raise self._owner._map_error(error) from error


class AnthropicCompatibleClient:
    """Adapts the Anthropic SDK to the OpenAI-compatible interface.

    Wraps ``anthropic.Anthropic().messages.create()`` so the agent's
    existing ``client.chat.completions.create(**kwargs)`` calls work
    unchanged.  Handles:

    * System prompt → top-level ``system`` parameter
    * Messages without the ``system`` role
    * Response → ``.choices[0].message.content``
    * Streaming → ``.choices[0].delta.content`` chunks
    * Error mapping (Anthropic → OpenAI exception types)
    * Custom ``base_url`` for Anthropic-compatible endpoints
    """

    def __init__(
        self,
        api_key: str,
        base_url: str | None = None,
        verbose: bool = False,
    ) -> None:
        try:
            import anthropic  # type: ignore
        except ImportError as error:
            raise RuntimeError(
                "Anthropic SDK is not installed. Install dependency 'anthropic'."
            ) from error

        if not api_key or not api_key.strip():
            raise RuntimeError("Anthropic API key is required.")

        import anthropic

        self._api_key = api_key.strip()
        self._base_url = (base_url or "https://api.anthropic.com").strip()
        self._verbose = verbose
        self.chat = _AnthropicChatNamespace(self)
        self._client: Any = anthropic.Anthropic(api_key=self._api_key, base_url=self._base_url)

    def _create_client(self):
        return self._client

    def _normalize_response(self, response) -> Any:
        """Convert Anthropic response to an object that looks like an OpenAI completion."""
        from types import SimpleNamespace
        text = ""
        if hasattr(response, "content") and response.content:
            for block in response.content:
                if hasattr(block, "text"):
                    text += block.text
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    message=SimpleNamespace(content=text),
                    finish_reason=getattr(response, "stop_reason", None),
                )
            ]
        )

    def _normalize_stream_chunk(self, event) -> Any | None:
        """Convert an Anthropic streaming event to an OpenAI-like chunk, or None."""
        from types import SimpleNamespace
        event_type = getattr(event, "type", None)
        if event_type == "content_block_delta":
            delta = getattr(event, "delta", None)
            if delta and getattr(delta, "text", None):
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(content=delta.text)
                        )
                    ]
                )
            if delta and getattr(delta, "thinking", None):
                # Surface extended-thinking tokens on the reasoning channel so
                # the live panel shows them; they are not part of the answer.
                return SimpleNamespace(
                    choices=[
                        SimpleNamespace(
                            delta=SimpleNamespace(
                                content=None,
                                reasoning_content=delta.thinking,
                            )
                        )
                    ]
                )
        elif event_type == "message_delta":
            # The final event with stop_reason — send an empty delta with finish_reason
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(content=None),
                        finish_reason=getattr(event, "stop_reason", None),
                    )
                ]
            )
        return None

    def _map_error(self, error: Exception) -> Exception:
        """Wrap an Anthropic error with a clear message.

        We intentionally do NOT try to construct OpenAI exception types
        (APIStatusError / BadRequestError / etc.) because their constructors
        require httpx.Response and body keyword arguments that are not
        available from the Anthropic SDK.
        """
        import anthropic
        msg = redact_text(str(error))
        # Map known Anthropic error types to descriptive RuntimeError wrappers
        if isinstance(error, anthropic.AuthenticationError):
            return RuntimeError(f"Anthropic authentication failed: {msg}")
        if isinstance(error, anthropic.PermissionDeniedError):
            return RuntimeError(f"Anthropic permission denied: {msg}")
        if isinstance(error, anthropic.NotFoundError):
            return RuntimeError(f"Anthropic model/endpoint not found: {msg}")
        if isinstance(error, anthropic.RateLimitError):
            return RuntimeError(f"Anthropic rate limit exceeded: {msg}")
        if isinstance(error, anthropic.BadRequestError):
            return RuntimeError(f"Anthropic bad request: {msg}")
        if isinstance(error, anthropic.APIConnectionError):
            return RuntimeError(f"Anthropic connection error: {msg}")
        if isinstance(error, anthropic.InternalServerError):
            return RuntimeError(f"Anthropic server error: {msg}")
        # Generic Anthropic error or unknown type
        return RuntimeError(f"Anthropic API error: {msg}")


class FreeFlowCompatibleClient:
    def __init__(
        self,
        gemini_api_key: str | None = None,
        groq_api_key: str | None = None,
        verbose: bool = False,
    ) -> None:
        try:
            from freeflow_llm import FreeFlowClient  # type: ignore
            from freeflow_llm.providers.gemini import GeminiProvider  # type: ignore
            from freeflow_llm.providers.groq import GroqProvider  # type: ignore
        except ImportError as error:
            raise RuntimeError(
                "FreeFlow LLM is not installed. Install dependency 'freeflow-llm'."
            ) from error

        self._freeflow_client_cls = FreeFlowClient
        self._verbose = verbose
        self._providers: list[tuple[str, Any]] = []
        self._disabled_backends: set[str] = set()

        if isinstance(gemini_api_key, str) and gemini_api_key.strip():
            self._providers.append(("gemini", GeminiProvider(api_key=gemini_api_key.strip())))
        if isinstance(groq_api_key, str) and groq_api_key.strip():
            self._providers.append(("groq", GroqProvider(api_key=groq_api_key.strip())))

        if not self._providers:
            raise RuntimeError(
                "FreeFlow LLM requires at least one configured key: Gemini or Groq."
            )

        self.chat = _ChatNamespace(self)

    def build_client_for_model(self, model: str):
        preferred_backend = infer_freeflow_backend(model)
        return self._freeflow_client_cls(
            providers=self._ordered_providers(preferred_backend, model),
            verbose=self._verbose,
        )

    def register_backend_failure(self, model: str, error: Exception) -> None:
        backend = infer_freeflow_backend(model)
        if not backend:
            return
        lowered = str(error).casefold()
        terminal_markers = (
            "invalid api key",
            "authentication",
            "unauthorized",
            "no healthy backend available",
            "unavailable or misconfigured",
        )
        if any(marker in lowered for marker in terminal_markers):
            self._disabled_backends.add(backend)

    def _ordered_providers(self, preferred_backend: str | None, model: str) -> list[Any]:
        if preferred_backend:
            compatible = [
                provider
                for backend_name, provider in self._providers
                if backend_name == preferred_backend and backend_name not in self._disabled_backends
            ]
            if compatible:
                return compatible
            # The preferred backend is configured but currently disabled (e.g. auth failure).
            is_configured = preferred_backend in {
                backend_name for backend_name, _provider in self._providers
            }
            fallback_providers = [
                provider
                for backend_name, provider in self._providers
                if backend_name != preferred_backend and backend_name not in self._disabled_backends
            ]
            if is_configured and not fallback_providers:
                raise RuntimeError(
                    f"No healthy backend available for model '{model}'. "
                    f"Backend '{preferred_backend}' is unavailable or misconfigured. "
                    f"Check your API key for {preferred_backend.capitalize()}."
                )
            if fallback_providers:
                return fallback_providers

        preferred: list[Any] = []
        fallback: list[Any] = []
        for backend_name, provider in self._providers:
            if backend_name in self._disabled_backends:
                continue
            if preferred_backend and backend_name == preferred_backend:
                preferred.append(provider)
            else:
                fallback.append(provider)

        ordered = preferred + fallback
        if not ordered:
            raise RuntimeError("FreeFlow LLM has no available providers for the selected model.")
        return ordered
