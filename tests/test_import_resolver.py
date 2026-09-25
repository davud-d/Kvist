# -*- coding: utf-8 -*-
from __future__ import annotations

from pathlib import Path

import import_resolver as ir


def _write(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# ── extraction ───────────────────────────────────────────────────────────


def test_extract_python_imports_ignores_non_import_lines():
    head = "import os\nfrom pkg.helpers import greet\nx = 1\nfrom . import sibling\n"
    assert ir.extract_raw_imports(head, "python") == [
        "import os",
        "from pkg.helpers import greet",
        "from . import sibling",
    ]


def test_extract_ts_imports_covers_all_import_forms():
    head = (
        "import React from 'react';\n"
        "import { Foo } from './foo';\n"
        "import '../styles.css';\n"
        "const bar = require('../bar');\n"
    )
    assert ir.extract_raw_imports(head, "typescript") == [
        "react",
        "./foo",
        "../styles.css",
        "../bar",
    ]


def test_extract_raw_imports_unknown_language_returns_nothing():
    assert ir.extract_raw_imports("import os", "rust") == []


# ── Python resolution ────────────────────────────────────────────────────


def test_resolves_absolute_project_import_and_drops_third_party(tmp_path):
    workspace = tmp_path
    _write(workspace / "pkg" / "__init__.py", "")
    _write(
        workspace / "pkg" / "helpers.py",
        "def greet(name: str) -> str:\n"
        "    return name\n\n"
        "def _private() -> None:\n"
        "    pass\n\n"
        "class Widget:\n"
        "    pass\n",
    )
    main_file = _write(workspace / "main.py", "import os\nfrom pkg.helpers import greet\n")

    raw = ir.extract_raw_imports(main_file.read_text(encoding="utf-8"), "python")
    resolved = ir.resolve_project_imports(workspace, main_file, raw, "python")

    assert len(resolved) == 1
    item = resolved[0]
    assert item.module == "pkg.helpers"
    assert item.relative_path == "pkg/helpers.py"
    assert "def greet(name: str) -> str:" in item.signatures
    assert "class Widget:" in item.signatures
    assert not any("_private" in sig for sig in item.signatures)


def test_resolves_relative_sibling_import(tmp_path):
    workspace = tmp_path
    _write(workspace / "app" / "__init__.py", "")
    _write(workspace / "app" / "utils.py", "def helper() -> None:\n    pass\n")
    caller = _write(workspace / "app" / "main.py", "from . import utils\n")

    raw = ir.extract_raw_imports(caller.read_text(encoding="utf-8"), "python")
    resolved = ir.resolve_project_imports(workspace, caller, raw, "python")

    assert len(resolved) == 1
    assert resolved[0].relative_path == "app/utils.py"
    assert "def helper() -> None:" in resolved[0].signatures


def test_third_party_dir_nested_in_workspace_is_never_project_code(tmp_path):
    workspace = tmp_path
    _write(workspace / "node_modules" / "leftpad" / "index.js", "export function pad() {}\n")
    caller = _write(
        workspace / "app.ts", "import { pad } from './node_modules/leftpad/index.js';\n"
    )

    raw = ir.extract_raw_imports(caller.read_text(encoding="utf-8"), "typescript")
    resolved = ir.resolve_project_imports(workspace, caller, raw, "typescript")
    assert resolved == []


def test_import_cannot_escape_the_workspace(tmp_path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    _write(tmp_path / "secret.py", "def leaked() -> None:\n    pass\n")
    # A relative import whose level walks above the workspace root must
    # never resolve to a file outside it, however deep the dots go.
    caller = _write(workspace / "pkg" / "sub" / "mod.py", "from .... import secret\n")

    raw = ir.extract_raw_imports(caller.read_text(encoding="utf-8"), "python")
    resolved = ir.resolve_project_imports(workspace, caller, raw, "python")
    assert resolved == []


def test_unresolvable_third_party_import_is_dropped(tmp_path):
    workspace = tmp_path
    caller = _write(workspace / "main.py", "import numpy\nfrom collections import OrderedDict\n")
    raw = ir.extract_raw_imports(caller.read_text(encoding="utf-8"), "python")
    resolved = ir.resolve_project_imports(workspace, caller, raw, "python")
    assert resolved == []


# ── TypeScript resolution ────────────────────────────────────────────────


def test_resolves_relative_ts_import_with_missing_extension(tmp_path):
    workspace = tmp_path
    _write(
        workspace / "src" / "widget.ts",
        "export function render(): void {}\n"
        "export class Widget {}\n"
        "function internal() {}\n",
    )
    caller = _write(workspace / "src" / "app.ts", "import { render, Widget } from './widget';\n")

    raw = ir.extract_raw_imports(caller.read_text(encoding="utf-8"), "typescript")
    resolved = ir.resolve_project_imports(workspace, caller, raw, "typescript")

    assert len(resolved) == 1
    assert resolved[0].relative_path == "src/widget.ts"
    assert "export function render(): void" in resolved[0].signatures
    assert "export class Widget" in resolved[0].signatures
    assert not any("internal" in sig for sig in resolved[0].signatures)


def test_bare_ts_package_specifier_is_never_resolved(tmp_path):
    workspace = tmp_path
    caller = _write(workspace / "app.ts", "import React from 'react';\n")
    raw = ir.extract_raw_imports(caller.read_text(encoding="utf-8"), "typescript")
    resolved = ir.resolve_project_imports(workspace, caller, raw, "typescript")
    assert resolved == []


# ── budget ───────────────────────────────────────────────────────────────


def test_signature_block_budget_is_capped(tmp_path):
    workspace = tmp_path
    body = "\n".join(f"def fn_{i}(a, b, c) -> None:\n    pass\n" for i in range(50))
    _write(workspace / "big.py", body)
    caller = _write(workspace / "main.py", "from big import fn_0\n")

    raw = ir.extract_raw_imports(caller.read_text(encoding="utf-8"), "python")
    resolved = ir.resolve_project_imports(workspace, caller, raw, "python")

    assert len(resolved) == 1
    assert len(resolved[0].signatures) <= ir.MAX_SYMBOLS_PER_MODULE
    assert sum(len(sig) for sig in resolved[0].signatures) <= ir.MAX_SIGNATURE_BLOCK_CHARS
