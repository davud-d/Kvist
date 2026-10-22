# -*- coding: utf-8 -*-
"""Filesystem identity and path helpers shared by the terminal modules."""
from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from kernel.sandbox.terminal_types import FileIdentity


def _path_identity(path: Path) -> FileIdentity | None:
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return None
    return (
        str(resolved),
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _directory_identity(path: Path) -> tuple[str, int, int] | None:
    # No size/mtime/ctime: they change whenever an entry is created or removed
    # in the directory, which is not a change of the directory itself.
    try:
        resolved = path.resolve(strict=True)
        info = resolved.stat()
    except OSError:
        return None
    return (str(resolved), info.st_dev, info.st_ino)


def _path_entry_identity(path: Path) -> FileIdentity | None:
    try:
        absolute = Path(os.path.abspath(path))
        info = absolute.lstat()
    except OSError:
        return None
    return (
        str(absolute),
        info.st_dev,
        info.st_ino,
        info.st_size,
        info.st_mtime_ns,
        info.st_ctime_ns,
    )


def _path_entry_is_symlink(path: Path | None) -> bool:
    """Return whether the exact lookup entry is a symlink, without following it."""
    if path is None:
        return False
    try:
        return stat.S_ISLNK(Path(os.path.abspath(path)).lstat().st_mode)
    except OSError:
        return False


def _is_relative_to(path: Path, boundary: Path) -> bool:
    try:
        path.relative_to(boundary)
    except ValueError:
        return False
    return True


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _find_parent_file(start: Path, boundary: Path, name: str) -> Path | None:
    current = start.resolve()
    boundary = boundary.resolve()
    while True:
        candidate = current / name
        if candidate.is_file():
            return candidate.resolve()
        if current == boundary:
            return None
        if boundary not in current.parents:
            return None
        current = current.parent
