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


def is_secret_basename(name: str) -> bool:
    lowered = name.casefold()
    if _ENV_RE.match(name):
        return True
    if name in _SECRET_BASENAMES or lowered in {b.casefold() for b in _SECRET_BASENAMES}:
        return True
    return lowered.endswith(_SECRET_SUFFIXES)


def is_secret_dir_name(name: str) -> bool:
    return name.casefold() in {d.casefold() for d in _SECRET_DIR_NAMES}


def enumerate_secret_targets(workspace: Path) -> tuple[list[Path], list[Path]]:
    """Return (secret_files, secret_dirs) that must be masked under ``workspace``.

    Masking shadows a *path*, but a secret's protection must follow its *inode*:
    a hardlink is a second directory entry — with an attacker-chosen innocuous
    name — pointing at the same content.  So the whole tree is walked (symlinks
    are never followed), every secret's inode is recorded, and any other path
    that shares a secret inode (a hardlink) is masked too.  Secret directories
    are masked wholesale but still descended into, so hardlinks to files inside
    them (e.g. ``.ssh/id_rsa``) are also covered.
    """
    workspace = workspace.resolve()
    secret_files: list[Path] = []
    secret_dirs: list[Path] = []
    secret_inodes: set[tuple[int, int]] = set()
    linked_paths: dict[tuple[int, int], list[Path]] = {}
    stack: list[tuple[Path, bool]] = [(workspace, False)]
    while stack:
        current, in_secret = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue
        for entry in entries:
            name = entry.name
            try:
                info = entry.lstat()
            except OSError:
                continue
            if stat.S_ISLNK(info.st_mode):
                # A symlink holds no secret bytes; its target, if a real secret,
                # is masked when the walk reaches the target's real path.
                continue
            if stat.S_ISDIR(info.st_mode):
                if not in_secret and name in _MASKED_DIR_NAMES:
                    continue
                if not in_secret and is_secret_dir_name(name):
                    secret_dirs.append(entry)
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

    masked = {p.resolve() for p in secret_files}
    for key in secret_inodes:
        for path in linked_paths.get(key, ()):
            if path.resolve() not in masked:
                masked.add(path.resolve())
                secret_files.append(path)

    secret_files.sort()
    secret_dirs.sort()
    return secret_files, secret_dirs


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
