# -*- coding: utf-8 -*-
"""Workspace secret classification and enumeration.

The execution boundary protects secret material two ways: hard-denying argv
tokens that name secret paths (defence in depth in ``terminal.py``) and — the
real boundary — masking those files inside the OS sandbox so arbitrary child
code cannot read them through runtime-constructed paths.  Both consumers share
the classification here so the argv check and the filesystem mask never drift.
"""
from __future__ import annotations

import hashlib
import os
import re
import stat
from pathlib import Path

# Basenames whose whole name marks a secret regardless of extension.
_SECRET_BASENAMES: frozenset[str] = frozenset({
    "id_rsa", "id_ed25519", "id_ecdsa", "id_dsa",
    "credentials",
    ".netrc", ".npmrc", ".pypirc", ".git-credentials",
})

# Directories whose entire contents are secret.
_SECRET_DIR_NAMES: frozenset[str] = frozenset({
    ".ssh", ".aws", ".gnupg", ".kube", ".azure",
})

# Extensions that mark private key / certificate material.
_SECRET_SUFFIXES: tuple[str, ...] = (
    ".pem", ".key", ".p12", ".pfx", ".crt",
)

# ``.env`` and any dotted variant (``.env.local`` …).  Existing Kvist policy
# intentionally treats templates such as ``.env.example`` as secret too, so we
# do not carve them out here.
_ENV_RE = re.compile(r"^\.env(?:\..*)?$", re.IGNORECASE)

# ``.kvist`` is masked wholesale (tmpfs) and never descended into.  Everything
# else is walked: a secret can hide anywhere, and — critically — a hardlink is a
# second name for a secret's inode, so we must discover every path that shares a
# secret inode, including inside .git / node_modules.
_MASKED_DIR_NAMES: frozenset[str] = frozenset({".kvist"})

# A secret-named symlink must never be skipped: its lexical name is what makes
# it protected, regardless of what it resolves to.  Chains are bounded so a
# malicious or accidental loop cannot hang enumeration.
_MAX_SYMLINK_DEPTH = 40


class UnsafeSecretPathError(ValueError):
    """A protected-named path cannot be safely classified.

    Raised for symlink loops or chains exceeding the depth bound on a
    lexically secret path.  Callers must fail closed — never construct a
    sandbox with an incomplete secret mask.  Subclasses ``ValueError`` so it
    is caught by the same hard-deny translation as other sandbox setup
    failures.
    """


def _is_relative_to(path: Path, boundary: Path) -> bool:
    try:
        path.relative_to(boundary)
    except ValueError:
        return False
    return True


def _resolve_symlink_chain(entry: Path) -> tuple[Path | None, bool]:
    """Follow ``entry``'s symlink chain lexically, without racing readlink/stat.

    Returns ``(final_path, dangling)``.  ``final_path`` is ``None`` only when
    the chain is unsafe (a loop or too many hops) — the caller must fail
    closed in that case.  Hops are resolved textually (no ``os.path.realpath``)
    so a target that does not yet exist is reported as dangling rather than
    raising, and an absolute escape outside the workspace is preserved as-is
    for the caller's own containment check.
    """
    current = entry
    visited: set[Path] = set()
    for _ in range(_MAX_SYMLINK_DEPTH):
        try:
            info = current.lstat()
        except FileNotFoundError:
            return current, True
        except OSError as error:
            raise UnsafeSecretPathError(
                "Cannot build a complete secret mask because a protected "
                "symlink target cannot be inspected"
            ) from error
        if not stat.S_ISLNK(info.st_mode):
            return current, False
        if current in visited:
            return None, False
        visited.add(current)
        try:
            target_text = os.readlink(current)
        except OSError as error:
            raise UnsafeSecretPathError(
                "Cannot build a complete secret mask because a protected "
                "symlink cannot be read"
            ) from error
        target = Path(target_text)
        current = target if target.is_absolute() else (current.parent / target)
        current = Path(os.path.normpath(current))
    return None, False


def is_secret_basename(name: str) -> bool:
    lowered = name.casefold()
    if _ENV_RE.match(name):
        return True
    if name in _SECRET_BASENAMES or lowered in {b.casefold() for b in _SECRET_BASENAMES}:
        return True
    return lowered.endswith(_SECRET_SUFFIXES)


