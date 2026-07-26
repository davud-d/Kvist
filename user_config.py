# -*- coding: utf-8 -*-
from __future__ import annotations

import getpass
import os
import sys
from collections.abc import Callable
from pathlib import Path

from openai import AuthenticationError, OpenAI

from durable_json import (
    JsonStorageCorruptError,
    JsonStorageError,
    read_json_object,
    update_json_object,
    write_json_object,
)
from llm_clients import (
    FREEFLOW_PROVIDER, OPENAI_PROVIDER, OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER,
    ANTHROPIC_PROVIDER,
    CUSTOM_PROVIDER_PREFIX, is_custom_provider, extract_custom_provider_name,
    normalize_provider,
)

USER_CONFIG_DIRNAME = ".kvist"
USER_CONFIG_FILENAME = "config.json"
USER_CONFIG_KEY = "openai_api_key"
LEGACY_USER_CONFIG_KEY = "OPENAI_API_KEY"
USER_CONFIG_KEYS = (USER_CONFIG_KEY, LEGACY_USER_CONFIG_KEY)
LLM_PROVIDER_KEY = "llm_provider"
LLM_MODELS_KEY = "llm_models"
ROLE_SELECTION_KEY = "role_selection"
DEFAULT_LLM_PROVIDER = OPENAI_PROVIDER
FALLBACK_LLM_PROVIDER = FREEFLOW_PROVIDER
GEMINI_API_KEY_KEY = "gemini_api_key"
GROQ_API_KEY_KEY = "groq_api_key"
OMNIROUTE_API_KEY_KEY = "omniroute_api_key"
OMNIROUTE_BASE_URL_KEY = "omniroute_base_url"
OPENROUTER_API_KEY_KEY = "openrouter_api_key"
OPENROUTER_BASE_URL_KEY = "openrouter_base_url"
ANTHROPIC_API_KEY_KEY = "anthropic_api_key"
ANTHROPIC_BASE_URL_KEY = "anthropic_base_url"
CUSTOM_PROVIDERS_KEY = "custom_providers"

_ENV_KEY_ALIASES = {
    USER_CONFIG_KEY: ("OPENAI_API_KEY",),
    GEMINI_API_KEY_KEY: ("GEMINI_API_KEY",),
    GROQ_API_KEY_KEY: ("GROQ_API_KEY",),
    OMNIROUTE_API_KEY_KEY: ("OMNIROUTE_API_KEY",),
    OMNIROUTE_BASE_URL_KEY: ("OMNIROUTE_BASE_URL",),
    OPENROUTER_API_KEY_KEY: ("OPENROUTER_API_KEY",),
    OPENROUTER_BASE_URL_KEY: ("OPENROUTER_BASE_URL",),
    ANTHROPIC_API_KEY_KEY: ("ANTHROPIC_API_KEY",),
    ANTHROPIC_BASE_URL_KEY: ("ANTHROPIC_BASE_URL",),
}

_SECRET_LABELS = {
    USER_CONFIG_KEY: "OpenAI API key",
    GEMINI_API_KEY_KEY: "Gemini API key",
    GROQ_API_KEY_KEY: "Groq API key",
    OMNIROUTE_API_KEY_KEY: "OmniRoute API key",
    OPENROUTER_API_KEY_KEY: "OpenRouter API key",
    ANTHROPIC_API_KEY_KEY: "Anthropic API key",
}


def get_user_config_path() -> Path:
    return Path.home() / USER_CONFIG_DIRNAME / USER_CONFIG_FILENAME


def _config_path_and_boundary(config_path: Path | None) -> tuple[Path, Path]:
    if config_path is None:
        boundary = Path.home().resolve(strict=True)
        return boundary / USER_CONFIG_DIRNAME / USER_CONFIG_FILENAME, boundary
    path = Path(os.path.abspath(Path(config_path).expanduser()))
    boundary = path.parent
    while not boundary.exists():
        if boundary.parent == boundary:
            raise RuntimeError("User config parent directory is unavailable.")
        boundary = boundary.parent
    return path, boundary.resolve(strict=True)


def load_user_config(config_path: Path | None = None) -> dict[str, object]:
    path, boundary = _config_path_and_boundary(config_path)
    try:
        return read_json_object(
            path,
            boundary=boundary,
            empty_as_missing=True,
        )
    except JsonStorageCorruptError as error:
        raise RuntimeError("User config is invalid.") from error
    except JsonStorageError as error:
        raise RuntimeError("Failed to read user config.") from error


def save_user_config(payload: dict[str, object], config_path: Path | None = None) -> None:
    path, boundary = _config_path_and_boundary(config_path)
    try:
        write_json_object(path, payload, boundary=boundary)
    except JsonStorageError as error:
        raise RuntimeError("Failed to save user config.") from error


