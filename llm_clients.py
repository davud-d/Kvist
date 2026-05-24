# -*- coding: utf-8 -*-
from __future__ import annotations

import logging
import os
from typing import Any

from language_utils import choose_localized_text
from openai import OpenAI

OPENAI_PROVIDER = "openai"
FREEFLOW_PROVIDER = "freeflow"
SUPPORTED_LLM_PROVIDERS = (OPENAI_PROVIDER, FREEFLOW_PROVIDER)

OPENAI_API_KEY_KEY = "openai_api_key"
GEMINI_API_KEY_KEY = "gemini_api_key"
GROQ_API_KEY_KEY = "groq_api_key"
MODEL_ROLES = ("fast", "smart", "planner")

_MODEL_DEFAULTS = {
    OPENAI_PROVIDER: {
        "fast": "gpt-5-mini",
        "smart": "gpt-5-mini",
        "planner": "gpt-5-mini",
    },
    FREEFLOW_PROVIDER: {
        "fast": "gemini-3.1-pro-preview",
        "smart": "gemini-3.1-pro-preview",
        "planner": "gemini-3.1-pro-preview",
    },
}

_MODEL_ENV_KEYS = {
    "fast": "AGENT_FAST_MODEL",
    "smart": "AGENT_SMART_MODEL",
    "planner": "AGENT_PLANNER_MODEL",
}

_MODEL_OPTIONS = {
    OPENAI_PROVIDER: [
        {
            "value": "gpt-5-nano",
            "label": "GPT-5 Nano",
            "description": "Самый быстрый и лёгкий вариант GPT-5",
        },
        {
            "value": "gpt-5-mini",
            "label": "GPT-5 Mini",
            "description": "Быстрый GPT-5 для повседневных шагов",
        },
        {
            "value": "gpt-5-mini",
            "label": "GPT-5 Mini (was GPT-5)",
            "description": "Основная сильная модель OpenAI",
        },
        {
            "value": "gpt-5-mini",
            "label": "GPT-5 Mini (was GPT-4.1 Mini)",
            "description": "Надёжный fallback, если GPT-5 недоступен",
        },
        {
            "value": "gpt-5-mini",
            "label": "GPT-5 Mini (was GPT-4.1)",
            "description": "Сильный fallback для smart/planner",
        },
        {
            "value": "gpt-5-mini",
            "label": "GPT-5 Mini (was GPT-4o Mini)",
            "description": "Быстрый мультимодальный fallback",
        },
        {
            "value": "gpt-5-mini",
            "label": "GPT-5 Mini (was GPT-4o)",
            "description": "Сильная мультимодальная fallback-модель",
        },
    ],
    FREEFLOW_PROVIDER: [
        {
            "value": "gemini-3.1-pro-preview",
            "label": "Gemini 3.1 Pro Preview",
            "description": "Текущий дефолт для сложных задач",
        },
        {
            "value": "gemini-3-flash-preview",
            "label": "Gemini 3 Flash Preview",
            "description": "Быстрее и дешевле, чем Pro",
        },
        {
            "value": "llama-3.3-70b-versatile",
            "label": "Llama 3.3 70B Versatile",
            "description": "Сильная Groq-модель общего назначения",
        },
        {
            "value": "llama-3.3-70b-versatile",
            "label": "Llama 3.3 70B Versatile (was 3.1)",
            "description": "Стабильный Groq fallback",
        },
        {
            "value": "llama-3.1-8b-instant",
            "label": "Llama 3.1 8B Instant",
            "description": "Очень быстрый Groq-вариант",
        },
        {
            "value": "gpt-oss-120b",
            "label": "GPT-OSS 120B",
            "description": "Усиленный reasoning через Groq",
        },
        {
            "value": "deepseek-r1-distill-qwen-32b",
            "label": "DeepSeek R1 Distill Qwen 32B",
            "description": "Reasoning fallback через Groq",
        },
        {
            "value": "qwen-3-32b",
            "label": "Qwen 3 32B",
            "description": "Сильная универсальная Groq-модель",
        },
        {
            "value": "qwen-2.5-coder-32b",
            "label": "Qwen 2.5 Coder 32B",
            "description": "Хороша для кодогенерации",
        },
        {
            "value": "llama-3.2-3b-preview",
            "label": "Llama 3.2 3B Preview",
            "description": "Легкий быстрый preview-вариант",
        },
        {
            "value": "llama-3.3-70b-versatile",
            "label": "Llama 3.3 70B Versatile (was ALLaM 2 7B)",
            "description": "Дополнительный Groq fallback",
        },
        {
            "value": "deepseek-chat",
            "label": "DeepSeek Chat",
            "description": "DeepSeek chat модель",
        },
    ],
}

