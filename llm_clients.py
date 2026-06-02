# -*- coding: utf-8 -*-
"""
This module configures LLM clients and model lists for DevAgentXD.

The defaults and model options have been updated to reflect the most recent
officially supported model IDs from OpenAI, Google Gemini, Groq and DeepSeek.
Please do not modify these IDs without verifying them against official
documentation; see MODEL_UPDATE_REPORT.md for details and sources.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from language_utils import choose_localized_text
from openai import OpenAI

# Provider names.
OPENAI_PROVIDER = "openai"
FREEFLOW_PROVIDER = "freeflow"
SUPPORTED_LLM_PROVIDERS = (OPENAI_PROVIDER, FREEFLOW_PROVIDER)

# API key environment keys.
OPENAI_API_KEY_KEY = "openai_api_key"
GEMINI_API_KEY_KEY = "gemini_api_key"
GROQ_API_KEY_KEY = "groq_api_key"
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
    return OPENAI_PROVIDER


def provider_label(provider: str) -> str:
    """Return a human‑friendly label for a provider."""
    return "FreeFlow LLM" if normalize_provider(provider) == FREEFLOW_PROVIDER else "OpenAI"


def normalize_model_name(provider: str, model: str) -> str:
    """Normalize a model name by applying aliases for FreeFlow models."""
    candidate = str(model or "").strip()
    if not candidate:
        return candidate
    if normalize_provider(provider) != FREEFLOW_PROVIDER:
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


def is_model_supported_by_provider(provider: str, model: str) -> bool:
    """Check whether the provider supports the given model."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return True
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


def resolve_openai_accessible_models(
    api_key: str,
    requested_models: dict[str, str],
    language_hint: str | None = None,
) -> tuple[dict[str, str], list[str]]:
    """Filter requested models down to those actually accessible for the given OpenAI API key."""
    client = OpenAI(api_key=api_key)
    available_models = {
        str(getattr(item, "id", "")).strip()
        for item in client.models.list().data
        if str(getattr(item, "id", "")).strip()
    }
    if not available_models:
        return requested_models, []

    resolved: dict[str, str] = {}
    warnings: list[str] = []

    for role, requested_model in requested_models.items():
        candidates = [requested_model, *_OPENAI_ROLE_FALLBACKS.get(role, _OPENAI_GENERIC_FALLBACKS)]
        selected_model = next((item for item in candidates if item in available_models), None)
        if selected_model is None:
            selected_model = requested_model
        elif selected_model != requested_model:
            warnings.append(
                choose_localized_text(
                    f"Модель '{requested_model}' недоступна для этого OpenAI API key. Использую '{selected_model}' для роли {role}.",
                    f"Model '{requested_model}' is unavailable for this OpenAI API key. Using '{selected_model}' for role {role}.",
                    language_hint,
                )
            )
        resolved[role] = selected_model

    return resolved, warnings


def fallback_models_for_provider(provider: str) -> tuple[str, ...]:
    """Return the generic fallback chain for a provider."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return _OPENAI_GENERIC_FALLBACKS
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
    """Return True if the provider supports streaming responses."""
    return normalize_provider(provider) == OPENAI_PROVIDER


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


def get_model_options(provider: str) -> list[dict[str, str]]:
    """Return a deep copy of the model options list for the given provider."""
    normalized_provider = normalize_provider(provider)
    return [dict(item) for item in _MODEL_OPTIONS.get(normalized_provider, [])]


def required_secret_keys_for_provider(provider: str, models: list[str] | tuple[str, ...]) -> tuple[str, ...]:
    """Return which secret keys must be configured for the given provider and models."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return (OPENAI_API_KEY_KEY,)

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


def create_llm_client(provider: str, credentials: dict[str, str], verbose: bool = False) -> Any:
    """Instantiate an LLM client for the given provider using provided credentials."""
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        api_key = credentials.get(OPENAI_API_KEY_KEY, "").strip()
        if not api_key:
            raise RuntimeError("OpenAI API key is missing.")
        return OpenAI(api_key=api_key)

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
        client = self._owner.build_client_for_model(model)
        try:
            if stream:
                return client.chat_stream(**request_kwargs)
            return client.chat(**request_kwargs)
        except Exception as error:
            self._owner.register_backend_failure(model, error)
            raise


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
