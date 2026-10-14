# -*- coding: utf-8 -*-
"""Installed skills on disk: the catalog, immutable versions and the active pointer.

Layout under ``<config_dir>/plugins/skill/<id>/``::

    versions/<version>/   one installed version; read-only once installed
    active.json           {"version", "digest", "previous", "installed": {...}}
    env/<key>/            the managed Python environment of pinned dependencies

Installing copies a package into a private staging directory, computes its
digest there and renames it into ``versions/`` — a crash at any point leaves
the active version untouched, and activation is a separate atomic replace of
``active.json``. Nothing here imports a package's code.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.plugins.manifest import (
    KIND_TOOLS,
    MAX_PACKAGE_BYTES,
    SKILL_ID_RE,
    Manifest,
    ManifestError,
    load_manifest,
    package_digest,
    package_files,
    read_package_file,
)
from kernel.plugins.paths import skills_root

ACTIVE_FILE = "active.json"
VERSIONS_DIR = "versions"
ENV_DIR = "env"
REFERENCES_DIR = "references"
_SYSTEM_PATH = os.pathsep.join(p for p in ("/usr/local/bin", "/usr/bin", "/bin", "/usr/sbin", "/sbin") if Path(p).is_dir())


def reference_path(name: str) -> str:
    """``terminal`` and ``terminal.md`` both mean ``references/terminal.md``: a near miss should not cost a step."""
    name = name.strip()
    if "/" not in name:
        name = f"{REFERENCES_DIR}/{name}"
    return name if name.endswith(".md") else f"{name}.md"


class RegistryError(RuntimeError):
    """An install, activation or removal that could not be done, with the reason."""


@dataclass(frozen=True, slots=True)
class CatalogEntry:
    id: str
    version: str
    digest: str
    available: bool
    reason: str
    manifest: Manifest | None
    version_dir: Path | None

    def label(self, language: str) -> str:
        return self.manifest.label(language) if self.manifest else self.id

    def short(self, language: str) -> str:
        return self.manifest.short(language) if self.manifest else ""


@dataclass(frozen=True, slots=True)
class InstallResult:
    skill_id: str
    version: str
    digest: str
    created: bool


class SkillRegistry:
    def __init__(self, root: Path | None = None) -> None:
        self.root = Path(root) if root is not None else skills_root()

    # --- reading ---------------------------------------------------------

    def catalog(self) -> list[CatalogEntry]:
        if not self.root.is_dir():
            return []
        entries: list[CatalogEntry] = []
        for child in sorted(self.root.iterdir(), key=lambda item: item.name):
            if child.name.startswith(".") or not child.is_dir() or child.is_symlink():
                continue
            entries.append(self.entry(child.name))
        return [entry for entry in entries if entry is not None]

    def entry(self, skill_id: str) -> CatalogEntry | None:
        if not SKILL_ID_RE.match(skill_id):
            return None
        folder = self.root / skill_id
        if not folder.is_dir() or folder.is_symlink():
            return None
        pointer = self._active(skill_id)
        version, digest = pointer.get("version"), pointer.get("digest")
        if not isinstance(version, str) or not isinstance(digest, str):
            return CatalogEntry(skill_id, "", "", False, "no active version (install or activate one)", None, None)
        version_dir = folder / VERSIONS_DIR / version
        try:
            manifest = load_manifest(version_dir)
        except ManifestError as error:
            return CatalogEntry(skill_id, version, digest, False, f"invalid package: {error}", None, version_dir)
        if manifest.id != skill_id or manifest.version != version:
            return CatalogEntry(skill_id, version, digest, False, "manifest id/version do not match the folder", manifest, version_dir)
        try:
            actual = package_digest(version_dir)
        except ManifestError as error:
            return CatalogEntry(skill_id, version, digest, False, f"invalid package: {error}", manifest, version_dir)
        if actual != digest:
            return CatalogEntry(
                skill_id, version, digest, False,
                "the installed files changed after installation; reinstall the skill", manifest, version_dir,
            )
        reason = self.unavailable_reason(manifest)
        return CatalogEntry(skill_id, version, digest, not reason, reason, manifest, version_dir)

    def instructions(self, entry: CatalogEntry, max_bytes: int) -> str:
        assert entry.manifest is not None and entry.version_dir is not None
        data = read_package_file(entry.version_dir, entry.manifest.instructions, max_bytes)
        return data.decode("utf-8", errors="replace")

    def references(self, entry: CatalogEntry) -> list[str]:
        """The pages a skill ships beside its instructions (``references/*.md``), read only on request."""
        assert entry.version_dir is not None
        folder = entry.version_dir / REFERENCES_DIR
        if folder.is_symlink() or not folder.is_dir():
            return []
        with os.scandir(folder) as entries:
            return sorted(f"{REFERENCES_DIR}/{item.name}" for item in entries
                          if item.name.endswith(".md") and item.is_file(follow_symlinks=False))

    def reference(self, entry: CatalogEntry, name: str, max_bytes: int) -> str:
        """One page by its listed path (see ``reference_path`` for the short forms accepted)."""
        assert entry.version_dir is not None
        available = self.references(entry)
        name = reference_path(name)
        if name not in available:
            listed = ", ".join(available) or "none"
            raise RegistryError(f"{entry.id} has no reference page {name!r}; its pages: {listed}")
        return read_package_file(entry.version_dir, name, max_bytes).decode("utf-8", errors="replace")

    def versions(self, skill_id: str) -> dict[str, Any]:
        return dict(self._active(skill_id).get("installed") or {})

    def active_version(self, skill_id: str) -> str | None:
        version = self._active(skill_id).get("version")
        return version if isinstance(version, str) else None

    def unavailable_reason(self, manifest: Manifest) -> str:
        """Why the skill cannot be attached on this machine; empty when it can."""
        if not manifest.api_compatible():
            return (f"needs plugin API {manifest.api_min}..{manifest.api_max}, "
                    f"this Kvist provides {_api_version()}")
        if sys.platform not in manifest.platforms:
            return f"not supported on {sys.platform} (supports {', '.join(manifest.platforms)})"
        if manifest.kind != KIND_TOOLS:
            return ""
        from kernel.plugins.sandbox import runner_unavailable_reason

        reason = runner_unavailable_reason()
        if reason:
            return reason
        missing = [name for name in manifest.system_deps if not shutil.which(name, path=_SYSTEM_PATH)]
        if missing:
            return f"missing system programs: {', '.join(missing)}"
        if manifest.python_deps and not self.env_ready(manifest):
            return (f"Python dependencies are not installed; run: kvist --skill-op install-deps {manifest.id}")
        return ""

    # --- environments ----------------------------------------------------

    def env_dir(self, manifest: Manifest) -> Path:
        return self.root / manifest.id / ENV_DIR / env_key(manifest.python_deps)

    def env_ready(self, manifest: Manifest) -> bool:
        marker = self.env_dir(manifest).with_suffix(".ready.json")
        if not marker.is_file():
            return False
        try:
            data = json.loads(marker.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return False
        return data.get("pins") == list(manifest.python_deps)

    # --- changing --------------------------------------------------------

    def install(self, source: Path, *, origin: str = "local") -> InstallResult:
        """Copy a package into ``versions/`` without activating it."""
        source = Path(source).resolve(strict=True)
        manifest = load_manifest(source)
        folder = self.root / manifest.id
        (folder / VERSIONS_DIR).mkdir(parents=True, exist_ok=True, mode=0o700)
        target = folder / VERSIONS_DIR / manifest.version
        staging = folder / f".staging-{uuid.uuid4().hex[:12]}"
        try:
            _copy_package(source, staging)
            staged_manifest = load_manifest(staging)
            digest = package_digest(staging)
            if staged_manifest.raw != manifest.raw:
                raise RegistryError("the package changed while it was being copied")
            if target.exists():
                existing = package_digest(target)
                if existing != digest:
                    raise RegistryError(
                        f"version {manifest.version} is already installed with different content; "
                        "installed versions never change — raise the version number"
                    )
                return InstallResult(manifest.id, manifest.version, digest, created=False)
            os.rename(staging, target)
            _make_read_only(target)
        finally:
            if staging.exists():
                _remove_tree(staging)
        self._update_active(manifest.id, lambda doc: _record_install(doc, manifest.version, digest, origin, source))
        return InstallResult(manifest.id, manifest.version, digest, created=True)

    def activate(self, skill_id: str, version: str, expected_digest: str) -> None:
        """Point the skill at an installed version whose content still has ``expected_digest``."""
        version_dir = self.root / skill_id / VERSIONS_DIR / version
        if not version_dir.is_dir():
            raise RegistryError(f"{skill_id} {version} is not installed")
        if package_digest(version_dir) != expected_digest:
            raise RegistryError(f"{skill_id} {version} changed since it was checked; it was not activated")

        def update(document: dict[str, Any]) -> dict[str, Any]:
            current = {"version": document.get("version"), "digest": document.get("digest")}
            if current["version"] and current["version"] != version:
                document["previous"] = current
            document["version"] = version
            document["digest"] = expected_digest
            document["activated_at"] = time.time()
            return document

        self._update_active(skill_id, update)

    def rollback(self, skill_id: str) -> str:
        """Activate the previous working version; returns its number."""
        previous = self._active(skill_id).get("previous")
        if not isinstance(previous, dict) or not previous.get("version"):
            raise RegistryError(f"{skill_id} has no previous version to return to")
        self.activate(skill_id, str(previous["version"]), str(previous["digest"]))
        return str(previous["version"])

    def uninstall(self, skill_id: str) -> None:
        if not SKILL_ID_RE.match(skill_id):
            raise RegistryError(f"invalid skill id {skill_id!r}")
        folder = self.root / skill_id
        if not folder.is_dir():
            raise RegistryError(f"{skill_id} is not installed")
        _remove_tree(folder)

    def remove_version(self, skill_id: str, version: str) -> None:
        """Delete one installed version; never the active one, so a working version always remains."""
        if self._active(skill_id).get("version") == version:
            raise RegistryError("the active version cannot be removed; activate another one first")
        target = self.root / skill_id / VERSIONS_DIR / version
        if not target.is_dir():
            raise RegistryError(f"{skill_id} {version} is not installed")
        _remove_tree(target)
        self._update_active(skill_id, lambda doc: _forget_version(doc, version))

    def _active(self, skill_id: str) -> dict[str, Any]:
        path = self.root / skill_id / ACTIVE_FILE
        if not path.is_file():
            return {}
        try:
            return read_json_object(path, boundary=self.root, empty_as_missing=True)
        except JsonStorageError:
            # A damaged pointer and its backup read as "no active version": the
            # catalog then says so instead of guessing which version was meant.
            return {}

    def _update_active(self, skill_id: str, update) -> None:
        update_json_object(self.root / skill_id / ACTIVE_FILE, update, boundary=self.root, empty_as_missing=True)


def env_key(pins: tuple[str, ...]) -> str:
    material = json.dumps({"pins": sorted(pins), "python": _runner_python_tag()}, sort_keys=True)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


def _runner_python_tag() -> str:
    from kernel.plugins.sandbox import runner_python_version

    return runner_python_version() or "unknown"


def _api_version() -> int:
    from kernel.plugins.manifest import PLUGIN_API_VERSION

    return PLUGIN_API_VERSION


def _record_install(document: dict[str, Any], version: str, digest: str, origin: str, source: Path) -> dict[str, Any]:
    document.setdefault("installed", {})[version] = {
        "digest": digest,
        "installed_at": time.time(),
        "origin": origin,
        "source": str(source),
    }
    return document


def _forget_version(document: dict[str, Any], version: str) -> dict[str, Any]:
    document.get("installed", {}).pop(version, None)
    previous = document.get("previous")
    if isinstance(previous, dict) and previous.get("version") == version:
        document["previous"] = None
    return document


def _copy_package(source: Path, target: Path) -> None:
    target.mkdir(mode=0o700)
    for relative, _path in package_files(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = read_package_file(source, relative, MAX_PACKAGE_BYTES)
        descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        try:
            view = memoryview(data)
            while view:
                view = view[os.write(descriptor, view):]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)


def _make_read_only(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=False):
        for name in files:
            os.chmod(os.path.join(current, name), stat.S_IRUSR | stat.S_IRGRP)
        os.chmod(current, stat.S_IRUSR | stat.S_IXUSR)
    os.chmod(root, stat.S_IRUSR | stat.S_IXUSR)


def _remove_tree(root: Path) -> None:
    for current, directories, _files in os.walk(root):
        os.chmod(current, stat.S_IRWXU)
        for name in directories:
            path = os.path.join(current, name)
            if not os.path.islink(path):
                os.chmod(path, stat.S_IRWXU)
    shutil.rmtree(root)
