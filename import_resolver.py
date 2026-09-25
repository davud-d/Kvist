# -*- coding: utf-8 -*-
"""Resolve a file's imports to project-local modules only.

Third-party and stdlib imports are dropped before they ever reach a prompt:
the model already knows public library APIs from training, so sending that
code is pure token cost and privacy exposure with no accuracy benefit. Only
imports that resolve to a real file inside the workspace get their public
top-level signatures extracted and passed on to the completion prompt.

This is a best-effort enrichment, not a language server: every resolution
step degrades to "no enrichment for this import" on ambiguity rather than
raising, and nothing here ever executes the target code.
"""
from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path

from change_journal import journal_path_violation

# Directories that can appear inside a workspace but are never "project
# code" even when nested under it (vendored copies, package managers).
_THIRD_PARTY_DIR_NAMES = frozenset({
    "node_modules", "venv", ".venv", "site-packages", "dist-packages",
    "vendor", ".tox", "__pycache__",
})

_TS_LANGUAGES = frozenset({
    "typescript", "typescriptreact", "javascript", "javascriptreact",
})
_TS_SOURCE_SUFFIXES = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
_TS_INDEX_NAMES = tuple(f"index{suffix}" for suffix in _TS_SOURCE_SUFFIXES)

MAX_MODULES = 6
MAX_SYMBOLS_PER_MODULE = 12
MAX_SIGNATURE_BLOCK_CHARS = 3_000
MAX_SIGNATURE_CHARS = 200
# Bounded read of a resolved import target: only the head of a file is ever
# needed for top-level signatures, and this caps memory on a huge file.
_MAX_TARGET_FILE_BYTES = 200_000

_TS_IMPORT_RE = re.compile(
    r"""import\b[^;\n]*?from\s+['"]([^'"]+)['"]"""
    r"""|import\s+['"]([^'"]+)['"]"""
    r"""|require\(\s*['"]([^'"]+)['"]\s*\)"""
)
_TS_EXPORT_RE = re.compile(
    r"^\s*export\s+(?:default\s+)?(?:declare\s+)?(?:async\s+)?"
    r"(?:function|class|const|let|interface|type|enum)\s+"
    r"[A-Za-z_$][A-Za-z0-9_$]*"
)
# A one-line signature's trailing "{" or "{}" is body noise, not signature.
_TS_TRAILING_BODY_RE = re.compile(r"\s*\{\s*\}?\s*$")


@dataclass(slots=True)
class ResolvedImport:
    module: str
    relative_path: str
    signatures: list[str]


def extract_raw_imports(head: str, language: str) -> list[str]:
    """Pull raw import statements (Python) or specifiers (TS/JS) out of *head*."""
    if language == "python":
        return [
            stripped
            for raw_line in head.splitlines()
            if (stripped := raw_line.strip()).startswith(("import ", "from "))
        ]
    if language in _TS_LANGUAGES:
        return [
            next(group for group in match.groups() if group)
            for match in _TS_IMPORT_RE.finditer(head)
        ]
    return []


def resolve_project_imports(
    workspace: Path,
    file_path: Path,
    raw_imports: list[str],
    language: str,
) -> list[ResolvedImport]:
    """Resolve only the imports that point at files inside *workspace*.

    Everything else (stdlib, third-party packages, anything outside the
    workspace) is dropped without being read.
    """
    if language == "python":
        candidates = _resolve_python_imports(workspace, file_path, raw_imports)
    elif language in _TS_LANGUAGES:
        candidates = _resolve_ts_imports(workspace, file_path, raw_imports)
    else:
        return []

    resolved: list[ResolvedImport] = []
    budget = MAX_SIGNATURE_BLOCK_CHARS
    for module_name, target in candidates[:MAX_MODULES]:
        if budget <= 0:
            break
        signatures = _extract_signatures(target, language)[:MAX_SYMBOLS_PER_MODULE]
        kept: list[str] = []
        for signature in signatures:
            if budget - len(signature) <= 0:
                break
            kept.append(signature)
            budget -= len(signature)
        if kept:
            resolved.append(
                ResolvedImport(
                    module=module_name,
                    relative_path=target.relative_to(workspace).as_posix(),
                    signatures=kept,
                )
            )
    return resolved


# ── path safety ─────────────────────────────────────────────────────────


def _safe_resolve(workspace: Path, candidate: Path) -> Path | None:
    """*candidate* if it stays inside the workspace and names no protected
    or third-party path; ``None`` otherwise."""
    try:
        resolved = candidate.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(workspace):
        return None
    relative = resolved.relative_to(workspace)
    if journal_path_violation(workspace, relative.as_posix()) is not None:
        return None
    if any(part in _THIRD_PARTY_DIR_NAMES for part in relative.parts):
        return None
    return resolved


def _read_bounded(path: Path) -> str | None:
    try:
        with path.open("rb") as handle:
            data = handle.read(_MAX_TARGET_FILE_BYTES)
    except OSError:
        return None
    return data.decode("utf-8", errors="replace")


