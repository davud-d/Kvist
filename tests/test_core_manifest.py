# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from core_manifest import build_manifest
from frontend_protocol import ManifestEvent


def test_build_manifest_keys():
    manifest = build_manifest()
    expected_keys = {
        "roles",
        "effort_levels",
        "providers",
        "model_roles",
        "default_models",
        "action_types",
        "slash_commands",
        "api_settings",
        "styles",
        "themes",
    }
    assert set(manifest.keys()) == expected_keys


def test_manifest_roles():
    manifest = build_manifest()
    roles = manifest["roles"]
    assert len(roles) >= 4
    assert roles[0]["value"] == "auto"
    role_values = [r["value"] for r in roles]
    assert "coder" in role_values
    assert "reader" in role_values
    assert "devops" in role_values
    assert "chat" in role_values


def test_manifest_effort_levels():
    manifest = build_manifest()
    effort = manifest["effort_levels"]
    assert len(effort) > 0
    values = [e["value"] for e in effort]
    assert "medium" in values
    # Check default flag presence
    defaults = [e for e in effort if e.get("default") == "true"]
    assert len(defaults) == 1


def test_manifest_providers():
    manifest = build_manifest()
    providers = manifest["providers"]
    assert len(providers) > 0
    values = [p["value"] for p in providers]
    assert "openai" in values
    assert "anthropic" in values


def test_manifest_action_types():
    manifest = build_manifest()
    actions = manifest["action_types"]
    assert "read_file" in actions
    assert "write_file" in actions
    assert "run_shell" in actions


def test_manifest_event_to_dict():
    manifest = build_manifest()
    event = ManifestEvent(manifest=manifest)
    d = event.to_dict()
    assert d["type"] == "manifest"
    assert "roles" in d
    assert d["roles"] == manifest["roles"]
