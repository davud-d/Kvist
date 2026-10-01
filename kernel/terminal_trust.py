# -*- coding: utf-8 -*-
"""Executable trust: shebang parsing, venv launcher verification and trusted system executable checks."""
from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from kernel.sandbox import RUNTIME_MOUNT_ROOTS
from kernel.terminal_paths import _is_relative_to, _path_entry_identity, _sha256_file
from kernel.terminal_types import ExecutableBinding, FileIdentity


def _parse_shebang(head: bytes) -> list[str] | None:
    """Return the ``#!`` token list, ``[]`` for an ambiguous line, or None.

    ``None`` means the file has no shebang (e.g. an ELF binary).  An empty list
    means the shebang exists but is empty/undecodable and the caller must fail
    closed.  The first token is the program the kernel actually execs.
    """
    if not head.startswith(b"#!"):
        return None
    line = head[2:].split(b"\n", 1)[0]
    try:
        text = line.decode("utf-8").strip()
    except UnicodeDecodeError:
        return []
    return text.split()


def _env_interpreter_name(args: list[str]) -> str:
    index = 0
    while index < len(args):
        token = args[index]
        if token in ("-S", "--split-string"):
            index += 1
            continue
        if token.startswith("-S"):
            inner = token[2:].strip()
            if inner:
                return inner.split()[0]
            index += 1
            continue
        if token.startswith("--split-string="):
            inner = token.split("=", 1)[1].strip()
            return inner.split()[0] if inner else ""
        if token in ("-u", "--unset", "-C", "--chdir", "-P"):
            index += 2
            continue
        if token.startswith("-"):
            index += 1
            continue
        if "=" in token:
            index += 1
            continue
        return token.split()[0] if " " in token else token
    return ""


def _same_executable_family(a: str, b: str) -> bool:
    """Return True when *a* and *b* name the same tool family.

    ``python`` / ``python3`` / ``python3.11`` are the same family;
    ``python`` / ``node`` are not.
    """
    if a == b:
        return True
    a_base = re.sub(r"\d[\d.]*$", "", a)
    b_base = re.sub(r"\d[\d.]*$", "", b)
    return a_base == b_base


_VENV_LAUNCHER_DIRS: frozenset[str] = frozenset(
    (".venv/bin", "venv/bin", ".venv/Scripts", "venv/Scripts")
)
# python, python3, python3.11, ... and the Windows .exe suffix.  Deliberately
# excludes bash/sh/env/perl/node/etc so a launcher aliasing another family is
# never mistaken for a venv Python interpreter.
_VENV_PYTHON_BASENAME_RE = re.compile(r"^python(3(\.\d{1,2})?)?(\.exe)?$", re.IGNORECASE)


def _verify_venv_python_launcher(
    workspace: Path,
    lookup: Path,
    resolved: Path,
) -> tuple[FileIdentity, str] | None:
    """Verify ``lookup`` is a genuine project venv Python launcher.

    This is the *only* exception that lets a workspace-owned (attacker- or
    tool-writable) path stand in for a trusted system executable.  Every
    condition below is required; any failure returns ``None`` and the caller
    denies the workspace-to-system escape like any other untrusted alias.
    Returns ``(pyvenv_cfg_identity, pyvenv_cfg_digest)`` on success so the
    execution context binds the launcher to its venv's declared identity —
    mutating pyvenv.cfg between approval and spawn invalidates the approval.

    The final interpreter may live outside the trusted system roots — a venv
    builder such as ``uv`` or ``pyenv`` places it under the user's HOME — but
    only when ``pyvenv.cfg`` itself, attested below, declares that exact
    resolved path as the venv's interpreter, so a venv can only vouch for the
    interpreter it was built with, never for an arbitrary target.  Under
    Bubblewrap that interpreter's installation root is then mounted read-only
    (``venv_interpreter_roots``); refusing it left a project's own Python —
    and so its tests and linters — unrunnable in the default sandbox.
    """
    try:
        if not _is_relative_to(lookup, workspace):
            return None
        rel_parts = lookup.relative_to(workspace).parts
    except ValueError:
        return None
    if len(rel_parts) != 3:
        return None
    venv_name, bin_name, launcher_name = rel_parts
    if f"{venv_name}/{bin_name}" not in _VENV_LAUNCHER_DIRS:
        return None
    if not _VENV_PYTHON_BASENAME_RE.match(launcher_name):
        return None

    venv_root = workspace / venv_name
    try:
        if stat.S_ISLNK(venv_root.lstat().st_mode):
            return None
        venv_root_resolved = venv_root.resolve(strict=True)
    except OSError:
        return None
    if not _is_relative_to(venv_root_resolved, workspace) or venv_root_resolved == workspace:
        return None

    pyvenv_cfg = venv_root / "pyvenv.cfg"
    try:
        cfg_lstat = pyvenv_cfg.lstat()
    except OSError:
        return None
    if not stat.S_ISREG(cfg_lstat.st_mode):
        return None

    # The final target the kernel would actually exec must be a trusted,
    # user-immutable system Python — never Bash, sh, env, Perl, Node, or any
    # other binary, regardless of what the launcher basename claims.
    if not _VENV_PYTHON_BASENAME_RE.match(resolved.name):
        return None
    trusted_root = _is_trusted_system_executable(resolved, resolved)
    if not trusted_root:
        # The venv cannot vouch for a target it did not itself record at
        # creation.
        declared = _read_pyvenv_cfg_executable(pyvenv_cfg)
        if declared is None:
            return None
        try:
            declared_resolved = declared.resolve(strict=True)
        except OSError:
            return None
        if declared_resolved != resolved:
            return None

    cfg_identity = _path_entry_identity(pyvenv_cfg)
    if cfg_identity is None:
        return None
    try:
        cfg_digest = _sha256_file(pyvenv_cfg)
    except OSError:
        return None
    return (cfg_identity, cfg_digest)


