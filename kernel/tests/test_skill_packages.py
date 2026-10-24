"""Skill packages on disk: reading without executing, validation, identity, versions."""
from __future__ import annotations

import json
import os
import stat
import sys

import pytest

from kernel.plugins.host import RunnerStartError, check_description
from kernel.plugins.manifest import PLATFORMS, ManifestError, load_manifest, package_digest, parse_manifest
from kernel.plugins.registry import RegistryError, SkillRegistry
from kernel.tests.skill_fixtures import ECHO_TOOL, install, write_skill


def _registry(tmp_path):
    return SkillRegistry(tmp_path / "config" / "plugins" / "skill")


def test_listing_the_catalog_never_imports_plugin_code(tmp_path):
    marker = tmp_path / "imported"
    source = write_skill(tmp_path / "src", plugin=f"""
open({str(marker)!r}, "w").write("ran")
def describe():
    return {{"tools": ["say"]}}
def execute(tool_name, arguments, context):
    return {{}}
""")
    registry = _registry(tmp_path)
    install(registry, source)

    entries = registry.catalog()

    assert [entry.id for entry in entries] == ["echo"]
    assert entries[0].manifest is not None
    assert not marker.exists()


@pytest.mark.parametrize(
    ("change", "reason"),
    [
        ({"plugin_api": {"min": 2, "max": 3}}, "needs plugin API 2..3"),
        ({"platforms": [min(PLATFORMS - {sys.platform})]}, "not supported on"),
    ],
)
def test_an_incompatible_package_is_listed_as_unavailable_with_the_reason(tmp_path, change, reason):
    registry = _registry(tmp_path)
    install(registry, write_skill(tmp_path / "src", extra=change))

    entry = registry.entry("echo")

    assert entry is not None and not entry.available
    assert reason in entry.reason


def test_a_damaged_manifest_is_refused_with_its_error(tmp_path):
    source = write_skill(tmp_path / "src")
    (source / "manifest.json").write_text("{not json", encoding="utf-8")

    with pytest.raises(ManifestError, match="not valid JSON"):
        load_manifest(source)


def test_unknown_fields_and_capabilities_are_refused():
    base = {"manifest_version": 1, "id": "x1", "version": "1.0.0", "plugin_api": 1, "kind": "instructions"}
    with pytest.raises(ManifestError, match="unknown fields"):
        parse_manifest({**base, "run_as_root": True})
    with pytest.raises(ManifestError, match="unknown capability"):
        parse_manifest({**base, "capabilities": {"required": ["root.everything"]}})


@pytest.mark.parametrize("skill_id", ["mcp", "read", "Browser", "a__b", "x"])
def test_ids_that_could_collide_or_break_wire_names_are_refused(skill_id):
    data = {"manifest_version": 1, "id": skill_id, "version": "1.0.0", "plugin_api": 1, "kind": "instructions"}
    if skill_id == "read":
        # "read" is a valid id: its wire names ("read__file") can never equal a
        # built-in action ("read_file"), because built-ins have no "__".
        assert parse_manifest(data).id == "read"
        return
    with pytest.raises(ManifestError):
        parse_manifest(data)


def test_tool_schemas_may_only_use_the_supported_keywords():
    data = {"manifest_version": 1, "id": "x1", "version": "1.0.0", "plugin_api": 1, "kind": "tools",
            "tools": [{**ECHO_TOOL, "parameters": {"type": "object", "properties": {}, "patternProperties": {}}}]}
    with pytest.raises(ManifestError, match="unsupported keywords"):
        parse_manifest(data)


def test_a_package_with_a_symlink_is_refused(tmp_path):
    source = write_skill(tmp_path / "src")
    os.symlink("/etc/passwd", source / "passwd")

    with pytest.raises(ManifestError, match="symbolic link"):
        _registry(tmp_path).install(source)


def test_describe_may_narrow_but_never_widen_the_manifest(tmp_path):
    manifest = load_manifest(write_skill(tmp_path / "src"))

    assert set(check_description(manifest, {"tools": ["say"]})) == {"say"}
    assert check_description(manifest, {"tools": []}) == {}
    with pytest.raises(RunnerStartError, match="does not declare"):
        check_description(manifest, {"tools": ["say", "delete_everything"]})
    with pytest.raises(RunnerStartError, match="changes the parameters"):
        check_description(manifest, {"tools": [{"name": "say", "parameters": {"type": "object"}}]})
    with pytest.raises(RunnerStartError, match="read-only"):
        check_description(manifest, {"tools": [{"name": "say", "readonly": False}]})
    with pytest.raises(RunnerStartError, match="does not accept"):
        check_description(manifest, {"tools": ["say"], "capabilities": ["files.write"]})


def test_installed_files_changed_afterwards_make_the_skill_unavailable(tmp_path):
    registry = _registry(tmp_path)
    install(registry, write_skill(tmp_path / "src"))
    plugin = registry.root / "echo" / "versions" / "0.1.0" / "plugin.py"
    os.chmod(plugin, stat.S_IRUSR | stat.S_IWUSR)
    plugin.write_text("def describe():\n    return {'tools': ['say']}\n", encoding="utf-8")

    entry = registry.entry("echo")

    assert not entry.available
    assert "changed after installation" in entry.reason


def test_installed_versions_are_immutable_and_activation_is_separate(tmp_path):
    registry = _registry(tmp_path)
    first = install(registry, write_skill(tmp_path / "v1"))
    changed = write_skill(tmp_path / "v1b", instructions="# Different\n")

    with pytest.raises(RegistryError, match="never change"):
        registry.install(changed)
    second = registry.install(write_skill(tmp_path / "v2", version="0.2.0"))

    assert registry.entry("echo").version == "0.1.0"
    registry.activate("echo", "0.2.0", second.digest)
    assert registry.entry("echo").version == "0.2.0"
    assert registry.rollback("echo") == "0.1.0"
    assert registry.entry("echo").digest == first.digest


def test_a_crash_after_copying_leaves_the_active_version_working(tmp_path, monkeypatch):
    registry = _registry(tmp_path)
    # Instructions only: a tools skill is unavailable wherever the runner is missing, so `available` would say
    # nothing about the install there.
    install(registry, write_skill(tmp_path / "v1", kind="instructions"))

    def crash(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("kernel.plugins.registry.os.rename", crash)
    with pytest.raises(OSError):
        registry.install(write_skill(tmp_path / "v2", version="0.2.0", kind="instructions"))

    entry = registry.entry("echo")
    assert entry.available and entry.version == "0.1.0"
    assert not list((registry.root / "echo").glob(".staging-*"))


def test_the_active_version_cannot_be_removed(tmp_path):
    registry = _registry(tmp_path)
    install(registry, write_skill(tmp_path / "v1"))

    with pytest.raises(RegistryError, match="active version"):
        registry.remove_version("echo", "0.1.0")


def test_digest_covers_every_file(tmp_path):
    source = write_skill(tmp_path / "src")
    before = package_digest(source)
    (source / "notes.txt").write_text("x", encoding="utf-8")

    assert package_digest(source) != before
    assert json.loads((source / "manifest.json").read_text())["id"] == "echo"
