# -*- coding: utf-8 -*-
"""Validation for custom-provider payloads coming from a frontend.

The terminal builds a custom provider through an interactive wizard
(``main.configure_custom_provider_interactively``). The GUI has no place to run
that wizard, so it collects the same fields in a form and hands them here as one
dict. This module turns that dict into the ``(name, config)`` pair
``user_config.save_custom_provider`` expects, applying the same normalisation the
wizard does (https:// prefix, ``/v1`` suffix for the OpenAI protocol) and
rejecting anything malformed with a single clear message.
"""
from __future__ import annotations

from typing import Any

from llm_clients import MODEL_ROLES

_PROTOCOLS = ("openai", "anthropic")


class CustomProviderError(ValueError):
    """A custom-provider payload is missing a field or has an invalid value."""


def validate_provider_name(raw: object) -> str:
    """Return a normalised custom-provider name, or raise ``CustomProviderError``."""
    name = str(raw or "").strip().lower()
    if not name:
        raise CustomProviderError("Provider ID cannot be empty.")
    if not all(c.islower() or c.isdigit() or c in "-_" for c in name):
        raise CustomProviderError(
            "Provider ID may contain only lowercase letters, digits, '-' and '_'."
        )
    return name


def normalize_base_url(raw: object) -> str:
    """Prefix a scheme and drop a trailing slash. Path decisions (e.g. the
    ``/v1`` suffix most OpenAI-compatible gateways need) are left to the caller,
    which is closer to the user and can ask."""
    base_url = str(raw or "").strip()
    if not base_url:
        raise CustomProviderError("Base URL cannot be empty.")
    if not base_url.startswith(("http://", "https://")):
        base_url = "https://" + base_url
    return base_url.rstrip("/")


def _clean_models(raw: object) -> dict[str, str]:
    models = raw if isinstance(raw, dict) else {}
    resolved: dict[str, str] = {}
    for role in MODEL_ROLES:
        value = str(models.get(role, "")).strip()
        if not value:
            raise CustomProviderError(f"Model ID for the {role} role cannot be empty.")
        resolved[role] = value
    return resolved


def normalize_custom_provider_payload(payload: Any) -> tuple[str, dict[str, Any]]:
    """Return ``(name, config)`` for ``user_config.save_custom_provider``.

    Raises ``CustomProviderError`` on any missing or invalid field.
    """
    if not isinstance(payload, dict):
        raise CustomProviderError("Custom-provider payload must be an object.")

    name = validate_provider_name(payload.get("name"))
    protocol = str(payload.get("protocol", "openai")).strip().lower() or "openai"
    if protocol not in _PROTOCOLS:
        raise CustomProviderError("Protocol must be 'openai' or 'anthropic'.")

    label = str(payload.get("label", "")).strip() or name.replace("-", " ").replace("_", " ").title()
    config = {
        "label": label,
        "protocol": protocol,
        "base_url": normalize_base_url(payload.get("base_url")),
        "api_key": str(payload.get("api_key", "")).strip(),
        "models": _clean_models(payload.get("models")),
        "streaming": bool(payload.get("streaming", False)),
    }
    return name, config