_OPENAI_ROLE_FALLBACKS = {
    "fast": ("gpt-5-mini", "gpt-5-nano"),
    "smart": ("gpt-5-mini", "gpt-5-nano"),
    "planner": ("gpt-5-mini", "gpt-5-nano"),
}

_OPENAI_GENERIC_FALLBACKS = ("gpt-5-mini", "gpt-5-nano")

_FREEFLOW_GENERIC_FALLBACKS = (
    "gemini-3.1-pro-preview",
    "gemini-3-flash-preview",
    "llama-3.3-70b-versatile",
    "deepseek-chat",
)

_DEPRECATED_FREEFLOW_MODELS = {
    "mixtral-8x7b-32768",
    "llama-3.1-70b-versatile",
    "allam-2-7b",
    "deepseek-r1-distill-llama-70b",
}

_FREEFLOW_MODEL_ALIASES = {
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
    "deepseek-r1-distill-llama-70b": "gpt-oss-120b",
}

_FREEFLOW_GROQ_PREFIXES = (
    "llama-",
    "mixtral-",
    "gemma",
    "qwen",
    "deepseek",
    "moonshotai/",
)


class UnsupportedProviderFeatureError(RuntimeError):
    pass


def normalize_provider(provider: str | None) -> str:
    candidate = str(provider or "").strip().lower()
    if candidate in SUPPORTED_LLM_PROVIDERS:
        return candidate
    return OPENAI_PROVIDER


def provider_label(provider: str) -> str:
    return "FreeFlow LLM" if normalize_provider(provider) == FREEFLOW_PROVIDER else "OpenAI"


def normalize_model_name(provider: str, model: str) -> str:
    candidate = str(model or "").strip()
    if not candidate:
        return candidate
    if normalize_provider(provider) != FREEFLOW_PROVIDER:
        return candidate
    return _FREEFLOW_MODEL_ALIASES.get(candidate.casefold(), candidate)


def infer_freeflow_backend(model: str) -> str | None:
    candidate = normalize_model_name(FREEFLOW_PROVIDER, model).casefold()
    if candidate.startswith("gemini-"):
        return "gemini"
    if candidate.startswith(_FREEFLOW_GROQ_PREFIXES):
        return "groq"
    return None


def is_model_supported_by_provider(provider: str, model: str) -> bool:
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return True
    return infer_freeflow_backend(model) is not None


def is_model_deprecated(provider: str, model: str) -> bool:
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
    normalized_provider = normalize_provider(provider)
    if normalized_provider == OPENAI_PROVIDER:
        return _OPENAI_GENERIC_FALLBACKS
    return _FREEFLOW_GENERIC_FALLBACKS


def fallback_candidates_for_model(
    provider: str,
    model: str,
    allow_runtime_fallbacks: bool = False,
) -> tuple[str, ...]:
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
    return normalize_provider(provider) == OPENAI_PROVIDER


def configured_freeflow_backends(credentials: dict[str, str]) -> tuple[str, ...]:
    backends: list[str] = []
    if str(credentials.get(GEMINI_API_KEY_KEY, "")).strip():
        backends.append("gemini")
    if str(credentials.get(GROQ_API_KEY_KEY, "")).strip():
        backends.append("groq")
    return tuple(backends)


def required_backends_for_models(models: dict[str, str] | list[str] | tuple[str, ...]) -> tuple[str, ...]:
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
    normalized_provider = normalize_provider(provider)
    return [dict(item) for item in _MODEL_OPTIONS.get(normalized_provider, [])]


def required_secret_keys_for_provider(provider: str, models: list[str] | tuple[str, ...]) -> tuple[str, ...]:
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
            from freeflow_llm import FreeFlowClient
            from freeflow_llm.providers.gemini import GeminiProvider
            from freeflow_llm.providers.groq import GroqProvider
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
            # The preferred backend is configured but currently disabled (e.g. auth
            # failure recorded earlier in this session).  If no other backend is
            # available either, raise a clear error; otherwise fall through so the
            # caller can try a different backend.
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