def _update_user_config(
    update: Callable[[dict[str, object]], dict[str, object]],
    config_path: Path | None = None,
) -> dict[str, object]:
    path, boundary = _config_path_and_boundary(config_path)
    try:
        return update_json_object(
            path,
            update,
            boundary=boundary,
            empty_as_missing=True,
        )
    except JsonStorageCorruptError as error:
        raise RuntimeError(
            "User config is invalid; refusing to overwrite it."
        ) from error
    except JsonStorageError as error:
        raise RuntimeError("Failed to save user config.") from error


def load_api_key_or_bootstrap(validation_model: str) -> str:
    credentials = ensure_provider_credentials(
        provider=OPENAI_PROVIDER,
        validation_model=validation_model,
        required_keys=(USER_CONFIG_KEY,),
    )
    return credentials[USER_CONFIG_KEY]


def load_api_key(config_path: Path | None = None) -> str:
    payload = load_user_config(config_path)

    api_key = next(
        (
            value
            for key in USER_CONFIG_KEYS
            if isinstance((value := payload.get(key)), str) and value.strip()
        ),
        None,
    )
    if not isinstance(api_key, str) or not api_key.strip():
        raise RuntimeError("OpenAI API key is missing in user config.")

    return api_key.strip()


def get_saved_provider(config_path: Path | None = None) -> str:
    payload = load_user_config(config_path)
    raw_provider = payload.get(LLM_PROVIDER_KEY, DEFAULT_LLM_PROVIDER)
    return normalize_provider(str(raw_provider))


def get_saved_models(
    provider: str,
    config_path: Path | None = None,
) -> dict[str, str]:
    payload = load_user_config(config_path)
    all_models = payload.get(LLM_MODELS_KEY)
    if not isinstance(all_models, dict):
        return {}
    provider_models = all_models.get(normalize_provider(provider))
    if not isinstance(provider_models, dict):
        return {}
    resolved: dict[str, str] = {}
    for key in ("fast", "smart", "planner"):
        value = provider_models.get(key)
        if isinstance(value, str) and value.strip():
            resolved[key] = value.strip()
    return resolved


def save_models_for_provider(
    provider: str,
    models: dict[str, str],
    config_path: Path | None = None,
) -> None:
    normalized_provider = normalize_provider(provider)
    normalized_models = {
        key: value.strip()
        for key, value in models.items()
        if isinstance(value, str) and value.strip()
    }

    def save_models(payload: dict[str, object]) -> dict[str, object]:
        existing_models = payload.get(LLM_MODELS_KEY)
        all_models = (
            dict(existing_models)
            if isinstance(existing_models, dict)
            else {}
        )
        all_models[normalized_provider] = normalized_models
        payload[LLM_MODELS_KEY] = all_models
        return payload

    _update_user_config(save_models, config_path)


def get_saved_role(config_path: Path | None = None) -> str | None:
    """Get the saved role selection, or None if not set (auto mode)."""
    payload = load_user_config(config_path)
    role = payload.get(ROLE_SELECTION_KEY)
    if isinstance(role, str) and role.strip():
        return role.strip()
    return None


def save_role_selection(role: str, config_path: Path | None = None) -> None:
    """Save the role selection. Use 'auto' for automatic role detection."""
    normalized_role = role.strip()

    def save_role(payload: dict[str, object]) -> dict[str, object]:
        payload[ROLE_SELECTION_KEY] = normalized_role
        return payload

    _update_user_config(save_role, config_path)


