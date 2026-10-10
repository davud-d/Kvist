"""Web adapter for the Python core's provider catalog and configuration services.

Secrets never enter a response, event, URL, or subprocess command line. Settings
are host-wide defaults; existing cores retain their configuration until changed
through the existing stdio command protocol.
"""
from __future__ import annotations

import os
import threading

from kernel import llm_clients as llm, user_config as config
from kernel.anymodel_onboarding import ANYMODEL_DEFAULT_MODELS, ANYMODEL_REGISTER_URL
from kernel.config_info import build_config_info
from kernel.providers.chatgpt_auth import account_status
# The manifest's own builders: the status chips need roles and reasoning levels with
# their descriptions before any core of a session is running.
from kernel.core_manifest import _effort_levels, _roles
from kernel.reasoning_effort import EFFORT_LEVELS

_lock = threading.RLock()
ROLE_CHOICES = frozenset(entry["value"] for entry in _roles())


def configuration_info() -> dict:
    info = build_config_info()
    provider = os.getenv("AGENT_LLM_PROVIDER") or config.get_saved_provider_or_none() or llm.DEFAULT_BLANK_START_PROVIDER
    entries = info["providers"] + info["custom_providers"]
    selected = next((entry for entry in entries if entry["id"] == provider), None)
    for entry in entries:
        required = llm.required_secret_keys_for_provider(entry["id"], tuple(entry["defaults"].values()))
        entry["credential_fields"] = [
            {"id": key, "label": config.secret_label_for_key(key),
             "present": key in config.load_available_credentials(required)} for key in required
        ] if not entry["custom"] else []
        base_key = f"{entry['id']}_base_url"
        manual = llm.MANUAL_OPENAI_PROVIDERS.get(entry["id"])
        if manual or entry["id"] in {"omniroute", "openrouter", "anthropic", "deepseek"}:
            entry["base_url"] = config.load_available_credentials((base_key,)).get(base_key, "")
            entry["base_url_placeholder"] = (manual or {}).get("default_base_url", "Provider default") or "Required provider endpoint"
            entry["can_edit_base_url"] = True
    info.update(
        provider=provider,
        setup_required=not selected or not selected["has_credentials"] or any(not v for v in selected["defaults"].values()),
        registration_url=ANYMODEL_REGISTER_URL,
        chatgpt_accounts=account_status(),
        recommended_models={llm.DEFAULT_BLANK_START_PROVIDER: list(ANYMODEL_DEFAULT_MODELS)},
        model_roles=list(llm.MODEL_ROLES),
        effort=config.get_saved_reasoning_effort(),
        effort_levels=list(EFFORT_LEVELS),
        effort_info=_effort_levels(),
        role=config.get_saved_role() or "auto",
        roles=_roles(),
        environment_overrides=[key for key in ("AGENT_LLM_PROVIDER", "AGENT_FAST_MODEL", "AGENT_SMART_MODEL", "AGENT_REASONING_EFFORT") if os.getenv(key)],
    )
    return info


def _provider(payload: dict) -> dict:
    value = payload.get("provider")
    info = configuration_info()
    entry = next((p for p in info["providers"] + info["custom_providers"] if p["id"] == value), None)
    if entry is None:
        raise ValueError("Choose a provider from the catalog.")
    return entry