# ── Python ──────────────────────────────────────────────────────────────


def _resolve_dotted(root: Path, dotted_parts: list[str]) -> Path | None:
    candidate = root.joinpath(*dotted_parts) if dotted_parts else root
    if dotted_parts:
        module_file = candidate.with_suffix(".py")
        if module_file.is_file():
            return module_file
    package_init = candidate / "__init__.py"
    if package_init.is_file():
        return package_init
    return None


def _resolve_absolute_python(workspace: Path, dotted_parts: list[str]) -> Path | None:
    for root in (workspace, workspace / "src"):
        found = _resolve_dotted(root, dotted_parts)
        if found is not None:
            return found
    return None


def _collect_python_target(
    results: list[tuple[str, Path]],
    seen: set[Path],
    workspace: Path,
    name: str,
    found: Path | None,
) -> None:
    if found is None:
        return
    resolved = _safe_resolve(workspace, found)
    if resolved is None or resolved in seen:
        return
    seen.add(resolved)
    results.append((name, resolved))


def _collect_python_from(
    results: list[tuple[str, Path]],
    seen: set[Path],
    workspace: Path,
    file_dir: Path,
    node: ast.ImportFrom,
) -> None:
    if node.level > 0:
        base = file_dir
        for _ in range(node.level - 1):
            base = base.parent
        dots = "." * node.level
        if node.module:
            found = _resolve_dotted(base, node.module.split("."))
            _collect_python_target(results, seen, workspace, dots + node.module, found)
        else:
            # `from . import x` / `from .. import x, y` — each name is a
            # candidate submodule of the current package.
            for alias in node.names:
                found = _resolve_dotted(base, [alias.name])
                _collect_python_target(results, seen, workspace, dots + alias.name, found)
        return
    if node.module:
        found = _resolve_absolute_python(workspace, node.module.split("."))
        _collect_python_target(results, seen, workspace, node.module, found)


def _resolve_python_imports(
    workspace: Path, file_path: Path, raw_imports: list[str]
) -> list[tuple[str, Path]]:
    results: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    file_dir = file_path.parent
    for line in raw_imports:
        try:
            tree = ast.parse(line)
        except SyntaxError:
            continue
        for node in tree.body:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    found = _resolve_absolute_python(workspace, alias.name.split("."))
                    _collect_python_target(results, seen, workspace, alias.name, found)
            elif isinstance(node, ast.ImportFrom):
                _collect_python_from(results, seen, workspace, file_dir, node)
    return results


def _python_signatures(path: Path) -> list[str]:
    text = _read_bounded(path)
    if not text:
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    signatures: list[str] = []
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name.startswith("_"):
                continue
            signatures.append(_render_python_signature(node)[:MAX_SIGNATURE_CHARS])
    return signatures


def _render_python_signature(node: ast.AST) -> str:
    if isinstance(node, ast.ClassDef):
        bases = ", ".join(ast.unparse(base) for base in node.bases)
        return f"class {node.name}({bases}):" if bases else f"class {node.name}:"
    prefix = "async def" if isinstance(node, ast.AsyncFunctionDef) else "def"
    returns = f" -> {ast.unparse(node.returns)}" if node.returns else ""
    return f"{prefix} {node.name}({ast.unparse(node.args)}){returns}:"


# ── TypeScript / JavaScript ─────────────────────────────────────────────


def _resolve_ts_specifier(file_dir: Path, specifier: str) -> Path | None:
    base = file_dir / specifier
    candidates: list[Path] = []
    if base.suffix in _TS_SOURCE_SUFFIXES:
        candidates.append(base)
    candidates.extend(base.with_suffix(suffix) for suffix in _TS_SOURCE_SUFFIXES)
    candidates.extend(base / name for name in _TS_INDEX_NAMES)
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _resolve_ts_imports(
    workspace: Path, file_path: Path, specifiers: list[str]
) -> list[tuple[str, Path]]:
    results: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    file_dir = file_path.parent
    for specifier in specifiers:
        if not specifier.startswith(("./", "../")):
            continue  # bare package specifier — third-party by definition
        found = _resolve_ts_specifier(file_dir, specifier)
        if found is None:
            continue
        resolved = _safe_resolve(workspace, found)
        if resolved is None or resolved in seen:
            continue
        seen.add(resolved)
        results.append((specifier, resolved))
    return results


def _ts_signatures(path: Path) -> list[str]:
    text = _read_bounded(path)
    if not text:
        return []
    signatures: list[str] = []
    for line in text.splitlines():
        if _TS_EXPORT_RE.match(line):
            cleaned = _TS_TRAILING_BODY_RE.sub("", line.strip())
            signatures.append(cleaned[:MAX_SIGNATURE_CHARS])
    return signatures


def _extract_signatures(path: Path, language: str) -> list[str]:
    if language == "python":
        return _python_signatures(path)
    if language in _TS_LANGUAGES:
        return _ts_signatures(path)
    return []
