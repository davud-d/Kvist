# -*- coding: utf-8 -*-
"""Centralised manifest of UI-facing metadata.

This module does not own any data.  It queries the authoritative source modules
(``role_router``, ``reasoning_effort``, ``llm_clients``, ``protocol``,
``slash_commands``, ``api_settings``, ``user_config``, ``theme``) and assembles
a single dict that a frontend can consume at startup instead of hardcoding its
own copies.

The manifest is intentionally cheap: every value it returns is already computed
and cached in the source module, so calling ``build_manifest`` on every
connection adds no measurable work.
"""
from __future__ import annotations

from typing import Any


def build_manifest(
    *,
    workspace: str | None = None,
    include_themes: bool = False,
) -> dict[str, Any]:
    """Assemble the full frontend manifest.

    Parameters
    ----------
    workspace:
        Workspace path, forwarded to theme discovery so workspace-local
        themes appear in the listing.
    include_themes:
        If *True*, embed the full theme colour maps (50 slots × 2 modes per
        theme).  Defaults to *False* because themes are large and a frontend
        that only needs names can call ``build_theme_manifest`` separately.
    """
    return {
        "roles": _roles(),
        "effort_levels": _effort_levels(),
        "providers": _providers(),
        "model_roles": _model_roles(),
        "default_models": _default_models(),
        "action_types": _action_types(),
        "slash_commands": _slash_commands(),
        "api_settings": _api_settings(),
        "styles": _styles(),
        "themes": _themes(workspace, include_full=include_themes),
    }


# -- Section builders (each queries exactly one source module) -----------------


def _roles() -> list[dict[str, str]]:
    from kernel.role_router import _VALID_ROLES, ROLE_DESCRIPTIONS

    # \"auto\" is a UI-level alias handled by main.py / the CLI, not a real role.
    # It goes first so pickers default to it.
    entries: list[dict[str, str]] = [
        {"value": "auto", "description": "Auto-detect role based on the task"},
    ]
    # Iterate over ROLE_DESCRIPTIONS (ordered dict) rather than _VALID_ROLES
    # (frozenset) so the manifest order is deterministic.
    for role, description in ROLE_DESCRIPTIONS.items():
        entries.append({
            "value": role,
            "description": description,
        })
    return entries


def _effort_levels() -> list[dict[str, str]]:
    from kernel.reasoning_effort import EFFORT_LEVELS, EFFORT_DESCRIPTIONS, DEFAULT_EFFORT

    return [
        {
            "value": level,
            "description": EFFORT_DESCRIPTIONS.get(level, ""),
            "default": "true" if level == DEFAULT_EFFORT else "",
        }
        for level in EFFORT_LEVELS
    ]


def _providers() -> list[dict[str, str]]:
    from kernel.llm_clients import (
        SUPPORTED_LLM_PROVIDERS,
        provider_label,
    )

    return [
        {"value": p, "label": provider_label(p)}
        for p in SUPPORTED_LLM_PROVIDERS
    ]


def _model_roles() -> list[str]:
    from kernel.llm_clients import MODEL_ROLES

    return list(MODEL_ROLES)


def _default_models() -> dict[str, dict[str, str]]:
    from kernel.llm_clients import _MODEL_DEFAULTS

    return dict(_MODEL_DEFAULTS)


def _action_types() -> list[str]:
    from typing import get_args

    from kernel.protocol import ActionType

    return list(get_args(ActionType))


def _slash_commands() -> list[dict[str, Any]]:
    from kernel.slash_commands import SLASH_COMMANDS

    result: list[dict[str, Any]] = []
    for cmd in SLASH_COMMANDS:
        entry: dict[str, Any] = {
            "name": cmd.name,
            "description": cmd.description,
        }
        if cmd.aliases:
            entry["aliases"] = list(cmd.aliases)
        result.append(entry)
    return result


def _api_settings() -> list[dict[str, Any]]:
    from kernel.api_settings import API_SETTINGS

    result: list[dict[str, Any]] = []
    for setting in API_SETTINGS:
        entry: dict[str, Any] = {
            "key": setting.key,
            "label": setting.label,
            "description": setting.description,
            "type": setting.kind,
            "default": setting.default,
        }
        if setting.minimum:
            entry["min"] = setting.minimum
        if setting.maximum:
            entry["max"] = setting.maximum
        if setting.unit:
            entry["unit"] = setting.unit
        if setting.zero_label:
            entry["zero_label"] = setting.zero_label
        if setting.env_var:
            entry["env_var"] = setting.env_var
        result.append(entry)
    return result


def _styles() -> dict[str, list[dict[str, str]]]:
    from kernel.user_config import get_style_options

    result: dict[str, list[dict[str, str]]] = {}
    for style_type in ("chat", "agents"):
        options = get_style_options(style_type)
        result[style_type] = [
            {"value": name, "description": desc}
            for name, desc in options.items()
        ]
    return result


def _themes(
    workspace: str | None,
    *,
    include_full: bool = False,
) -> dict[str, Any]:
    from kernel.theme import discover_themes, load_theme, SLOT_NAMES, ThemeError

    available = discover_themes(workspace=workspace)
    listing: list[dict[str, Any]] = []
    for name in sorted(available):
        entry: dict[str, Any] = {"name": name}
        if include_full:
            try:
                theme = load_theme(name, workspace=workspace)
                entry["dark"] = dict(theme.dark)
                entry["light"] = dict(theme.light)
            except ThemeError:
                entry["error"] = True
        listing.append(entry)
    result: dict[str, Any] = {
        "available": listing,
        "slots": list(SLOT_NAMES),
    }
    return result
