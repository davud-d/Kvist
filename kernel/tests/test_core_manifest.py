# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from kernel.protocol.core_manifest import build_manifest
from kernel.protocol.frontend_protocol import ManifestEvent


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


def _by_name(manifest: dict) -> dict:
    return {entry["name"]: entry for entry in manifest["slash_commands"]}


def test_a_slash_command_the_core_carries_out_says_so():
    commands = _by_name(build_manifest(commands=["models", "resume", "session"]))

    assert commands["models"]["via"] == "core"
    assert commands["session"]["via"] == "core"
    # What the running core does not serve is only for the Classic Legacy terminal.
    assert "via" not in commands["bug"]


def test_without_a_command_table_nothing_is_claimed_to_be_the_cores():
    assert all(entry.get("via") != "core" for entry in build_manifest()["slash_commands"])


def test_the_machines_commands_are_listed_and_are_the_cores_when_it_serves_them():
    served = _by_name(build_manifest(commands=["machines", "remote"]))
    unserved = _by_name(build_manifest(commands=["models"]))

    assert served["machines"]["via"] == "core" and served["remote"]["via"] == "core"
    assert served["machines"]["description"]
    # A core that does not have them (an older server) leaves them out of what a frontend offers.
    assert "via" not in unserved["machines"]


def test_the_real_core_serves_what_the_manifest_calls_its_own():
    """Whatever the manifest says the core carries out must be in the command table the core really has."""
    from kernel.main import STDIO_COMMANDS

    manifest = build_manifest(commands=sorted({*STDIO_COMMANDS, "resume"}))
    claimed = {entry["name"] for entry in manifest["slash_commands"] if entry.get("via") == "core"}

    assert claimed <= {*STDIO_COMMANDS, "resume"}
    assert {"models", "session", "provider", "memory", "permissions", "doctor", "machines", "remote"} <= claimed