def venv_interpreter_roots(bindings: tuple[ExecutableBinding, ...]) -> tuple[Path, ...]:
    """Installation roots of attested venv interpreters outside the system roots.

    Bubblewrap hides HOME, where uv and pyenv install interpreters, so the
    launcher resolved but could not exec (``bwrap: execvp ... No such file``).
    ``<root>/bin/python3.x`` needs ``<root>/lib`` beside it; the root is bound
    read-only through the same validation as ``AGENT_TOOL_PATHS``.
    """
    return tuple(
        interpreter.parent.parent
        for binding in bindings
        if (interpreter := _external_venv_interpreter(binding)) is not None
    )


def venv_interpreter_links(
    bindings: tuple[ExecutableBinding, ...], workspace: Path
) -> tuple[tuple[str, str], ...]:
    """``(link target, link path)`` for each hop from a launcher to its interpreter
    that the sandbox would otherwise not see.

    A venv built by ``python3.11 -m venv`` points at ``~/.local/bin/python3.11``,
    itself a link into the uv install; with only the install root mounted, the
    middle hop is missing and exec fails. Only the link itself is recreated —
    nothing else under that directory becomes visible.
    """
    links: list[tuple[str, str]] = []
    for binding in bindings:
        interpreter = _external_venv_interpreter(binding)
        if interpreter is None or not binding.lookup_path:
            continue
        mounted = (workspace, interpreter.parent.parent, *map(Path, RUNTIME_MOUNT_ROOTS))
        for target, link in _symlinks_along(Path(binding.lookup_path)):
            if not any(_is_relative_to(Path(link), root) for root in mounted):
                links.append((target, link))
    return tuple(links)


_MAX_VENV_LINK_HOPS = 16


def _symlinks_along(path: Path) -> list[tuple[str, str]]:
    """Every symlink ``realpath`` passes through on ``path``, as (target, link).

    Directory components count too: uv's ``cpython-3.11-...`` is itself a
    link to the versioned install, so following only the last component
    missed it.
    """
    pending = list(path.parts[1:])
    current = Path(path.anchor)
    links: list[tuple[str, str]] = []
    while pending and len(links) < _MAX_VENV_LINK_HOPS:
        part = pending.pop(0)
        candidate = current / part
        if not candidate.is_symlink():
            current = current.parent if part == ".." else candidate
            continue
        target = os.readlink(candidate)
        links.append((target, str(candidate)))
        target_path = Path(target)
        if target_path.is_absolute():
            current = Path(target_path.anchor)
        pending = list(target_path.parts[1:] if target_path.is_absolute() else target_path.parts) + pending
    return links


def _external_venv_interpreter(binding: ExecutableBinding) -> Path | None:
    if not binding.venv_launcher or not binding.resolved_path:
        return None
    resolved = Path(binding.resolved_path)
    if _is_trusted_system_executable(resolved, resolved):
        return None
    return resolved


def _read_pyvenv_cfg_executable(pyvenv_cfg: Path) -> Path | None:
    """Return the ``executable`` path a venv's ``pyvenv.cfg`` declares, if any."""
    try:
        text = pyvenv_cfg.read_text(encoding="utf-8", errors="strict")
    except (OSError, UnicodeDecodeError):
        return None
    for line in text.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key.strip().lower() != "executable":
            continue
        value = value.strip()
        if not value:
            return None
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = pyvenv_cfg.parent / candidate
        return candidate
    return None


def _is_trusted_system_executable(lookup: Path, resolved: Path) -> bool:
    """Trust only fixed system roots that the current user cannot mutate."""
    if os.name == "nt":
        system_root = Path(os.environ.get("SystemRoot", r"C:\Windows"))
        roots = [system_root / "System32", system_root]
    else:
        roots = [Path("/usr"), Path("/bin"), Path("/sbin")]

    canonical_roots: list[Path] = []
    for root in roots:
        try:
            candidate = root.resolve(strict=True)
        except OSError:
            continue
        if candidate not in canonical_roots:
            canonical_roots.append(candidate)

    try:
        lookup_parent = lookup.parent.resolve(strict=True)
        target = resolved.resolve(strict=True)
    except OSError:
        return False
    matching_root = next(
        (
            root
            for root in canonical_roots
            if _is_relative_to(lookup_parent, root) and _is_relative_to(target, root)
        ),
        None,
    )
    if matching_root is None:
        return False

    paths_to_check = [target]
    for start in (lookup_parent, target.parent):
        current = start
        while True:
            if current not in paths_to_check:
                paths_to_check.append(current)
            if current == matching_root:
                break
            if current.parent == current:
                return False
            current = current.parent
    return not any(_path_is_user_mutable(path) for path in paths_to_check)


def _path_is_user_mutable(path: Path) -> bool:
    try:
        info = path.stat()
    except OSError:
        return True
    if not hasattr(os, "geteuid"):
        return bool(info.st_mode & stat.S_IWOTH)
    if info.st_uid == os.geteuid():
        return True
    try:
        if os.access(path, os.W_OK, effective_ids=True):
            return True
    except TypeError:
        if os.access(path, os.W_OK):
            return True
    if info.st_mode & stat.S_IWOTH:
        return True
    try:
        groups = {os.getegid(), *os.getgroups()}
    except OSError:
        groups = {os.getegid()}
    return bool(info.st_mode & stat.S_IWGRP and info.st_gid in groups)