def is_secret_dir_name(name: str) -> bool:
    return name.casefold() in {d.casefold() for d in _SECRET_DIR_NAMES}


def is_secret_path(path: Path) -> bool:
    """Lexical secret classification for a path outside the sandbox mask.

    Bubblewrap masking only covers the workspace.  Extra read-only roots are
    plain host paths read by the Python layer, so they need the same
    classification applied by name before any content is opened.
    """
    if any(is_secret_dir_name(part) for part in path.parts):
        return True
    return is_secret_basename(path.name)


def enumerate_secret_targets(workspace: Path) -> tuple[list[Path], list[Path]]:
    """Return (secret_files, secret_dirs) that must be masked under ``workspace``.

    Masking shadows a *path*, but a secret's protection must follow its *inode*:
    a hardlink is a second directory entry — with an attacker-chosen innocuous
    name — pointing at the same content.  So the whole tree is walked, every
    secret's inode is recorded, and any other path that shares a secret inode
    (a hardlink) is masked too.  Secret directories are masked wholesale but
    still descended into, so hardlinks to files inside them (e.g.
    ``.ssh/id_rsa``) are also covered.

    Classification is lexical-first: a symlink whose own name (or an
    ancestor's name) marks it as secret is never skipped just because it is a
    symlink.  Bubblewrap cannot bind a mask directly onto a symlink node (that
    fails at spawn), so the chain is resolved to its real target and the
    *target* is masked instead — following the lexical name through the
    symlink still lands on the masked target, so no content escapes.  A
    symlink chain that loops or exceeds the depth bound raises
    ``UnsafeSecretPathError``: the caller must fail closed rather than run
    with an incomplete mask.
    """
    try:
        workspace = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise UnsafeSecretPathError(
            "Cannot build a complete secret mask because the workspace "
            "cannot be resolved"
        ) from error
    secret_files: list[Path] = []
    secret_dirs: list[Path] = []
    secret_inodes: set[tuple[int, int]] = set()
    linked_paths: dict[tuple[int, int], list[Path]] = {}
    link_counts: dict[tuple[int, int], int] = {}
    visited_dir_inodes: set[tuple[int, int]] = set()
    stack: list[tuple[Path, bool]] = [(workspace, False)]
    while stack:
        current, in_secret = stack.pop()
        try:
            current_real = current.resolve(strict=True)
            current_info = current.lstat()
            if (
                current_real != current
                or not _is_relative_to(current_real, workspace)
                or not stat.S_ISDIR(current_info.st_mode)
                or stat.S_ISLNK(current_info.st_mode)
            ):
                raise UnsafeSecretPathError(
                    "Cannot build a complete secret mask because a scanned "
                    "directory changed identity or escaped the workspace"
                )
            entries = list(current.iterdir())
        except UnsafeSecretPathError:
            raise
        except (OSError, RuntimeError) as error:
            raise UnsafeSecretPathError(
                "Cannot build a complete secret mask because a workspace "
                "directory cannot be scanned"
            ) from error
        for entry in entries:
            name = entry.name
            try:
                info = entry.lstat()
            except OSError as error:
                raise UnsafeSecretPathError(
                    "Cannot build a complete secret mask because a workspace "
                    "entry cannot be inspected"
                ) from error
            if stat.S_ISLNK(info.st_mode):
                lexically_secret = in_secret or is_secret_basename(name) or is_secret_dir_name(name)
                if not lexically_secret:
                    # A non-secret-named symlink holds no secret bytes; its
                    # target, if a real secret, is masked when the walk
                    # reaches the target's own real path.
                    continue
                final, dangling = _resolve_symlink_chain(entry)
                if final is None:
                    raise UnsafeSecretPathError(
                        f"Protected path '{entry}' is a symlink loop or exceeds the "
                        "maximum resolution depth; refusing to build an incomplete "
                        "secret mask"
                    )
                if dangling:
                    # Nothing exists to leak yet; if it is created later the
                    # mask digest changes and final re-validation catches it.
                    continue
                try:
                    final_real = final.resolve(strict=True)
                except (OSError, RuntimeError) as error:
                    raise UnsafeSecretPathError(
                        "Cannot build a complete secret mask because a "
                        "protected symlink target cannot be resolved"
                    ) from error
                if not _is_relative_to(final_real, workspace):
                    # Nothing outside the workspace is bound into the
                    # sandbox, so the target is already inaccessible; do not
                    # broaden sandbox visibility trying to mask it.
                    continue
                try:
                    target_info = final_real.stat()
                except OSError as error:
                    raise UnsafeSecretPathError(
                        "Cannot build a complete secret mask because a "
                        "protected symlink target cannot be inspected"
                    ) from error
                key = (target_info.st_dev, target_info.st_ino)
                if stat.S_ISDIR(target_info.st_mode):
                    if key in visited_dir_inodes:
                        continue
                    visited_dir_inodes.add(key)
                    if final_real not in secret_dirs:
                        secret_dirs.append(final_real)
                    stack.append((final_real, True))
                elif stat.S_ISREG(target_info.st_mode):
                    secret_inodes.add(key)
                    if final_real not in secret_files:
                        secret_files.append(final_real)
                continue
            if stat.S_ISDIR(info.st_mode):
                if not in_secret and name in _MASKED_DIR_NAMES:
                    continue
                if not in_secret and is_secret_dir_name(name):
                    secret_dirs.append(entry)
                    visited_dir_inodes.add((info.st_dev, info.st_ino))
                    stack.append((entry, True))
                    continue
                stack.append((entry, in_secret))
                continue
            if not stat.S_ISREG(info.st_mode):
                continue
            key = (info.st_dev, info.st_ino)
            is_secret = in_secret or is_secret_basename(name)
            if is_secret:
                secret_inodes.add(key)
                if not in_secret:
                    secret_files.append(entry)
            if info.st_nlink > 1:
                linked_paths.setdefault(key, []).append(entry)
                link_counts[key] = max(link_counts.get(key, 0), info.st_nlink)

    masked = {p.resolve() for p in secret_files}
    protected_inodes = set(secret_inodes)
    for key, paths in linked_paths.items():
        # A regular inode reporting more links than were found during the
        # complete workspace walk has at least one alias outside the mounted
        # workspace (or in masked internal state). Binding any visible alias
        # would otherwise import out-of-workspace bytes into Bubblewrap.
        if link_counts[key] > len(paths):
            protected_inodes.add(key)
    for key in protected_inodes:
        for path in linked_paths.get(key, ()):
            if path.resolve() not in masked:
                masked.add(path.resolve())
                secret_files.append(path)

    secret_files.sort()
    secret_dirs.sort()
    return secret_files, secret_dirs


