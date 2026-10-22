# -*- coding: utf-8 -*-
"""Preinstalled skills: installed at startup, upgraded with Kvist, never forced back on a person who removed them."""
from __future__ import annotations

import pytest

from kernel.plugins import skill_ops
from kernel.plugins import bundled
from kernel.plugins.registry import SkillRegistry
from kernel.tests.skill_fixtures import write_skill


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(path))
    return path


@pytest.fixture
def registry(config_dir):
    return SkillRegistry(config_dir / "plugins" / "skill")


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """A bundled folder with one instruction skill whose version a test can raise."""
    root = tmp_path / "shipped"
    root.mkdir()
    monkeypatch.setattr(bundled, "BUNDLED_ROOT", root)

    def ship(version: str, text: str = "# Guide\n") -> None:
        for old in root.iterdir():
            for item in old.iterdir():
                item.unlink()
            old.rmdir()
        folder = write_skill(tmp_path / f"build-{version}", "guide", version=version, kind="instructions",
                             instructions=text)
        folder.rename(root / "guide")

    return ship


def test_every_skill_kvist_ships_is_installed_and_active_on_a_fresh_machine(registry):
    expected = {path.name for path in bundled.bundled_sources()}

    assert bundled.sync_bundled(registry) == []

    entries = {entry.id: entry for entry in registry.catalog()}
    assert set(entries) == expected and {"design", "debugging", "browser"} <= expected
    for skill_id in expected:
        record = registry.versions(skill_id)[registry.active_version(skill_id)]
        assert record["origin"] == "bundled"
    assert bundled.sync_bundled(registry) == []  # a second start changes nothing


def test_a_changed_bundled_skill_has_a_new_version():
    """An installed version is never reinstalled, so new content under an old version would reach nobody."""
    import json
    from pathlib import Path

    from kernel.plugins.manifest import load_manifest, package_digest

    recorded = json.loads((Path(__file__).parent / "bundled_skill_digests.json").read_text(encoding="utf-8"))
    current = {source.name: {"version": load_manifest(source).version, "digest": package_digest(source)}
               for source in bundled.bundled_sources()}

    stale = sorted(skill_id for skill_id, row in current.items()
                   if skill_id in recorded and row["version"] == recorded[skill_id]["version"]
                   and row["digest"] != recorded[skill_id]["digest"])
    assert not stale, (f"content of {', '.join(stale)} changed under the same version: raise the version in its "
                       "manifest.json, then record the new version and digest in bundled_skill_digests.json")
    assert current == recorded, "record the new versions and digests in kernel/tests/bundled_skill_digests.json"


def test_a_newer_shipped_version_replaces_the_shipped_one_but_a_rollback_is_kept(registry, shipped):
    shipped("0.1.0")
    bundled.sync_bundled(registry)
    shipped("0.2.0", "# Guide v2\n")

    bundled.sync_bundled(registry)
    upgraded = registry.active_version("guide")
    registry.rollback("guide")
    bundled.sync_bundled(registry)

    assert upgraded == "0.2.0"
    assert registry.active_version("guide") == "0.1.0"


def test_a_skill_the_person_installed_under_the_same_id_is_left_alone(registry, shipped, tmp_path):
    own = write_skill(tmp_path / "own", "guide", version="0.5.0", kind="instructions", instructions="# Mine\n")
    result = registry.install(own, origin="local")
    registry.activate(result.skill_id, result.version, result.digest)
    shipped("0.9.0")

    assert bundled.sync_bundled(registry) == []
    assert registry.active_version("guide") == "0.5.0" and "0.9.0" not in registry.versions("guide")


def test_an_uninstalled_shipped_skill_stays_removed_until_installed_again(config_dir, registry, shipped, tmp_path):
    shipped("0.1.0")
    bundled.sync_bundled(registry)

    removed = skill_ops.run_skill_op("uninstall", workspace=tmp_path, skill="guide", path=None, version=None,
                                     assume_yes=True)
    after_restart = bundled.sync_bundled(registry)
    still_gone = registry.entry("guide")
    skill_ops.run_skill_op("install", workspace=tmp_path, skill=None, path="bundled:guide", version=None,
                           assume_yes=True)
    shipped("0.2.0", "# Guide v2\n")
    bundled.sync_bundled(registry)

    assert "will not be installed again at startup" in removed["note"]
    assert after_restart == [] and still_gone is None
    assert registry.active_version("guide") == "0.2.0"  # kept current again once brought back


def test_the_model_is_not_shown_skills_this_os_can_never_run(registry, monkeypatch):
    from types import SimpleNamespace

    from kernel.plugins.skills import SkillManager

    bundled.sync_bundled(registry)
    manager = SkillManager(SimpleNamespace(executor=SimpleNamespace(workspace=str(registry.root))), registry=registry)
    monkeypatch.setattr("kernel.plugins.skills.sys.platform", "darwin")

    shown = manager.prompt_section("en")

    assert "- design (instructions only)" in shown and "- debugging (instructions only)" in shown
    assert "- browser (" not in shown and "not supported" not in shown
    assert "- desktop (5 tools)" in shown
    assert any(row["id"] == "browser" for row in manager.list_page("", 1, "en")["skills"])


def test_an_unreadable_list_of_removed_skills_installs_nothing(registry, shipped):
    shipped("0.1.0")
    registry.root.mkdir(parents=True)
    (registry.root / bundled.DECLINED_FILE).write_text("{not json", encoding="utf-8")

    problems = bundled.sync_bundled(registry)

    assert problems and "cannot be read" in problems[0]
    assert registry.entry("guide") is None
