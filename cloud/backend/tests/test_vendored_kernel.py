# -*- coding: utf-8 -*-
"""A release carries only the core files the server imports; the list must match what the server really imports."""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import kvist_cloud

CLOUD = Path(kvist_cloud.__file__).resolve().parents[2]
REPO = CLOUD.parent
LISTED = {line.strip() for line in (CLOUD / "deploy" / "kernel-files.txt").read_text().splitlines() if line.strip()}


def imported_kernel_modules() -> set[str]:
    modules: set[str] = set()
    for source in (CLOUD / "backend" / "kvist_cloud").rglob("*.py"):
        for node in ast.walk(ast.parse(source.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom) and node.level == 0 and (node.module or "").split(".")[0] == "kernel":
                modules.add(node.module)
            elif isinstance(node, ast.Import):
                modules.update(a.name for a in node.names if a.name.split(".")[0] == "kernel")
    return modules


def files_for(module: str) -> set[str]:
    parts = module.split(".")
    needed = {"/".join(parts[:i]) + "/__init__.py" for i in range(1, len(parts))}
    leaf = "/".join(parts)
    needed.add(leaf + "/__init__.py" if (REPO / leaf / "__init__.py").exists() else leaf + ".py")
    return needed


def test_every_core_import_of_the_server_is_vendored():
    needed = set().union(*(files_for(module) for module in imported_kernel_modules()))
    assert needed <= LISTED, f"add to deploy/kernel-files.txt: {sorted(needed - LISTED)}"


def test_nothing_unneeded_is_listed():
    needed = set().union(*(files_for(module) for module in imported_kernel_modules()))
    assert LISTED <= needed, f"listed but not imported: {sorted(LISTED - needed)}"


def test_the_listed_files_exist_and_use_only_the_standard_library():
    """If one of them imported the rest of the core, the release would break again, quietly."""
    for name in sorted(LISTED):
        path = REPO / name
        assert path.is_file(), name
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.ImportFrom):
                imported = [node.module or ""] if node.level == 0 else ["<relative>"]
            elif isinstance(node, ast.Import):
                imported = [alias.name for alias in node.names]
            else:
                continue
            for module in imported:
                assert module.split(".")[0] in sys.stdlib_module_names, f"{name} imports {module}"
