# -*- coding: utf-8 -*-
"""The provider a core on a server works with: the person's own from this computer, held in the daemon's memory only.

The proxy sends it right after attaching to a daemon that asks for it (``remote_attached.needs_credentials``). The daemon
keeps it in ``user_config``'s in-memory holder, so it is never written to the server's disk, and the server's own provider
settings are not used for this work. They stay for running Kvist directly on the server. After the daemon restarts it asks again.
"""
from __future__ import annotations

import os

import httpx

from kernel.config import user_config
from kernel.llm.llm_clients import (
    ANTHROPIC_PROVIDER,
    CHATGPT_PROVIDER,
    DEEPSEEK_PROVIDER,
    MANUAL_OPENAI_PROVIDERS,
    OMNIROUTE_PROVIDER,
    OPENROUTER_PROVIDER,
    extract_custom_provider_name,
    is_custom_provider,
    normalize_provider,
    required_secret_keys_for_provider,
)

_BASE_URL_KEYS = {
    OMNIROUTE_PROVIDER: user_config.OMNIROUTE_BASE_URL_KEY,
    OPENROUTER_PROVIDER: user_config.OPENROUTER_BASE_URL_KEY,
    ANTHROPIC_PROVIDER: user_config.ANTHROPIC_BASE_URL_KEY,
    DEEPSEEK_PROVIDER: user_config.DEEPSEEK_BASE_URL_KEY,
}


def _base_url_key(provider: str) -> str | None:
    if provider in MANUAL_OPENAI_PROVIDERS:
        return MANUAL_OPENAI_PROVIDERS[provider]["base_url_key"]
    return _BASE_URL_KEYS.get(provider)


def _chatgpt_access(*, force_refresh: bool = False) -> dict[str, object] | None:
    """The ChatGPT sign-in as a server may have it: a short-lived access token and no refresh token.

    A refresh token is replaced whenever it is used, so one used on the server would leave this computer signed out. The
    access token is renewed here and sent again before it runs out (``Proxy._renew_loop``).
    """
    from kernel.providers import chatgpt_auth as auth

    try:
        if not auth.has_credentials():
            return None
        token = auth.access_token(force_refresh=force_refresh)
        store = user_config.load_user_config().get(auth.STORE_KEY, {})
        active = store["active"]
        record = store["accounts"][active]
    except (auth.ChatGPTAuthError, httpx.HTTPError, KeyError):
        return None
    lent = {"access_token": token, "expires_at": record["expires_at"], "scopes": record["scopes"], "email": record.get("email", ""), "lent": True}
    return {"active": active, "accounts": {active: lent}}


def renewal() -> dict[str, object] | None:
    """A new ChatGPT access token for a server that holds one, renewed here with the refresh token that never leaves this computer."""
    access = _chatgpt_access(force_refresh=True)
    return {user_config.CHATGPT_OAUTH_KEY: access} if access else None


def chatgpt_expiry(settings: dict[str, object]) -> float | None:
    """When the lent ChatGPT access token in ``settings`` runs out, if there is one."""
    store = settings.get(user_config.CHATGPT_OAUTH_KEY)
    if not isinstance(store, dict) or not isinstance(store.get("accounts"), dict):
        return None
    record = store["accounts"].get(store.get("active"))
    return float(record["expires_at"]) if isinstance(record, dict) and "expires_at" in record else None


def from_this_computer(wanted: str | None = None) -> dict[str, object] | None:
    """This computer's provider (``wanted``, else the one in use), its models and its key, as ``user_config`` keys; None when there is nothing to send.

    That is no provider set up, a key that is missing, or a ChatGPT sign-in that is not usable.
    """
    saved = wanted or os.environ.get("AGENT_LLM_PROVIDER", "").strip() or user_config.get_saved_provider_or_none()
    if not saved:
        return None
    provider = normalize_provider(saved)
    settings: dict[str, object] = {user_config.LLM_PROVIDER_KEY: provider}
    models = user_config.get_saved_models(provider)
    if models:
        settings[user_config.LLM_MODELS_KEY] = {provider: models}
    if provider == CHATGPT_PROVIDER:
        access = _chatgpt_access()
        if access is None:
            return None
        settings[user_config.CHATGPT_OAUTH_KEY] = access
        return settings
    if is_custom_provider(provider):
        name = extract_custom_provider_name(provider)
        entry = user_config.get_custom_provider_by_name(name)
        if entry is None:
            return None
        settings[user_config.CUSTOM_PROVIDERS_KEY] = {name: entry}
        return settings
    required = required_secret_keys_for_provider(provider, ())
    found = user_config.load_available_credentials(required)
    if len(found) < len(required):
        return None
    settings.update(found)
    base_url_key = _base_url_key(provider)
    if base_url_key:
        settings.update(user_config.load_available_credentials((base_url_key,)))
    return settings


def add(settings: object) -> None:
    """The daemon's side: another provider's settings, or a renewed token, join what is held (nothing is held: nothing happens)."""
    if isinstance(settings, dict):
        user_config.add_to_held({key: value for key, value in settings.items() if isinstance(key, str)})


def hold(settings: object) -> bool:
    """The daemon's side: keep what the frontend sent in memory. False when it is not something to hold."""
    if not isinstance(settings, dict) or user_config.LLM_PROVIDER_KEY not in settings:
        return False
    user_config.hold_provider_in_memory({key: value for key, value in settings.items() if isinstance(key, str)})
    return True
