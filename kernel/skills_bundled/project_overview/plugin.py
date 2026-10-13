# -*- coding: utf-8 -*-
"""Kvist project overview skill: languages, manifests, commands and entry points in one call."""
from __future__ import annotations

import json
import re
import tomllib
from typing import Any

MANIFEST_NAMES = (
    "pyproject.toml", "setup.py", "setup.cfg", "requirements.txt", "Pipfile", "package.json",
    "tsconfig.json", "Cargo.toml", "go.mod", "pom.xml", "build.gradle", "build.gradle.kts",
    "Gemfile", "composer.json", "CMakeLists.txt", "Makefile", "Dockerfile", "docker-compose.yml",
)
LANGUAGES = (
    ("Python", ("*.py",)), ("TypeScript", ("*.ts", "*.tsx")), ("JavaScript", ("*.js", "*.jsx", "*.mjs")),
    ("Go", ("*.go",)), ("Rust", ("*.rs",)), ("Java", ("*.java",)), ("Kotlin", ("*.kt",)),
    ("C/C++", ("*.c", "*.cc", "*.cpp", "*.h", "*.hpp")), ("C#", ("*.cs",)), ("Ruby", ("*.rb",)),
    ("PHP", ("*.php",)), ("Shell", ("*.sh",)), ("Markdown", ("*.md",)),
)
SCRIPT_KEYS = ("test", "lint", "typecheck", "build")
ENTRY_FILES = ("main.py", "app.py", "manage.py", "index.js", "index.ts", "main.go", "src/main.rs", "src/lib.rs")
MAX_TOP_LEVEL = 40
README_CHARS = 1500
VENDORED_PARTS = ("site-packages", "node_modules")


def describe() -> dict[str, Any]:
    return {"tools": ["scan"]}