def ensure_provider_credentials(
    provider: str,
    validation_model: str,
    required_keys: tuple[str, ...] = (),
    config_path: Path | None = None,
    language_hint: str | None = None,
    skip_validation: bool = False,
) -> dict[str, str]:
    normalized_provider = normalize_provider(provider)
    payload = load_user_config(config_path)
    payload[LLM_PROVIDER_KEY] = normalized_provider
    changed_values: dict[str, object] = {
        LLM_PROVIDER_KEY: normalized_provider,
    }
    collected: dict[str, str] = {}

    keys_to_resolve = required_keys
    if not keys_to_resolve and normalized_provider == OPENAI_PROVIDER:
        keys_to_resolve = (USER_CONFIG_KEY,)

    for key_name in keys_to_resolve:
        secret = _resolve_secret(payload, key_name)
        if not secret:
            secret = prompt_secret(_SECRET_LABELS.get(key_name, key_name))

        if not secret:
            if normalized_provider == FREEFLOW_PROVIDER:
                raise RuntimeError(
                    f"{_SECRET_LABELS.get(key_name, key_name)} is required for FreeFlow LLM."
                )
            if normalized_provider == OMNIROUTE_PROVIDER:
                raise RuntimeError(
                    f"{_SECRET_LABELS.get(key_name, key_name)} is required for OmniRoute."
                )
            if normalized_provider == OPENROUTER_PROVIDER:
                raise RuntimeError(
                    f"{_SECRET_LABELS.get(key_name, key_name)} is required for OpenRouter."
                )
            if normalized_provider == ANTHROPIC_PROVIDER:
                raise RuntimeError(
                    f"{_SECRET_LABELS.get(key_name, key_name)} is required for Anthropic."
                )
            raise RuntimeError("OpenAI API key is required.")

        secret = secret.strip()
        # Only validate OpenAI keys through the default OpenAI endpoint
        if key_name == USER_CONFIG_KEY and normalized_provider == OPENAI_PROVIDER and not skip_validation:
            validate_api_key(secret, validation_model, language_hint=language_hint)
        payload[key_name] = secret
        changed_values[key_name] = secret
        collected[key_name] = secret

    # Load base URL for OmniRoute and OpenRouter (non-secret)
    if normalized_provider == OMNIROUTE_PROVIDER:
        base_url = _resolve_secret(payload, OMNIROUTE_BASE_URL_KEY)
        if base_url:
            collected[OMNIROUTE_BASE_URL_KEY] = base_url.strip()
            payload[OMNIROUTE_BASE_URL_KEY] = base_url.strip()
            changed_values[OMNIROUTE_BASE_URL_KEY] = base_url.strip()
    elif normalized_provider == OPENROUTER_PROVIDER:
        base_url = _resolve_secret(payload, OPENROUTER_BASE_URL_KEY)
        if base_url:
            collected[OPENROUTER_BASE_URL_KEY] = base_url.strip()
            payload[OPENROUTER_BASE_URL_KEY] = base_url.strip()
            changed_values[OPENROUTER_BASE_URL_KEY] = base_url.strip()
    elif normalized_provider == ANTHROPIC_PROVIDER:
        base_url = _resolve_secret(payload, ANTHROPIC_BASE_URL_KEY)
        if base_url:
            collected[ANTHROPIC_BASE_URL_KEY] = base_url.strip()
            payload[ANTHROPIC_BASE_URL_KEY] = base_url.strip()
            changed_values[ANTHROPIC_BASE_URL_KEY] = base_url.strip()

    # Custom providers: credentials are stored in provider config, not as separate keys
    if is_custom_provider(normalized_provider):
        name = extract_custom_provider_name(normalized_provider)
        info = get_custom_provider_by_name(name, config_path)
        if info:
            if info.get("base_url"):
                collected["base_url"] = info["base_url"].strip()
            if info.get("api_key"):
                collected["api_key"] = info["api_key"].strip()

    def save_credentials(current: dict[str, object]) -> dict[str, object]:
        current.update(changed_values)
        return current

    _update_user_config(save_credentials, config_path)
    return collected

def get_saved_provider_or_none(config_path: Path | None = None) -> str | None:
    payload = load_user_config(config_path)
    raw_provider = payload.get(LLM_PROVIDER_KEY)
    if not isinstance(raw_provider, str) or not raw_provider.strip():
        return None
    return normalize_provider(raw_provider)


def prompt_provider_choice() -> str:
    print("Select LLM provider:")
    print("1. OpenAI")
    print("2. Anthropic (Claude)")
    print("3. FreeFlow LLM (Gemini/Groq)")
    print("4. OmniRoute")
    print("5. OpenRouter")

    while True:
        choice = input("> ").strip().lower()

        if choice in {"1", "openai", "o"}:
            return OPENAI_PROVIDER

        if choice in {"2", "anthropic", "claude", "ant", "a"}:
            return ANTHROPIC_PROVIDER

        if choice in {"3", "freeflow", "free", "f"}:
            return FREEFLOW_PROVIDER

        if choice in {"4", "omniroute", "omni", "or"}:
            return OMNIROUTE_PROVIDER

        if choice in {"5", "openrouter", "openroute", "openrt", "router"}:
            return OPENROUTER_PROVIDER

        print("Enter 1-5: OpenAI, Anthropic, FreeFlow, OmniRoute, OpenRouter.")



def load_available_credentials(
    key_names: tuple[str, ...],
    config_path: Path | None = None,
) -> dict[str, str]:
    payload = load_user_config(config_path)
    resolved: dict[str, str] = {}
    for key_name in key_names:
        secret = _resolve_secret(payload, key_name)
        if secret:
            resolved[key_name] = secret.strip()
    return resolved


def prompt_api_key() -> str:
    return prompt_secret("OpenAI API key")


