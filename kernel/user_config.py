# -*- coding: utf-8 -*-
from __future__ import annotations

import getpass
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from openai import AuthenticationError, OpenAI

from kernel.durable_json import (
    JsonStorageCorruptError,
    JsonStorageError,
    read_json_object,
    update_json_object,
    write_json_object,
)
from kernel.llm_clients import (
    FREEFLOW_PROVIDER, OPENAI_PROVIDER, OMNIROUTE_PROVIDER, OPENROUTER_PROVIDER,
    ANTHROPIC_PROVIDER, DEEPSEEK_PROVIDER, MANUAL_OPENAI_PROVIDERS,
    ANYMODEL_PROVIDER, CUSTOM_PROVIDER_PREFIX, is_custom_provider, extract_custom_provider_name,
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
REASONING_EFFORT_KEY = "reasoning_effort"
TITLE_MODEL_KEY = "title_model"
API_SETTINGS_KEY = "api_settings"
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
DEEPSEEK_API_KEY_KEY = "deepseek_api_key"
DEEPSEEK_BASE_URL_KEY = "deepseek_base_url"
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
    DEEPSEEK_API_KEY_KEY: ("DEEPSEEK_API_KEY",),
    DEEPSEEK_BASE_URL_KEY: ("DEEPSEEK_BASE_URL",),
}
for _manual_provider, _manual_config in MANUAL_OPENAI_PROVIDERS.items():
    _ENV_KEY_ALIASES[_manual_config["api_key_key"]] = (_manual_config["api_key_key"].upper(),)
    _ENV_KEY_ALIASES[_manual_config["base_url_key"]] = (_manual_config["base_url_key"].upper(),)
del _manual_provider, _manual_config

_SECRET_LABELS = {
    USER_CONFIG_KEY: "OpenAI API key",
    GEMINI_API_KEY_KEY: "Gemini API key",
    GROQ_API_KEY_KEY: "Groq API key",
    OMNIROUTE_API_KEY_KEY: "OmniRoute API key",
    OPENROUTER_API_KEY_KEY: "OpenRouter API key",
    ANTHROPIC_API_KEY_KEY: "Anthropic API key",
    DEEPSEEK_API_KEY_KEY: "DeepSeek API key",
}
for _manual_provider, _manual_config in MANUAL_OPENAI_PROVIDERS.items():
    _SECRET_LABELS[_manual_config["api_key_key"]] = f"{_manual_config['label']} API key"
del _manual_provider, _manual_config

StyleType = Literal["chat", "agents"]

STYLES_KEY = "styles"
DEFAULT_STYLE = "Default"

# One-line summaries shown in the `/style` picker. The keys are the agent style
# names; each one has a prompt file under prompts/styles/agents/.
_STYLE_DESCRIPTIONS = {
    "Default": "Does what was asked. Answers for everything it touched, and nothing else.",
    "Senior-dev": "Does what the result needs, not only what was asked. Never makes a point of it.",
    "Friendly Developer": "The same work, but the person is never left alone with the result.",
    "Perfectionist": "Quality is part of the task, not a task for later. Pays in time.",
    "Paranoid": "An outage costs more than time spent checking. Looks before irreversible steps.",
    "Minimalist": "Every line is an obligation for years. Extends a task only toward deletion.",
    "Explainer": "A fix it cannot explain is no different from a wrong one. Finds the cause.",
    "Fast-and-dirty": "A prototype may be bad if it is honestly called one. Says so first.",
    "Architect": "A change that blurs the structure is negative even when the task is done.",
}

# The one choice is the agent style; the chat style is derived from it here and
# nowhere else. Every value names a file under prompts/styles/chat/.
_CHAT_STYLE_FOR_AGENT_STYLE = {
    "Default": "Default",
    "Architect": "Default",
    "Senior-dev": "Honest",
    "Paranoid": "Honest",
    "Perfectionist": "Honest",
    "Friendly Developer": "Friendly",
    "Explainer": "Friendly",
    "Minimalist": "No-Friendly",
    "Fast-and-dirty": "Energetic",
}