def _credentials(entry: dict, payload: dict) -> tuple[dict, dict]:
    provider = entry["id"]
    fields = entry["credential_fields"]
    supplied = payload.get("credentials", {})
    if not isinstance(supplied, dict) or any(k not in {f["id"] for f in fields} for k in supplied):
        raise ValueError("Unsupported credential field.")
    if any(not isinstance(v, str) for v in supplied.values()):
        raise ValueError("Credentials must be text.")
    supplied = {k: v.strip() for k, v in supplied.items() if v.strip()}
    keys = tuple(f["id"] for f in fields) + (f"{provider}_base_url",)
    values = config.load_available_credentials(keys)
    values.update(supplied)
    base_url = payload.get("base_url", "")
    if not isinstance(base_url, str):
        raise ValueError("Provider address must be text.")
    if base_url.strip() and entry.get("can_edit_base_url"):
        from urllib.parse import urlparse
        parsed = urlparse(base_url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password or parsed.query or parsed.fragment:
            raise ValueError("Enter an HTTP(S) provider address without credentials or query parameters.")
        values[f"{provider}_base_url"] = base_url.strip()
    manual = llm.MANUAL_OPENAI_PROVIDERS.get(provider)
    if manual and not manual["default_base_url"] and not values.get(f"{provider}_base_url"):
        raise ValueError("Enter this provider's endpoint address.")
    return values, supplied


def _verify_anymodel_key(key: str) -> None:
    """Tell a refused AnyModel key from an unreachable AnyModel; the first run shows the message as is."""
    from kernel.providers.anymodel_pricing import AnyModelAuthError, AnyModelUnavailableError, fetch_model_coefficients

    try:
        fetch_model_coefficients(key, force_refresh=True)
    except AnyModelAuthError as error:
        raise ValueError("AnyModel did not accept this key. Copy it again from your AnyModel account.") from error
    except AnyModelUnavailableError as error:
        raise ValueError("Couldn't reach AnyModel. Check your connection, then try again.") from error


def model_catalog(payload: dict) -> dict:
    entry = _provider(payload)
    credentials, supplied = _credentials(entry, payload)
    if not entry["custom"] and entry.get("auth_type") != "oauth" and not any(credentials.get(f["id"]) for f in entry["credential_fields"]):
        raise ValueError("Enter an API key to load models.")
    if entry["id"] == llm.DEFAULT_BLANK_START_PROVIDER and supplied:
        _verify_anymodel_key(credentials["anymodel_api_key"])
    models, error = llm.fetch_available_models(entry["id"], credentials, force_refresh=True)
    if error:
        # Provider errors can contain request bodies; don't send raw exceptions
        # containing freshly entered credentials back to the browser.
        raise ValueError("Could not load models. Check the API key, provider address and connection.")
    return {"models": models}


def save_configuration(payload: dict) -> dict:
    with _lock:
        entry = _provider(payload)
        provider = entry["id"]
        if entry["custom"] and not entry["has_credentials"]:
            raise ValueError("Configure this custom provider's credentials in Kvist first.")
        if provider == "chatgpt" and not entry["has_credentials"]:
            raise ValueError("Continue with ChatGPT before saving / Сначала войдите через ChatGPT.")
        models = payload.get("models")
        if not isinstance(models, dict) or set(models) != set(llm.MODEL_ROLES) or any(
            not isinstance(v, str) or not v.strip() or len(v) > 256 for v in models.values()
        ):
            raise ValueError("Choose a model for each role.")
        models = {k: v.strip() for k, v in models.items()}
        effort = payload.get("effort", config.get_saved_reasoning_effort())
        if effort not in EFFORT_LEVELS:
            raise ValueError("Unknown reasoning level.")
        role = payload.get("role")
        if role is not None and role not in ROLE_CHOICES:
            raise ValueError("Unknown role.")
        credentials, supplied = _credentials(entry, payload)
        required = llm.required_secret_keys_for_provider(provider, tuple(models.values()))
        if not entry["custom"] and any(not credentials.get(key) for key in required):
            raise ValueError("Enter the required API keys before saving.")
        issues = llm.validate_model_selection(provider, models, credentials)
        if issues:
            raise ValueError("\n".join(issues))
        if supplied or payload.get("base_url", "").strip():
            if provider == llm.DEFAULT_BLANK_START_PROVIDER:
                from kernel.providers.anymodel_pricing import fetch_model_coefficients
                try:
                    fetch_model_coefficients(credentials["anymodel_api_key"], force_refresh=True)
                except Exception as error:
                    raise ValueError("AnyModel could not verify this key. Check the key and your connection; nothing was saved.") from error
            else:
                model_catalog(payload)
        # Use the core's existing atomic configuration writers, not a second
        # credential store owned by the web frontend.
        if entry["custom"]:
            name = llm.extract_custom_provider_name(provider)
            value = config.get_custom_provider_by_name(name)
            value["models"] = models
            config.save_custom_provider(name, value)
        else:
            for key, value in supplied.items():
                config.set_provider_secret(key, value)
            config.save_models_for_provider(provider, models)
            if payload.get("base_url", "").strip() and entry.get("can_edit_base_url"):
                config.set_provider_secret(f"{provider}_base_url", payload["base_url"])
        config.save_reasoning_effort(effort)
        if role is not None:
            config.save_role_selection(role)
        config.set_provider_secret(config.LLM_PROVIDER_KEY, provider)
        return configuration_info()


def chatgpt_authorization(payload: dict) -> dict:
    from kernel.providers import chatgpt_auth as auth
    operation = payload.get("operation", "status")
    if operation == "start":
        attempt_id, attempt = auth.start_login(account_id=payload.get("account") or None)
        return {"ok": True, "attempt": attempt_id, "url": attempt.url}
    if operation in {"poll", "cancel"}:
        return auth.login_status(str(payload.get("attempt", "")), cancel=operation == "cancel")
    if operation == "logout":
        return auth.sign_out()
    if operation == "select":
        auth.select_account(str(payload.get("account", "")))
    elif operation != "status":
        raise ValueError("Unknown ChatGPT authorization operation.")
    return {"ok": True, **auth.account_status()}
