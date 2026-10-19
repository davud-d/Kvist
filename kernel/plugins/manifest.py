# -*- coding: utf-8 -*-
"""The static skill manifest: parsing, validation and the package's content identity.

Everything here reads files and nothing executes them: listing installed
skills must never import ``plugin.py``. Package files are read without
following symlinks and under size limits, so a hostile package can neither
point the core at another file nor make it read without bound.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kernel.plugins.capabilities import CAPABILITIES
from kernel.plugins.schema import SchemaError, check_schema

MANIFEST_NAME = "manifest.json"
MANIFEST_VERSION = 1
PLUGIN_API_VERSION = 1

KIND_INSTRUCTIONS = "instructions"
KIND_TOOLS = "tools"

MAX_MANIFEST_BYTES = 64 * 1024
MAX_INSTRUCTIONS_BYTES = 64 * 1024
MAX_PACKAGE_FILES = 500
MAX_PACKAGE_BYTES = 20 * 1024 * 1024
MAX_TOOLS = 40
MAX_SUMMARY_CHARS = 300

# Ids and tool names become provider wire names (``<id>__<tool>``), so they keep
# to characters every provider accepts and never contain the "__" separator.
SKILL_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}[a-z0-9]$")
TOOL_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,38}[a-z0-9]$|^[a-z]$")
VERSION_RE = re.compile(r"^\d{1,4}\.\d{1,4}\.\d{1,6}(?:[-+][0-9A-Za-z.-]{1,40})?$")
PIN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,80}==[0-9][0-9A-Za-z.!+-]{0,40}$")
SYSTEM_DEP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+-]{0,60}$")
RESERVED_IDS = frozenset({"mcp", "kvist", "core", "skill", "skills", "builtin", "system", "memory", "helper"})
PLATFORMS = frozenset({"linux", "darwin", "win32"})
_SKIPPED_NAMES = frozenset({"__pycache__", ".DS_Store"})

_LIMIT_BOUNDS: dict[str, tuple[float, float, float]] = {
    # name: (default, minimum, maximum)
    "call_timeout_seconds": (120, 1, 1800),
    "max_result_bytes": (256 * 1024, 1024, 1024 * 1024),
    "memory_mb": (2048, 64, 16384),
    "model_calls_per_task": (20, 0, 500),
    "model_tokens_per_task": (200_000, 0, 5_000_000),
    "max_runtime_seconds": (3600, 10, 6 * 3600),
}
_TOP_LEVEL_KEYS = frozenset({
    "manifest_version", "id", "version", "plugin_api", "kind", "name", "summary", "instructions",
    "entrypoint", "platforms", "dependencies", "capabilities", "limits", "tools", "source", "settings", "core_group",
})
# Core tool groups a bundled skill may name: its tools are run by the core itself (CoreServices methods
# "<group>.<tool>"), so there is no package code and no sandbox. Never available to a package the person installs.
CORE_GROUPS = frozenset({"host"})
_TOOL_KEYS = frozenset({"name", "summary", "parameters", "result", "readonly", "timeout_seconds", "max_result_bytes"})


class ManifestError(ValueError):
    """A package that cannot be used, with the reason a person can act on."""


@dataclass(frozen=True, slots=True)
class ToolDef:
    name: str
    summary: str
    parameters: dict[str, Any]
    result: dict[str, Any] | None
    readonly: bool
    timeout_seconds: float
    max_result_bytes: int


@dataclass(frozen=True, slots=True)
class CapabilityRequest:
    name: str
    scope: dict[str, Any]
    reason: str
    optional: bool


@dataclass(frozen=True, slots=True)
class Manifest:
    id: str
    version: str
    api_min: int
    api_max: int
    kind: str
    name: dict[str, str]
    summary: dict[str, str]
    instructions: str
    entrypoint: str
    platforms: tuple[str, ...]
    python_deps: tuple[str, ...]
    system_deps: tuple[str, ...]
    capabilities: tuple[CapabilityRequest, ...]
    limits: dict[str, float]
    tools: tuple[ToolDef, ...]
    source: dict[str, str]
    settings: dict[str, Any] | None
    core_group: str = ""
    raw: dict[str, Any] = field(repr=False, compare=False, default_factory=dict)

    def label(self, language: str) -> str:
        return self.name.get(language) or self.name.get("en") or self.id

    def short(self, language: str) -> str:
        return self.summary.get(language) or self.summary.get("en") or ""

    def tool(self, name: str) -> ToolDef | None:
        return next((tool for tool in self.tools if tool.name == name), None)

    def required_capabilities(self) -> dict[str, dict[str, Any]]:
        return {item.name: item.scope for item in self.capabilities if not item.optional}

    def api_compatible(self) -> bool:
        return self.api_min <= PLUGIN_API_VERSION <= self.api_max


def load_manifest(version_dir: Path) -> Manifest:
    """Parse the manifest of one package directory without touching any other file."""
    raw = read_package_file(version_dir, MANIFEST_NAME, MAX_MANIFEST_BYTES)
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise ManifestError(f"manifest.json is not valid JSON: {error}") from None
    manifest = parse_manifest(data)
    read_package_file(version_dir, manifest.instructions, MAX_INSTRUCTIONS_BYTES)
    if manifest.kind == KIND_TOOLS and not manifest.core_group:
        read_package_file(version_dir, manifest.entrypoint, MAX_PACKAGE_BYTES)
    return manifest


def parse_manifest(data: Any) -> Manifest:
    if not isinstance(data, dict):
        raise ManifestError("manifest.json must hold one JSON object")
    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise ManifestError(f"manifest.json has unknown fields: {sorted(unknown)}")
    if data.get("manifest_version") != MANIFEST_VERSION:
        raise ManifestError(f"manifest_version must be {MANIFEST_VERSION}")
    skill_id = _require_str(data, "id")
    if not SKILL_ID_RE.match(skill_id) or "__" in skill_id or skill_id in RESERVED_IDS:
        raise ManifestError(
            f"id {skill_id!r} must be 2-32 lowercase letters, digits or single underscores, "
            "start with a letter and not be a reserved name"
        )
    version = _require_str(data, "version")
    if not VERSION_RE.match(version):
        raise ManifestError(f"version {version!r} must look like 1.2.3")
    api_min, api_max = _plugin_api(data.get("plugin_api"))
    kind = data.get("kind")
    if kind not in (KIND_INSTRUCTIONS, KIND_TOOLS):
        raise ManifestError("kind must be 'instructions' or 'tools'")
    instructions = _relative_file(data.get("instructions", "SKILL.md"), "instructions")
    core_group = data.get("core_group", "")
    if core_group and (kind != KIND_TOOLS or core_group not in CORE_GROUPS or data.get("dependencies")):
        raise ManifestError(f"core_group must be one of {sorted(CORE_GROUPS)} on a tools skill without dependencies")
    entrypoint = ""
    if kind == KIND_TOOLS and not core_group:
        entrypoint = _relative_file(data.get("entrypoint", "plugin.py"), "entrypoint")
        if not entrypoint.endswith(".py"):
            raise ManifestError("entrypoint must be a .py file")
    tools = _tools(data.get("tools", []), kind)
    return Manifest(
        id=skill_id,
        version=version,
        api_min=api_min,
        api_max=api_max,
        kind=kind,
        name=_localized(data.get("name"), "name", 80) or {"en": skill_id},
        summary=_localized(data.get("summary"), "summary", MAX_SUMMARY_CHARS),
        instructions=instructions,
        entrypoint=entrypoint,
        platforms=_platforms(data.get("platforms")),
        python_deps=_python_deps(data.get("dependencies")),
        system_deps=_system_deps(data.get("dependencies")),
        capabilities=_capabilities(data.get("capabilities")),
        limits=_limits(data.get("limits")),
        tools=tools,
        source=_source(data.get("source")),
        settings=_settings(data.get("settings")),
        core_group=core_group,
        raw=data,
    )


def read_package_file(root: Path, relative: str, max_bytes: int) -> bytes:
    """One regular file of a package, opened without following any symlink."""
    parts = Path(relative).parts
    if not parts or any(part in ("", ".", "..") for part in parts) or Path(relative).is_absolute():
        raise ManifestError(f"{relative!r} is not a path inside the package")
    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        try:
            descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW, dir_fd=directory)
        except FileNotFoundError:
            raise ManifestError(f"the package has no {relative}") from None
        except OSError as error:
            raise ManifestError(f"{relative} cannot be read: {error.strerror}") from None
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode):
                raise ManifestError(f"{relative} is not a regular file")
            if info.st_size > max_bytes:
                raise ManifestError(f"{relative} is larger than {max_bytes} bytes")
            data = os.read(descriptor, max_bytes + 1)
            if len(data) > max_bytes:
                raise ManifestError(f"{relative} is larger than {max_bytes} bytes")
            return data
        finally:
            os.close(descriptor)
    except OSError as error:
        raise ManifestError(f"{relative} cannot be read: {error.strerror or error}") from None
    finally:
        os.close(directory)


def package_files(root: Path) -> list[tuple[str, Path]]:
    """Every file of a package as (relative path, path); refuses links and special files."""
    files: list[tuple[str, Path]] = []
    total = 0
    pending = [Path(root)]
    while pending:
        current = pending.pop()
        with os.scandir(current) as entries:
            for entry in sorted(entries, key=lambda item: item.name):
                if entry.name in _SKIPPED_NAMES or entry.name.endswith(".pyc"):
                    continue
                relative = Path(entry.path).relative_to(root).as_posix()
                if entry.is_symlink():
                    raise ManifestError(f"{relative} is a symbolic link; packages may not contain links")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    raise ManifestError(f"{relative} is not a regular file")
                info = entry.stat(follow_symlinks=False)
                if info.st_nlink > 1:
                    raise ManifestError(f"{relative} has other hard links; copy the file instead")
                total += info.st_size
                files.append((relative, Path(entry.path)))
                if len(files) > MAX_PACKAGE_FILES:
                    raise ManifestError(f"the package has more than {MAX_PACKAGE_FILES} files")
                if total > MAX_PACKAGE_BYTES:
                    raise ManifestError(f"the package is larger than {MAX_PACKAGE_BYTES} bytes")
    files.sort()
    return files


def package_digest(root: Path) -> str:
    """The content identity of a package: every file's path, size and SHA-256, in order."""
    outer = hashlib.sha256()
    for relative, path in package_files(root):
        data = read_package_file(root, relative, MAX_PACKAGE_BYTES)
        outer.update(relative.encode("utf-8") + b"\0")
        outer.update(str(len(data)).encode("ascii") + b"\0")
        outer.update(hashlib.sha256(data).digest())
    return "sha256:" + outer.hexdigest()