# The system-prompt text for each style lives in its own file under
# prompts/styles/<chat|agents>/<slug>.txt (slug = style_slug(name)). A missing
# or empty file leaves the shared voice without that style's accent. Chat also
# receives the selected working style's HOW IT SOUNDS section, so styles that
# share a chat palette still retain their own emphasis.


def chat_style_for(agent_style: str) -> str:
    """The chat style that goes with an agent style name from `get_style_options`."""
    return _CHAT_STYLE_FOR_AGENT_STYLE[agent_style]


def style_slug(name: str) -> str:
    """Style name -> its prompt-file stem, e.g. 'Friendly Developer' -> 'friendly-developer'."""
    return name.strip().lower().replace(" ", "-")


def get_style_options() -> dict[str, str]:
    """Agent style name -> one-line description, for the `/style` picker.

    The line ends with the chat style the choice brings with it.
    """
    return {
        name: f"{description} Chat: {chat_style_for(name)}."
        for name, description in _STYLE_DESCRIPTIONS.items()
    }


def get_saved_style(style_type: StyleType, config_path: Path | None = None) -> str:
    """The saved agent style, or the chat style that goes with it.

    Only `styles.agents` is read: a `styles.chat` left in the file by an older
    build is ignored. "Default" when nothing valid is saved.
    """
    payload = load_user_config(config_path)
    styles = payload.get(STYLES_KEY)
    agent_style = DEFAULT_STYLE
    if isinstance(styles, dict):
        value = styles.get("agents")
        if isinstance(value, str) and value in _STYLE_DESCRIPTIONS:
            agent_style = value
    return agent_style if style_type == "agents" else chat_style_for(agent_style)

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


WEB_LOCAL_CONFIG_KEY = "web_local"


def get_web_local_config(config_path: Path | None = None) -> dict[str, object]:
    """Return the non-secret local-web settings from Kvist's global config."""
    value = load_user_config(config_path).get(WEB_LOCAL_CONFIG_KEY)
    return dict(value) if isinstance(value, dict) else {}


def save_web_local_config(
    value: dict[str, object], config_path: Path | None = None
) -> None:
    """Atomically replace local-web settings without touching core settings."""
    if not isinstance(value, dict):
        raise TypeError("Web configuration must be an object.")

    def update(payload: dict[str, object]) -> dict[str, object]:
        payload[WEB_LOCAL_CONFIG_KEY] = value
        return payload

    _update_user_config(update, config_path)


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
    for key in ("fast", "smart"):
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


FRONTEND_KEY = "frontend"


SAVED_FRONTENDS = ("terminal", "tui", "claude")


def get_saved_frontend(config_path: Path | None = None) -> str:
    """Return the saved terminal frontend (plain terminal by default)."""
    payload = load_user_config(config_path)
    value = payload.get(FRONTEND_KEY)
    return value if value in ("tui", "claude") else "terminal"


def save_frontend(frontend: str, config_path: Path | None = None) -> None:
    """Persist the frontend used on the next launch."""
    normalized = str(frontend).strip().casefold()
    if normalized not in SAVED_FRONTENDS:
        raise ValueError("Frontend must be 'terminal', 'tui' or 'claude'.")

    def update(payload: dict[str, object]) -> dict[str, object]:
        if normalized == "terminal":
            payload.pop(FRONTEND_KEY, None)
        else:
            payload[FRONTEND_KEY] = normalized
        return payload

    _update_user_config(update, config_path)


def save_role_selection(role: str, config_path: Path | None = None) -> None:
    """Save the role selection. Use 'auto' for automatic role detection."""
    normalized_role = role.strip()

    def save_role(payload: dict[str, object]) -> dict[str, object]:
        payload[ROLE_SELECTION_KEY] = normalized_role
        return payload

    _update_user_config(save_role, config_path)