def count_workspace_inode_aliases(workspace: Path, key: tuple[int, int]) -> int:
    """Count workspace names that share one ``(st_dev, st_ino)`` regular inode.

    A caller compares the result with ``st_nlink`` to prove that every name of
    a multiply-linked file lives inside the workspace.  ``.kvist`` is not
    descended into, exactly as the mask walk does, so an alias hidden there is
    simply not counted and containment stays unproven.  Symlinks are skipped
    (they hold no inode alias) which also bounds the walk.
    """
    try:
        root = workspace.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise UnsafeSecretPathError(
            "Cannot prove hardlink containment because the workspace cannot "
            "be resolved"
        ) from error
    count = 0
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except (OSError, RuntimeError) as error:
            raise UnsafeSecretPathError(
                "Cannot prove hardlink containment because a workspace "
                "directory cannot be scanned"
            ) from error
        for entry in entries:
            try:
                info = entry.lstat()
            except OSError as error:
                raise UnsafeSecretPathError(
                    "Cannot prove hardlink containment because a workspace "
                    "entry cannot be inspected"
                ) from error
            if stat.S_ISLNK(info.st_mode):
                continue
            if stat.S_ISDIR(info.st_mode):
                if entry.name not in _MASKED_DIR_NAMES:
                    stack.append(entry)
                continue
            if stat.S_ISREG(info.st_mode) and (info.st_dev, info.st_ino) == key:
                count += 1
    return count


def secret_mask_digest(workspace: Path) -> str:
    """Stable digest of the protected-path set for approval binding."""
    files, dirs = enumerate_secret_targets(workspace)
    payload_lines: list[str] = []
    for kind, paths in (("f", files), ("d", dirs)):
        for path in paths:
            try:
                rel = path.resolve().relative_to(workspace).as_posix()
            except (ValueError, OSError):
                rel = path.name
            payload_lines.append(f"{kind}:{rel}")
    payload = "\n".join(sorted(payload_lines))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