def _require_str(data: dict[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} must be a non-empty string")
    return value.strip()


def _plugin_api(value: Any) -> tuple[int, int]:
    if isinstance(value, int) and not isinstance(value, bool):
        return value, value
    if isinstance(value, dict) and set(value) <= {"min", "max"}:
        low, high = value.get("min"), value.get("max", value.get("min"))
        if all(isinstance(item, int) and not isinstance(item, bool) and item >= 1 for item in (low, high)) and low <= high:
            return low, high
    raise ManifestError("plugin_api must be an integer or {\"min\": N, \"max\": M}")


def _relative_file(value: Any, key: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{key} must name a file in the package")
    path = Path(value.strip())
    if path.is_absolute() or any(part in ("", ".", "..") for part in path.parts):
        raise ManifestError(f"{key} must be a path inside the package")
    return path.as_posix()


def _localized(value: Any, key: str, limit: int) -> dict[str, str]:
    if value is None:
        return {}
    if isinstance(value, str):
        value = {"en": value}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ManifestError(f"{key} must be a string or an object of language → string")
    cleaned = {lang: " ".join(text.split()) for lang, text in value.items() if text.strip()}
    if any(len(text) > limit for text in cleaned.values()):
        raise ManifestError(f"{key} is longer than {limit} characters")
    return cleaned


def _platforms(value: Any) -> tuple[str, ...]:
    if value is None:
        return tuple(sorted(PLATFORMS))
    if not isinstance(value, list) or not value or not set(value) <= PLATFORMS:
        raise ManifestError(f"platforms must be a non-empty list from {sorted(PLATFORMS)}")
    return tuple(value)


def _dependencies(value: Any) -> dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict) or set(value) - {"python", "system"}:
        raise ManifestError("dependencies may only have 'python' and 'system'")
    return value


def _python_deps(value: Any) -> tuple[str, ...]:
    pins = _dependencies(value).get("python", [])
    if not isinstance(pins, list) or not all(isinstance(pin, str) and PIN_RE.match(pin) for pin in pins):
        raise ManifestError("dependencies.python must list exact pins like 'name==1.2.3'")
    return tuple(pins)


def _system_deps(value: Any) -> tuple[str, ...]:
    names = _dependencies(value).get("system", [])
    if not isinstance(names, list) or not all(isinstance(n, str) and SYSTEM_DEP_RE.match(n) for n in names):
        raise ManifestError("dependencies.system must list executable names")
    return tuple(names)


def _capabilities(value: Any) -> tuple[CapabilityRequest, ...]:
    if value is None:
        return ()
    if not isinstance(value, dict) or set(value) - {"required", "optional"}:
        raise ManifestError("capabilities must be {\"required\": [...], \"optional\": [...]}")
    requests: list[CapabilityRequest] = []
    seen: set[str] = set()
    for optional, key in ((False, "required"), (True, "optional")):
        items = value.get(key, [])
        if not isinstance(items, list):
            raise ManifestError(f"capabilities.{key} must be a list")
        for item in items:
            request = _capability(item, optional)
            if request.name in seen:
                raise ManifestError(f"capability {request.name} is declared twice")
            seen.add(request.name)
            requests.append(request)
    return tuple(requests)


def _capability(item: Any, optional: bool) -> CapabilityRequest:
    if isinstance(item, str):
        item = {"name": item}
    if not isinstance(item, dict) or set(item) - {"name", "scope", "reason"}:
        raise ManifestError("each capability is a name or {\"name\", \"scope\", \"reason\"}")
    name = item.get("name")
    if name not in CAPABILITIES:
        raise ManifestError(f"unknown capability {name!r}")
    scope = item.get("scope", {})
    if not isinstance(scope, dict) or set(scope) - CAPABILITIES[name].scope_keys:
        allowed = sorted(CAPABILITIES[name].scope_keys) or "none"
        raise ManifestError(f"capability {name} scope may only have: {allowed}")
    for key, entries in scope.items():
        if key in ("domains", "paths", "connections", "tiers"):
            if not isinstance(entries, list) or not all(isinstance(e, str) and e.strip() for e in entries):
                raise ManifestError(f"capability {name} scope.{key} must be a list of strings")
        elif not (isinstance(entries, int) and not isinstance(entries, bool) and entries >= 0):
            raise ManifestError(f"capability {name} scope.{key} must be a non-negative integer")
    reason = item.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 300:
        raise ManifestError(f"capability {name} reason must be a string of at most 300 characters")
    return CapabilityRequest(name=name, scope=scope, reason=reason.strip(), optional=optional)


def _limits(value: Any) -> dict[str, float]:
    if value is None:
        value = {}
    if not isinstance(value, dict) or set(value) - set(_LIMIT_BOUNDS):
        raise ManifestError(f"limits may only have: {sorted(_LIMIT_BOUNDS)}")
    limits: dict[str, float] = {}
    for key, (default, low, high) in _LIMIT_BOUNDS.items():
        number = value.get(key, default)
        if isinstance(number, bool) or not isinstance(number, (int, float)) or not low <= number <= high:
            raise ManifestError(f"limits.{key} must be a number in {low}..{high}")
        limits[key] = number
    return limits


def _tools(value: Any, kind: str) -> tuple[ToolDef, ...]:
    if not isinstance(value, list):
        raise ManifestError("tools must be a list")
    if kind == KIND_INSTRUCTIONS and value:
        raise ManifestError("an instructions-only skill declares no tools")
    if kind == KIND_TOOLS and not value:
        raise ManifestError("a tools skill declares at least one tool")
    if len(value) > MAX_TOOLS:
        raise ManifestError(f"at most {MAX_TOOLS} tools")
    tools: list[ToolDef] = []
    for item in value:
        if not isinstance(item, dict) or set(item) - _TOOL_KEYS:
            raise ManifestError(f"each tool may only have: {sorted(_TOOL_KEYS)}")
        name = item.get("name")
        if not isinstance(name, str) or not TOOL_NAME_RE.match(name) or "__" in name:
            raise ManifestError(f"tool name {name!r} must be lowercase letters, digits and single underscores")
        if any(tool.name == name for tool in tools):
            raise ManifestError(f"tool {name} is declared twice")
        summary = item.get("summary")
        if not isinstance(summary, str) or not summary.strip() or len(summary) > 600:
            raise ManifestError(f"tool {name} needs a summary of at most 600 characters")
        parameters = item.get("parameters", {"type": "object", "properties": {}})
        result = item.get("result")
        try:
            check_schema(parameters, where=f"{name}.parameters")
            if result is not None:
                check_schema(result, where=f"{name}.result")
        except SchemaError as error:
            raise ManifestError(str(error)) from None
        if parameters.get("type") != "object":
            raise ManifestError(f"tool {name} parameters must be an object schema")
        timeout = item.get("timeout_seconds", _LIMIT_BOUNDS["call_timeout_seconds"][0])
        max_bytes = item.get("max_result_bytes", _LIMIT_BOUNDS["max_result_bytes"][0])
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 1 <= timeout <= 1800:
            raise ManifestError(f"tool {name} timeout_seconds must be in 1..1800")
        if isinstance(max_bytes, bool) or not isinstance(max_bytes, int) or not 1024 <= max_bytes <= 1024 * 1024:
            raise ManifestError(f"tool {name} max_result_bytes must be in 1024..1048576")
        readonly = item.get("readonly", False)
        if not isinstance(readonly, bool):
            raise ManifestError(f"tool {name} readonly must be true or false")
        tools.append(ToolDef(name, " ".join(summary.split()), parameters, result, readonly, float(timeout), max_bytes))
    return tuple(tools)


def _source(value: Any) -> dict[str, str]:
    if value is None:
        return {}
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ManifestError("source must be an object of strings (origin, url, revision)")
    if set(value) - {"origin", "url", "revision", "author"}:
        raise ManifestError("source may only have origin, url, revision, author")
    return dict(value)


def _settings(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    try:
        check_schema(value, where="settings")
    except SchemaError as error:
        raise ManifestError(str(error)) from None
    if value.get("type") != "object":
        raise ManifestError("settings must be an object schema")
    return value
