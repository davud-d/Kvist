# -*- coding: utf-8 -*-
from __future__ import annotations

import getpass
import json
import os
import sys
from pathlib import Path


from openai import AuthenticationError, OpenAI

from llm_clients import FREEFLOW_PROVIDER, OPENAI_PROVIDER, normalize_provider

USER_CONFIG_DIRNAME = ".devagent"
USER_CONFIG_FILENAME = "config.json"
USER_CONFIG_KEY = "openai_api_key"
LEGACY_USER_CONFIG_KEY = "OPENAI_API_KEY"
USER_CONFIG_KEYS = (USER_CONFIG_KEY, LEGACY_USER_CONFIG_KEY)
LLM_PROVIDER_KEY = "llm_provider"
LLM_MODELS_KEY = "llm_models"
DEFAULT_LLM_PROVIDER = OPENAI_PROVIDER
FALLBACK_LLM_PROVIDER = FREEFLOW_PROVIDER
GEMINI_API_KEY_KEY = "gemini_api_key"
GROQ_API_KEY_KEY = "groq_api_key"

_ENV_KEY_ALIASES = {
    USER_CONFIG_KEY: ("OPENAI_API_KEY",),
    GEMINI_API_KEY_KEY: ("GEMINI_API_KEY",),
    GROQ_API_KEY_KEY: ("GROQ_API_KEY",),
}

_SECRET_LABELS = {
    USER_CONFIG_KEY: "OpenAI API key",
    GEMINI_API_KEY_KEY: "Gemini API key",
    GROQ_API_KEY_KEY: "Groq API key",
}


def get_user_config_path() -> Path:
    return Path.home() / USER_CONFIG_DIRNAME / USER_CONFIG_FILENAME


def load_user_config(config_path: Path | None = None) -> dict[str, object]:
    path = config_path or get_user_config_path()
    if not path.exists():
        return {}

    try:
        raw_data = path.read_text(encoding="utf-8").strip()
    except OSError as error:
        raise RuntimeError("Failed to read user config.") from error

    if not raw_data:
        return {}

    try:
        payload = json.loads(raw_data)
    except json.JSONDecodeError as error:
        raise RuntimeError("User config is invalid.") from error

    if not isinstance(payload, dict):
        raise RuntimeError("User config is invalid.")

    return payload


def save_user_config(payload: dict[str, object], config_path: Path | None = None) -> None:
    path = config_path or get_user_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


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
    payload = load_user_config(config_path)
    all_models = payload.get(LLM_MODELS_KEY)
    if not isinstance(all_models, dict):
        all_models = {}
    all_models[normalize_provider(provider)] = {
        key: value.strip()
        for key, value in models.items()
        if isinstance(value, str) and value.strip()
    }
    payload[LLM_MODELS_KEY] = all_models
    save_user_config(payload, config_path)


def ensure_provider_credentials(
    provider: str,
    validation_model: str,
    required_keys: tuple[str, ...] = (),
    config_path: Path | None = None,
    language_hint: str | None = None,
) -> dict[str, str]:
    normalized_provider = normalize_provider(provider)
    payload = load_user_config(config_path)
    payload[LLM_PROVIDER_KEY] = normalized_provider
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
            raise RuntimeError("OpenAI API key is required.")

        secret = secret.strip()
        if key_name == USER_CONFIG_KEY:
            validate_api_key(secret, validation_model, language_hint=language_hint)
        payload[key_name] = secret
        collected[key_name] = secret

    save_user_config(payload, config_path)
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
    print("2. FreeFlow LLM (Gemini/Groq)")

    while True:
        choice = input("> ").strip().lower()

        if choice in {"1", "openai", "o"}:
            return OPENAI_PROVIDER

        if choice in {"2", "freeflow", "free", "f"}:
            return FREEFLOW_PROVIDER

        print("Enter 1 for OpenAI or 2 for FreeFlow LLM.")



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