def execute(tool_name: str, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
    if tool_name != "scan":
        raise ValueError(f"unknown tool {tool_name}")
    root = _root_items(context)
    names = {item["name"] for item in root}
    notes: list[str] = []
    return {
        "languages": _languages(context, notes),
        "manifests": [name for name in MANIFEST_NAMES if name in names],
        "top_level": sorted(root, key=lambda item: item["name"].startswith("."))[:MAX_TOP_LEVEL],
        "commands": _commands(context, names, notes),
        "entry_points": _entry_points(context, names, notes),
        "readme": _readme(context, names),
        "notes": notes,
    }


def _root_items(context: Any) -> list[dict[str, str]]:
    items = context.files.list(".").get("items") or []
    return [{"name": str(item["name"]), "type": str(item.get("type", "file"))}
            for item in items if isinstance(item, dict) and item.get("name")]


def _languages(context: Any, notes: list[str]) -> list[dict[str, Any]]:
    rows = []
    for language, patterns in LANGUAGES:
        count, capped = 0, False
        for pattern in patterns:
            found = context.files.find(pattern)
            count += len(found.get("matches") or [])
            capped = capped or bool(found.get("truncated"))
        if count:
            rows.append({"language": language, "files": count, "at_least": capped})
    if any(row["at_least"] for row in rows):
        notes.append("A language marked at_least has more files than the search lists (cap per pattern).")
    return sorted(rows, key=lambda row: -row["files"])


def _read_text(context: Any, path: str) -> str:
    return str(context.files.read(path).get("content") or "")


def _commands(context: Any, names: set[str], notes: list[str]) -> dict[str, list[str]]:
    commands: dict[str, list[str]] = {}
    if "package.json" in names:
        _package_scripts(context, commands, notes)
    if "pyproject.toml" in names or names & {"pytest.ini", "tox.ini", "setup.cfg", "conftest.py"}:
        _python_commands(context, names, commands)
    if "go.mod" in names:
        commands.setdefault("test", []).append("go test ./...")
        commands.setdefault("lint", []).append("go vet ./...")
    if "Cargo.toml" in names:
        commands.setdefault("test", []).append("cargo test")
        commands.setdefault("build", []).append("cargo build")
    if "Makefile" in names:
        targets = re.findall(r"^([A-Za-z0-9_.-]+):(?!=)", _read_text(context, "Makefile"), re.MULTILINE)
        shown = [target for target in dict.fromkeys(targets) if not target.startswith(".")][:12]
        if shown:
            commands["make_targets"] = [f"make {target}" for target in shown]
    return commands


def _package_scripts(context: Any, commands: dict[str, list[str]], notes: list[str]) -> None:
    try:
        package = json.loads(_read_text(context, "package.json") or "{}")
    except ValueError:
        notes.append("package.json is not valid JSON, its scripts were not read.")
        return
    scripts = package.get("scripts") if isinstance(package, dict) else None
    for key in SCRIPT_KEYS:
        if isinstance(scripts, dict) and scripts.get(key):
            commands.setdefault(key, []).append(f"npm run {key}" if key != "test" else "npm test")


def _python_commands(context: Any, names: set[str], commands: dict[str, list[str]]) -> None:
    pyproject: dict[str, Any] = {}
    if "pyproject.toml" in names:
        try:
            pyproject = tomllib.loads(_read_text(context, "pyproject.toml"))
        except tomllib.TOMLDecodeError:
            pyproject = {}
    tools = pyproject.get("tool") if isinstance(pyproject.get("tool"), dict) else {}
    if "pytest" in tools or names & {"pytest.ini", "conftest.py"} or "tox.ini" in names:
        commands.setdefault("test", []).append("python -m pytest")
    if "ruff" in tools or names & {"ruff.toml", ".ruff.toml"}:
        commands.setdefault("lint", []).append("ruff check .")
    if "mypy" in tools or names & {"mypy.ini", ".mypy.ini"}:
        commands.setdefault("typecheck", []).append("python -m mypy .")


def _entry_points(context: Any, names: set[str], notes: list[str]) -> list[str]:
    points: list[str] = []
    if "pyproject.toml" in names:
        try:
            project = tomllib.loads(_read_text(context, "pyproject.toml")).get("project") or {}
        except tomllib.TOMLDecodeError:
            project = {}
        scripts = project.get("scripts") if isinstance(project, dict) else None
        points += [f"{name} = {target}" for name, target in (scripts or {}).items()]
    if "package.json" in names:
        try:
            package = json.loads(_read_text(context, "package.json") or "{}")
        except ValueError:
            package = {}
        if isinstance(package, dict):
            points += [f"main: {package['main']}"] if isinstance(package.get("main"), str) else []
            bins = package.get("bin")
            points += [f"bin {key}: {value}" for key, value in bins.items()] if isinstance(bins, dict) else []
    for path in ENTRY_FILES:
        folder, _, name = path.rpartition("/")
        if name in _names_in(context, folder, names):
            points.append(path)
    main_modules = [str(path) for path in context.files.find("__main__.py").get("matches") or []
                    if not _is_vendored(str(path))]
    points += main_modules[:5]
    if not points:
        notes.append("No entry point was found in the project's configuration or by the usual file names.")
    return points


def _is_vendored(path: str) -> bool:
    parts = path.split("/")
    return any(part in VENDORED_PARTS or part.startswith(".venv") for part in parts)


def _names_in(context: Any, folder: str, root_names: set[str]) -> set[str]:
    if not folder:
        return root_names
    if folder not in root_names:
        return set()
    items = context.files.list(folder).get("items") or []
    return {str(item["name"]) for item in items if isinstance(item, dict) and item.get("name")}


def _readme(context: Any, names: set[str]) -> str:
    for name in sorted(names):
        if name.lower().startswith("readme") and not name.lower().endswith(".ru.md"):
            text = re.sub(r"<[^>]+>", "", _read_text(context, name))
            return re.sub(r"\n{3,}", "\n\n", text).strip()[:README_CHARS]
    return ""
