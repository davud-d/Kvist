# -*- coding: utf-8 -*-
"""Where a remote core keeps its files on the server, and the empty folder a frontend gets locally."""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path

from kernel.config.user_config import get_user_config_path

# sun_path holds 104 bytes on macOS and the BSDs; leave room for the terminator.
_MAX_SOCKET_PATH = 100


class RemoteError(RuntimeError):
    """A failure the person can act on; the text says how."""


def remote_root() -> Path:
    return get_user_config_path().parent / "remote"


def _digest(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def daemon_id(workspace: str | Path) -> str:
    return _digest(os.path.realpath(os.path.expanduser(str(workspace))))


@dataclass(frozen=True, slots=True)
class DaemonPaths:
    id: str
    root: Path
    log: Path
    socket: Path
    lock: Path
    info: Path
    stderr: Path


def daemon_paths(workspace: str | Path) -> DaemonPaths:
    identity = daemon_id(workspace)
    root = remote_root() / identity
    socket_path = root / "sock"
    if len(str(socket_path)) > _MAX_SOCKET_PATH:
        raise RemoteError(
            f"The socket path {socket_path} is too long for a Unix socket; "
            "point KVIST_CONFIG_DIR at a shorter folder."
        )
    return DaemonPaths(
        id=identity,
        root=root,
        log=root / "events.ndjson",
        socket=socket_path,
        lock=root / "daemon.lock",
        info=root / "daemon.json",
        stderr=root / "daemon.log",
    )


def local_placeholder(host: str, remote_workspace: str) -> Path:
    """An empty folder that stands in for the workspace on the machine the frontend runs on."""
    folder = remote_root() / "local" / _digest(f"{host}:{remote_workspace}")
    folder.mkdir(parents=True, exist_ok=True)
    return folder
