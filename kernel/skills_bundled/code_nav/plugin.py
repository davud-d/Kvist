# -*- coding: utf-8 -*-
"""Kvist Python code navigation skill: jedi for names, pyflakes for diagnostics."""
from __future__ import annotations

import io
import shutil
from pathlib import Path
from typing import Any

PROJECT = Path("/tmp/kvist-project")
MAX_FILES = 2000
MAX_BYTES = 40 * 1024 * 1024
MAX_ITEMS = 200
_SYNCED: dict[str, bool] = {}


def describe() -> dict[str, Any]:
    return {"tools": ["definition", "references", "outline", "rename_preview", "diagnostics"]}


def execute(tool_name: str, arguments: dict[str, Any], context: Any) -> dict[str, Any]:
    note = sync(context)
    path = arguments.get("path") or ""
    if path:
        refresh(context, path)
    result = TOOLS[tool_name](arguments)
    if note:
        result["note"] = note
    return result


def sync(context: Any) -> str:
    """Copy the workspace's Python files once per runner (one task)."""
    if _SYNCED.get("done"):
        return ""
    matches = context.files.find("*.py").get("matches") or []
    total = 0
    copied = 0
    skipped = []
    for relative in matches[:MAX_FILES]:
        context.runtime.check()
        try:
            local = Path(context.files.read_bytes(relative))
        except Exception as error:  # noqa: BLE001 - protected or unreadable files stay out
            skipped.append(f"{relative}: {getattr(error, 'code', 'error')}")
            continue
        target = PROJECT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(local, target)
        total += target.stat().st_size
        copied += 1
        if total > MAX_BYTES:
            break
    _SYNCED["done"] = True
    notes = []
    if len(matches) > MAX_FILES or total > MAX_BYTES:
        notes.append(f"only the first {copied} Python files were analysed")
    if skipped:
        notes.append(f"{len(skipped)} files could not be read")
    return "; ".join(notes)


def refresh(context: Any, relative: str) -> None:
    local = Path(context.files.read_bytes(relative))
    target = PROJECT / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(local, target)


def _script(arguments: dict[str, Any]):
    import jedi

    target = PROJECT / arguments["path"]
    project = jedi.Project(str(PROJECT))
    return jedi.Script(path=str(target), project=project)


def _name(name: Any) -> dict[str, Any]:
    module = Path(name.module_path) if name.module_path else None
    inside = module is not None and PROJECT in module.parents
    return {
        "name": name.name,
        "type": name.type,
        "path": module.relative_to(PROJECT).as_posix() if inside else (str(module) if module else None),
        "in_workspace": inside,
        "line": name.line,
        "column": name.column,
        "code": (name.get_line_code() or "").strip()[:200],
    }


def definition(arguments: dict[str, Any]) -> dict[str, Any]:
    names = _script(arguments).goto(arguments["line"], arguments["column"], follow_imports=True)
    return {"definitions": [_name(name) for name in names][:MAX_ITEMS]}


def references(arguments: dict[str, Any]) -> dict[str, Any]:
    names = _script(arguments).get_references(arguments["line"], arguments["column"], scope="project")
    found = [_name(name) for name in names]
    return {"references": found[:MAX_ITEMS], "total": len(found), "truncated": len(found) > MAX_ITEMS}


def outline(arguments: dict[str, Any]) -> dict[str, Any]:
    names = _script(arguments).get_names(all_scopes=True, definitions=True)
    return {"symbols": [{"name": name.full_name or name.name, "type": name.type, "line": name.line}
                        for name in names if name.type in ("class", "function", "statement", "module")][:MAX_ITEMS * 2]}


def rename_preview(arguments: dict[str, Any]) -> dict[str, Any]:
    refactoring = _script(arguments).rename(arguments["line"], arguments["column"], new_name=arguments["new_name"])
    changes = []
    for path, changed in refactoring.get_changed_files().items():
        diff = changed.get_diff()
        relative = Path(path).relative_to(PROJECT).as_posix() if PROJECT in Path(path).parents else str(path)
        changes.append({"path": relative, "diff": diff.replace(str(PROJECT) + "/", "")})
    return {"files": changes, "note": "Nothing was written. Names built at run time (getattr, strings) are not covered."}


def diagnostics(arguments: dict[str, Any]) -> dict[str, Any]:
    from pyflakes.api import check
    from pyflakes.reporter import Reporter

    targets = [PROJECT / arguments["path"]] if arguments.get("path") else sorted(PROJECT.rglob("*.py"))
    out, err = io.StringIO(), io.StringIO()
    reporter = Reporter(out, err)
    for target in targets:
        check(target.read_text(encoding="utf-8", errors="replace"), target.relative_to(PROJECT).as_posix(), reporter)
    findings = parse_findings(out.getvalue() + err.getvalue())
    return {"findings": findings[:MAX_ITEMS], "total": len(findings), "files_checked": len(targets)}


def parse_findings(text: str) -> list[dict[str, Any]]:
    findings = []
    for line in text.splitlines():
        parts = line.split(":", 3)
        if len(parts) >= 3 and parts[1].isdigit():
            column = parts[2] if parts[2].isdigit() else ""
            message = (parts[3] if column else ":".join(parts[2:])).strip()
            findings.append({"path": parts[0], "line": int(parts[1]), "column": int(column) if column else None,
                             "message": message})
    return findings


TOOLS = {"definition": definition, "references": references, "outline": outline,
         "rename_preview": rename_preview, "diagnostics": diagnostics}