def prompt_secret(label: str) -> str:
    if sys.stdin.isatty():
        return getpass.getpass(f"{label}: ").strip()

    print(f"{label}: ", end="", flush=True)
    data = sys.stdin.buffer.readline()
    if not data:
        return ""
    return data.decode("utf-8", errors="replace").strip()


def validate_api_key(api_key: str, model: str, language_hint: str | None = None) -> None:
    _ = model
    client = OpenAI(api_key=api_key)
    try:
        client.models.list()
    except AuthenticationError as error:
        raise RuntimeError("Invalid OpenAI API key.") from error
    except Exception as error:
        raise RuntimeError("Failed to validate OpenAI API key.") from error


def _resolve_secret(payload: dict[str, object], key_name: str) -> str | None:
    candidate_keys = (key_name,)
    if key_name == USER_CONFIG_KEY:
        candidate_keys = USER_CONFIG_KEYS

    for candidate_key in candidate_keys:
        value = payload.get(candidate_key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    for env_name in _ENV_KEY_ALIASES.get(key_name, ()):
        env_value = os.getenv(env_name, "").strip()
        if env_value:
            return env_value
    return None


# ── Custom provider CRUD ───────────────────────────────────────────────────


def get_custom_providers(config_path: Path | None = None) -> dict[str, dict]:
    """Return all saved custom provider configurations.

    Returns a dict keyed by provider name (without the 'custom:' prefix).
    """
    payload = load_user_config(config_path)
    providers = payload.get(CUSTOM_PROVIDERS_KEY)
    if not isinstance(providers, dict):
        return {}
    # Filter valid entries
    result: dict[str, dict] = {}
    for name, info in providers.items():
        if isinstance(info, dict) and isinstance(info.get("base_url"), str) and info["base_url"].strip():
            result[name] = {
                "label": str(info.get("label", name)).strip(),
                "protocol": str(info.get("protocol", "openai")).strip(),
                "base_url": info["base_url"].strip(),
                "api_key": str(info.get("api_key", "")).strip(),
                "models": _normalize_provider_models(info.get("models")),
                # Missing on providers saved before this field existed — treat as
                # unverified and default to the safe non-streaming path.
                "streaming": bool(info.get("streaming", False)),
            }
    return result


def get_custom_provider_by_name(name: str, config_path: Path | None = None) -> dict | None:
    """Return a single custom provider configuration by name, or None."""
    providers = get_custom_providers(config_path)
    return providers.get(name)


def save_custom_provider(name: str, provider_config: dict, config_path: Path | None = None) -> None:
    """Save or update a custom provider configuration.

    ``provider_config`` must have keys: ``label``, ``base_url``, ``api_key``, ``models``.
    ``streaming`` is optional and defaults to ``False``.
    """
    if not name or not name.strip():
        raise ValueError("Custom provider name cannot be empty")
    name = name.strip()
    normalized_config = {
        "label": str(provider_config.get("label", name)).strip(),
        "protocol": str(provider_config.get("protocol", "openai")).strip(),
        "base_url": str(provider_config.get("base_url", "")).strip(),
        "api_key": str(provider_config.get("api_key", "")).strip(),
        "models": _normalize_provider_models(provider_config.get("models")),
        "streaming": bool(provider_config.get("streaming", False)),
    }

    def save_provider(payload: dict[str, object]) -> dict[str, object]:
        existing = payload.get(CUSTOM_PROVIDERS_KEY)
        all_providers = dict(existing) if isinstance(existing, dict) else {}
        all_providers[name] = normalized_config
        payload[CUSTOM_PROVIDERS_KEY] = all_providers
        return payload

    _update_user_config(save_provider, config_path)


def delete_custom_provider(name: str, config_path: Path | None = None) -> bool:
    """Delete a custom provider. Returns True if it was deleted, False if not found."""
    if not name:
        return False
    deleted = False

    def delete_provider(payload: dict[str, object]) -> dict[str, object]:
        nonlocal deleted
        existing = payload.get(CUSTOM_PROVIDERS_KEY)
        if not isinstance(existing, dict) or name not in existing:
            return payload
        all_providers = dict(existing)
        del all_providers[name]
        payload[CUSTOM_PROVIDERS_KEY] = all_providers
        deleted = True
        return payload

    _update_user_config(delete_provider, config_path)
    return deleted


def _normalize_provider_models(models: object) -> dict[str, str]:
    """Normalize a models dict from config to {role: model_id}."""
    if not isinstance(models, dict):
        return {}
    result: dict[str, str] = {}
    for role in ("fast", "smart", "planner"):
        value = models.get(role)
        if isinstance(value, str) and value.strip():
            result[role] = value.strip()
    return result
