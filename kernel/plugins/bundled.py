# -*- coding: utf-8 -*-
"""Skills shipped with Kvist are preinstalled: installed at startup, kept current, and left alone when removed.

A bundled skill goes through the same atomic install as one the person adds
(``SkillRegistry.install`` with origin ``bundled``), so it is listed, attached,
approved and rolled back like any other. At every start ``sync_bundled``:

- installs and activates a bundled skill that is not installed;
- installs and activates a newer bundled version when the active one came from
  Kvist and that version was never installed (a version the person rolled back
  from is already installed, so a rollback is not undone);
- leaves alone a skill whose active version the person installed from elsewhere,
  and a skill the person uninstalled (recorded in ``bundled.json``) until they
  install it again with ``--skill-op install --skill-path bundled:<id>``.

Installing never grants anything: a bundled skill with tools still asks the
person on first attach, and its Python dependencies still need ``install-deps``.
"""
from __future__ import annotations

from pathlib import Path

from kernel.session.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.plugins.manifest import ManifestError, load_manifest
from kernel.plugins.registry import RegistryError, SkillRegistry

BUNDLED_ROOT = Path(__file__).resolve().parent.parent / "skills_bundled"
DECLINED_FILE = "bundled.json"


def bundled_sources() -> list[Path]:
    if not BUNDLED_ROOT.is_dir():
        return []
    return sorted(path for path in BUNDLED_ROOT.iterdir() if (path / "manifest.json").is_file())


def sync_bundled(registry: SkillRegistry) -> list[str]:
    """Bring the bundled skills into ``registry``; returns one problem line per skill that could not be."""
    problems: list[str] = []
    try:
        declined = _declined(registry)
    except JsonStorageError as error:
        # Without the list we cannot tell which skills the person removed, so none is put back.
        return [f"Bundled skills were not checked: {DECLINED_FILE} cannot be read ({error})"]
    for source in bundled_sources():
        try:
            manifest = load_manifest(source)
            if manifest.id in declined or not _wants(registry, manifest.id, manifest.version):
                continue
            result = registry.install(source, origin="bundled")
            registry.activate(result.skill_id, result.version, result.digest)
        except (ManifestError, RegistryError, JsonStorageError, OSError) as error:
            problems.append(f"Bundled skill {source.name} was not installed: {error}")
    return problems


def _wants(registry: SkillRegistry, skill_id: str, version: str) -> bool:
    installed = registry.versions(skill_id)
    if not installed:
        return True
    active = installed.get(str(registry.active_version(skill_id) or ""), {})
    return active.get("origin") == "bundled" and version not in installed


def decline(registry: SkillRegistry, skill_id: str) -> None:
    """The person uninstalled a bundled skill: do not put it back at the next start."""
    registry.root.mkdir(parents=True, exist_ok=True, mode=0o700)
    update_json_object(_declined_path(registry), lambda doc: _with_declined(doc, skill_id, True),
                       boundary=registry.root, empty_as_missing=True)


def restore(registry: SkillRegistry, skill_id: str) -> None:
    """The person installed a bundled skill again: keep it current from now on."""
    if skill_id in _declined(registry):
        update_json_object(_declined_path(registry), lambda doc: _with_declined(doc, skill_id, False),
                           boundary=registry.root, empty_as_missing=True)


def _with_declined(document: dict, skill_id: str, declined: bool) -> dict:
    names = set(document.get("declined") or [])
    document["declined"] = sorted(names | {skill_id} if declined else names - {skill_id})
    return document


def _declined(registry: SkillRegistry) -> set[str]:
    path = _declined_path(registry)
    if not path.is_file():
        return set()
    document = read_json_object(path, boundary=registry.root, empty_as_missing=True)
    return {str(item) for item in document.get("declined") or []}


def _declined_path(registry: SkillRegistry) -> Path:
    return registry.root / DECLINED_FILE
