# -*- coding: utf-8 -*-
"""Headless, LLM-free snapshot of the saved run configuration for frontends.

Frontends must never read ``~/.kvist/config.json`` directly. This module is the
supported core boundary that answers "what would the next run actually use?"
without a model call or network round-trip: the saved default provider and role,
and, for every known provider, its resolved default models, the user's saved
model overrides, whether the required API credentials are present, and the
selectable model options. The GUI renders this so the Run configuration dialog
stops hiding everything behind opaque "saved" placeholders.
"""
from __future__ import annotations

from typing import Any

from llm_clients import (
    ANTHROPIC_PROVIDER,
    CUSTOM_PROVIDER_PREFIX,
    FREEFLOW_PROVIDER,
    MODEL_ROLES,
    OMNIROUTE_PROVIDER,
    OPENAI_PROVIDER,
    OPENROUTER_PROVIDER,
    get_model_options,
    normalize_provider,
    provider_label,
    required_secret_keys_for_provider,
    resolve_models_for_provider,
)
from user_config import (
    get_custom_providers,
    get_saved_models,
    get_saved_provider,
    get_saved_role,
    load_available_credentials,
    secret_label_for_key,
)

# The providers the GUI offers as first-class choices. Custom providers are
# appended separately because their credentials and models live in their own
# config block rather than in the shared secret/model keys.
_BUILTIN_PROVIDERS = (
    OPENAI_PROVIDER,
    ANTHROPIC_PROVIDER,
    OPENROUTER_PROVIDER,
    OMNIROUTE_PROVIDER,
    FREEFLOW_PROVIDER,
)


def _empty_roles() -> dict[str, str]:
    return {role: "" for role in MODEL_ROLES}


def _builtin_summary(provider: str) -> dict[str, Any]:
    normalized = normalize_provider(provider)
    saved_models = get_saved_models(normalized)
    resolved, _warnings = resolve_models_for_provider(normalized, saved_models)
    # Providers with no built-in defaults (OmniRoute/OpenRouter) leave roles
    # unresolved until the user types a model; the dialog must ask for one.
    manual = any(not resolved.get(role) for role in MODEL_ROLES)
    required = required_secret_keys_for_provider(normalized, tuple(resolved.values()))
    present = load_available_credentials(required)
    missing = [secret_label_for_key(key) for key in required if key not in present]
    return {
        "id": normalized,
        "label": provider_label(normalized),
        "custom": False,
        "requires_manual_models": manual,
        "defaults": {role: resolved.get(role, "") for role in MODEL_ROLES},
        "saved_models": {role: saved_models.get(role, "") for role in MODEL_ROLES},
        "options": get_model_options(normalized),
        "has_credentials": not missing,
        "missing_keys": missing,
    }


def _custom_summary(name: str, info: dict[str, Any]) -> dict[str, Any]:
    provider_id = f"{CUSTOM_PROVIDER_PREFIX}{name}"
    models = info.get("models") if isinstance(info.get("models"), dict) else {}
    saved_models = {role: str(models.get(role, "")).strip() for role in MODEL_ROLES}
    # Custom providers store their key inside the provider block, so credential
    # presence is a direct field check rather than a shared-secret lookup.
    has_key = bool(str(info.get("api_key", "")).strip())
    return {
        "id": provider_id,
        "label": provider_label(provider_id),
        "custom": True,
        "requires_manual_models": True,
        "defaults": saved_models,
        "saved_models": saved_models,
        "options": [],
        "base_url": str(info.get("base_url", "")).strip(),
        "has_credentials": has_key,
        "missing_keys": [] if has_key else ["API key"],
    }


def build_config_info() -> dict[str, Any]:
    """Return a JSON-serialisable snapshot of the saved run configuration."""
    providers = [_builtin_summary(name) for name in _BUILTIN_PROVIDERS]
    custom = [
        _custom_summary(name, info)
        for name, info in sorted(get_custom_providers().items())
    ]
    return {
        "ok": True,
        "saved_provider": get_saved_provider(),
        "saved_role": get_saved_role() or "auto",
        "providers": providers,
        "custom_providers": custom,
    }