def get_saved_reasoning_effort(config_path: Path | None = None) -> str:
    """The persisted global reasoning-effort level. Defaults to 'medium'.

    Kept as a plain string here to avoid importing the level scale into the
    config layer; callers normalize it (reasoning_effort.normalize_effort).
    """
    payload = load_user_config(config_path)
    value = payload.get(REASONING_EFFORT_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip().casefold()
    return "medium"


def save_reasoning_effort(level: str, config_path: Path | None = None) -> None:
    """Persist the global reasoning-effort preference for future sessions."""
    normalized = str(level).strip().casefold()

    def save_effort(payload: dict[str, object]) -> dict[str, object]:
        payload[REASONING_EFFORT_KEY] = normalized
        return payload

    _update_user_config(save_effort, config_path)


def get_saved_title_model(config_path: Path | None = None) -> str | None:
    """The model id saved for session-title generation, or None (derive it
    from the active provider's fast/cheap tier — see ``AgentConfig.title_model``)."""
    payload = load_user_config(config_path)
    value = payload.get(TITLE_MODEL_KEY)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def save_title_model(model: str, config_path: Path | None = None) -> None:
    """Persist the session-title model, or clear it back to "derive" on empty."""
    normalized = str(model).strip()

    def save_model(payload: dict[str, object]) -> dict[str, object]:
        if normalized:
            payload[TITLE_MODEL_KEY] = normalized
        else:
            payload.pop(TITLE_MODEL_KEY, None)
        return payload

    _update_user_config(save_model, config_path)


def get_saved_api_settings(config_path: Path | None = None) -> dict[str, object]:
    """The persisted API transport overrides, as stored.

    Only the knobs the user actually changed are present; ``api_settings.py``
    owns the defaults and the validation, so this layer stays a plain reader.
    """
    payload = load_user_config(config_path)
    value = payload.get(API_SETTINGS_KEY)
    if not isinstance(value, dict):
        return {}
    return {
        str(key): item
        for key, item in value.items()
        if isinstance(item, (bool, int, float))
    }


def save_api_settings(
    settings: dict[str, object],
    config_path: Path | None = None,
) -> None:
    """Replace the persisted API transport overrides with *settings*."""
    stored = {
        str(key): item
        for key, item in settings.items()
        if isinstance(item, (bool, int, float))
    }

    def save_settings(payload: dict[str, object]) -> dict[str, object]:
        payload[API_SETTINGS_KEY] = stored
        return payload

    _update_user_config(save_settings, config_path)


def ensure_provider_credentials(
    provider: str,
    validation_model: str,
    required_keys: tuple[str, ...] = (),
    config_path: Path | None = None,
    language_hint: str | None = None,
    skip_validation: bool = False,
    prompt_secret_fn: Callable[[str], str] | None = None,
) -> dict[str, str]:
    """Resolve (and persist) the secrets a provider needs, prompting for any
    that are missing.

    ``prompt_secret_fn`` overrides how a missing secret is collected. It
    defaults to the bare ``getpass``-based :func:`prompt_secret` in this
    module, which has no masking feedback at all (not even asterisks — that
    is ``getpass``'s actual behavior) and can fall back to fully visible
    input if the terminal can't disable echo. Frontends with a real input
    widget (the Rich/prompt_toolkit masked field in ``TerminalBannerUI``)
    should pass their own so first-time key entry gets the same visible
    feedback as `/change-api` already has, instead of silently dropping to
    this primitive fallback.
    """
    secret_prompt = prompt_secret_fn or prompt_secret
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
            secret = secret_prompt(_SECRET_LABELS.get(key_name, key_name))

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
            if normalized_provider == DEEPSEEK_PROVIDER:
                raise RuntimeError(
                    f"{_SECRET_LABELS.get(key_name, key_name)} is required for DeepSeek."
                )
            if normalized_provider in MANUAL_OPENAI_PROVIDERS:
                label = MANUAL_OPENAI_PROVIDERS[normalized_provider]["label"]
                raise RuntimeError(
                    f"{_SECRET_LABELS.get(key_name, key_name)} is required for {label}."
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
    elif normalized_provider == DEEPSEEK_PROVIDER:
        base_url = _resolve_secret(payload, DEEPSEEK_BASE_URL_KEY)
        if base_url:
            collected[DEEPSEEK_BASE_URL_KEY] = base_url.strip()
            payload[DEEPSEEK_BASE_URL_KEY] = base_url.strip()
            changed_values[DEEPSEEK_BASE_URL_KEY] = base_url.strip()
    elif normalized_provider in MANUAL_OPENAI_PROVIDERS:
        base_url_key = MANUAL_OPENAI_PROVIDERS[normalized_provider]["base_url_key"]
        base_url = _resolve_secret(payload, base_url_key)
        if base_url:
            collected[base_url_key] = base_url.strip()
            payload[base_url_key] = base_url.strip()
            changed_values[base_url_key] = base_url.strip()

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
    # AnyModel leads: it is the partner provider and the blank-start default.
    fixed_options: list[tuple[str, str, set[str]]] = [
        (ANYMODEL_PROVIDER, "AnyModel", {"anymodel"}),
        (OPENAI_PROVIDER, "OpenAI", {"openai", "o"}),
        (ANTHROPIC_PROVIDER, "Anthropic (Claude)", {"anthropic", "claude", "ant", "a"}),
        (DEEPSEEK_PROVIDER, "DeepSeek", {"deepseek", "ds"}),
        (FREEFLOW_PROVIDER, "FreeFlow LLM (Gemini/Groq)", {"freeflow", "free", "f"}),
        (OMNIROUTE_PROVIDER, "OmniRoute", {"omniroute", "omni", "or"}),
        (OPENROUTER_PROVIDER, "OpenRouter", {"openrouter", "openroute", "openrt", "router"}),
    ]
    # AnyModel is listed first above; skip its manual-tier entry to avoid a duplicate.
    manual_options = [
        (slug, config["label"], {slug})
        for slug, config in MANUAL_OPENAI_PROVIDERS.items()
        if slug != ANYMODEL_PROVIDER
    ]
    options = fixed_options + manual_options

    print("Select LLM provider:")
    for index, (_slug, label, _aliases) in enumerate(options, start=1):
        print(f"{index}. {label}")

    aliases_by_number = {str(index): slug for index, (slug, _label, _aliases) in enumerate(options, start=1)}
    aliases_by_name: dict[str, str] = {}
    for slug, _label, aliases in options:
        for alias in aliases:
            aliases_by_name[alias] = slug

    while True:
        choice = input("> ").strip().lower()

        if choice in aliases_by_number:
            return aliases_by_number[choice]
        if choice in aliases_by_name:
            return aliases_by_name[choice]

        print(f"Enter 1-{len(options)}, or the provider name.")



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


def secret_label_for_key(key_name: str) -> str:
    return _SECRET_LABELS.get(key_name, key_name)


def set_provider_secret(
    key_name: str,
    value: str,
    config_path: Path | None = None,
) -> None:
    """Overwrite a single provider secret in the user config, replacing any prior value."""
    trimmed = value.strip()

    def save_secret(payload: dict[str, object]) -> dict[str, object]:
        payload[key_name] = trimmed
        return payload

    _update_user_config(save_secret, config_path)


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


def clear_saved_provider(config_path: Path | None = None) -> None:
    """Unset the saved provider selection.

    Used after deleting the active custom provider: leaving the stale
    ``llm_provider`` value in place would point the next launch at a
    provider whose config no longer exists.
    """

    def clear_provider(payload: dict[str, object]) -> dict[str, object]:
        payload.pop(LLM_PROVIDER_KEY, None)
        return payload

    _update_user_config(clear_provider, config_path)


def reset_user_config(config_path: Path | None = None) -> None:
    """Wipe the entire user config file (API keys, custom providers, all
    saved preferences). Used by ``/full-reset``; irreversible."""
    save_user_config({}, config_path)


def save_style_configuration(style: str, config_path: Path | None = None) -> bool:
    """Save the agent style. Returns True if saved, False if not found."""
    if style not in _STYLE_DESCRIPTIONS:
        return False

    def set_style(config: dict) -> dict:
        config.setdefault(STYLES_KEY, {})["agents"] = style
        return config

    _update_user_config(set_style, config_path)
    return True


def _normalize_provider_models(models: object) -> dict[str, str]:
    """Normalize a models dict from config to {role: model_id}."""
    if not isinstance(models, dict):
        return {}
    result: dict[str, str] = {}
    for role in ("fast", "smart"):
        value = models.get(role)
        if isinstance(value, str) and value.strip():
            result[role] = value.strip()
    return result
