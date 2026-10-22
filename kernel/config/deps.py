# -*- coding: utf-8 -*-
from __future__ import annotations

import re
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:
    tomllib = None  # type: ignore[assignment]


def detect_dependency_versions(workspace: Path) -> dict[str, str]:
    versions: dict[str, str] = {}

    for parser in (
        lambda: _parse_requirements_versions(workspace),
        lambda: _parse_pyproject_versions(workspace),
        lambda: _parse_poetry_lock_versions(workspace),
        lambda: _parse_uv_lock_versions(workspace),
    ):
        try:
            for name, version in parser().items():
                if name and version:
                    versions[name] = version
        except Exception:
            continue

    return versions


def _normalize_package_name(name: str) -> str:
    return name.strip().lower().replace("_", "-")


def _parse_dependency_spec(raw: str) -> tuple[str, str]:
    pattern = re.compile(
        r"^\s*([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([=<>!~]{1,2})?\s*([^,;\s]+)?"
    )
    match = pattern.match(raw.strip())
    if not match:
        return "", ""

    name, operator, value = match.groups()
    normalized_name = _normalize_package_name(name)
    if operator and value:
        return normalized_name, f"{operator}{value}"
    return normalized_name, ""


def _parse_requirements_versions(workspace: Path) -> dict[str, str]:
    path = workspace / "requirements.txt"
    if not path.exists() or not path.is_file():
        return {}

    text = path.read_text(encoding="utf-8", errors="replace")
    versions: dict[str, str] = {}
    pattern = re.compile(r"^([A-Za-z0-9_.-]+)(?:\[[^\]]+\])?\s*([=<>!~]{1,2})\s*([^\s;]+)")
    for line in text.splitlines():
        clean = line.split("#", 1)[0].strip()
        if not clean:
            continue
        match = pattern.match(clean)
        if not match:
            continue
        name, operator, value = match.groups()
        versions[_normalize_package_name(name)] = f"{operator}{value}"

    return versions


def _parse_pyproject_versions(workspace: Path) -> dict[str, str]:
    path = workspace / "pyproject.toml"
    if not path.exists() or not path.is_file() or tomllib is None:
        return {}

    try:
        data = tomllib.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {}

    versions: dict[str, str] = {}

    project = data.get("project", {}) if isinstance(data, dict) else {}
    dependencies = project.get("dependencies", []) if isinstance(project, dict) else []
    if isinstance(dependencies, list):
        for item in dependencies:
            if not isinstance(item, str):
                continue
            name, spec = _parse_dependency_spec(item)
            if name and spec:
                versions[name] = spec

    optional = project.get("optional-dependencies", {}) if isinstance(project, dict) else {}
    if isinstance(optional, dict):
        for dep_list in optional.values():
            if not isinstance(dep_list, list):
                continue
            for item in dep_list:
                if not isinstance(item, str):
                    continue
                name, spec = _parse_dependency_spec(item)
                if name and spec:
                    versions[name] = spec

    tool = data.get("tool", {}) if isinstance(data, dict) else {}
    poetry = tool.get("poetry", {}) if isinstance(tool, dict) else {}
    poetry_dependencies = poetry.get("dependencies", {}) if isinstance(poetry, dict) else {}
    if isinstance(poetry_dependencies, dict):
        for raw_name, raw_spec in poetry_dependencies.items():
            name = _normalize_package_name(str(raw_name))
            if name == "python":
                continue
            if isinstance(raw_spec, str):
                spec = raw_spec.strip()
            elif isinstance(raw_spec, dict):
                spec = str(raw_spec.get("version", "")).strip()
            else:
                spec = ""
            if name and spec:
                versions[name] = spec

    return versions


def _parse_poetry_lock_versions(workspace: Path) -> dict[str, str]:
    path = workspace / "poetry.lock"
    if not path.exists() or not path.is_file():
        return {}

    text = path.read_text(encoding="utf-8", errors="replace")
    versions: dict[str, str] = {}
    blocks = text.split("[[package]]")
    for block in blocks:
        name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
        version_match = re.search(r"version\s*=\s*\"([^\"]+)\"", block)
        if not name_match or not version_match:
            continue
        name = _normalize_package_name(name_match.group(1))
        version = version_match.group(1).strip()
        if name and version:
            versions[name] = f"=={version}"

    return versions


def _parse_uv_lock_versions(workspace: Path) -> dict[str, str]:
    path = workspace / "uv.lock"
    if not path.exists() or not path.is_file():
        return {}

    text = path.read_text(encoding="utf-8", errors="replace")
    versions: dict[str, str] = {}

    if tomllib is not None:
        try:
            data = tomllib.loads(text)
            packages = data.get("package") if isinstance(data, dict) else None
            if isinstance(packages, list):
                for item in packages:
                    if not isinstance(item, dict):
                        continue
                    name = _normalize_package_name(str(item.get("name", "")))
                    version = str(item.get("version", "")).strip()
                    if name and version:
                        versions[name] = f"=={version}"
                if versions:
                    return versions
        except Exception:
            pass

    blocks = text.split("[[package]]")
    for block in blocks:
        name_match = re.search(r"name\s*=\s*\"([^\"]+)\"", block)
        version_match = re.search(r"version\s*=\s*\"([^\"]+)\"", block)
        if not name_match or not version_match:
            continue
        name = _normalize_package_name(name_match.group(1))
        version = version_match.group(1).strip()
        if name and version:
            versions[name] = f"=={version}"

    return versions
