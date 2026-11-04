# -*- coding: utf-8 -*-
"""``--remote-op``: what runs on the server to look at, start, stop and install the daemon.

Every operation prints one JSON line (``{"ok": true, ...}`` or ``{"ok": false, "error": ...}``) so the
local side, which runs these over ssh, can read the answer without parsing prose.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

from kernel.config.user_config import get_saved_provider_or_none
from kernel.config.version import kvist_version
from kernel.protocol.frontend_protocol import PROTOCOL_VERSION
from kernel.ssh import service, wire
from kernel.ssh.daemon import daemon_running, require_posix
from kernel.ssh.paths import DaemonPaths, RemoteError, daemon_paths

OPERATIONS = ("probe", "status", "start", "stop", "install-service", "uninstall-service", "mkdir")
_START_SECONDS = 20.0
_ASK_SECONDS = 5.0


def ask_daemon(paths: DaemonPaths, operation: str) -> dict | None:
    """One request to the live daemon, or None when none answers."""
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(_ASK_SECONDS)
    try:
        connection.connect(str(paths.socket))
        connection.sendall(wire.encode({"type": wire.HELLO, "op": operation}))
        reply = connection.makefile("rb").readline()
    except OSError:
        return None
    finally:
        connection.close()
    return wire.decode(reply)


def _wait_until(condition, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return condition()


def probe(workspace: str) -> dict:
    """Is a Kvist here, can it work, and what is it doing for this folder."""
    paths = daemon_paths(workspace)
    return {
        "kvist_version": kvist_version(),
        "protocol_version": PROTOCOL_VERSION,
        "remote_revision": wire.REVISION,
        "platform": sys.platform,
        "provider_ready": bool(os.environ.get("AGENT_LLM_PROVIDER") or get_saved_provider_or_none()),
        "workspace_exists": os.path.isdir(os.path.expanduser(workspace)),
        "daemon": ask_daemon(paths, "status"),
        "service": service.status(workspace),
    }


def status(workspace: str) -> dict:
    reply = ask_daemon(daemon_paths(workspace), "status")
    return {"running": reply is not None, "daemon": reply}


def start(workspace: str) -> dict:
    """Run the daemon once, detached; it lives until the machine restarts or ``stop``."""
    paths = daemon_paths(workspace)
    if daemon_running(paths):
        return {"started": False, "daemon": ask_daemon(paths, "status")}
    process = subprocess.Popen(
        service.daemon_argv(workspace),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    reply = None

    def answered() -> bool:
        nonlocal reply
        reply = ask_daemon(paths, "status")
        return reply is not None or process.poll() is not None

    _wait_until(answered, _START_SECONDS)
    if reply is None:
        raise RemoteError(f"The daemon did not come up (exit {process.poll()}); see {paths.stderr}.")
    return {"started": True, "daemon": reply}


def stop(workspace: str) -> dict:
    paths = daemon_paths(workspace)
    if ask_daemon(paths, "stop") is None:
        return {"stopped": False}
    if not _wait_until(lambda: not daemon_running(paths), _START_SECONDS):
        raise RemoteError(f"The daemon was asked to stop but is still running; see {paths.stderr}.")
    return {"stopped": True}


def make_folder(workspace: str) -> dict:
    """Create the folder the daemon will work in (and the ones above it)."""
    folder = Path(workspace).expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    return {"folder": str(folder)}


def install_service(workspace: str) -> dict:
    result = service.install(workspace)
    # cron only starts the daemon at the next boot.
    started = start(workspace) if result.manager == service.CRON else {}
    return {"manager": result.manager, "path": result.path, "notes": result.notes, **started}


def uninstall_service(workspace: str) -> dict:
    manager = service.uninstall(workspace)
    return {"manager": manager, **stop(workspace)}


_HANDLERS = {
    "probe": probe,
    "status": status,
    "start": start,
    "stop": stop,
    "install-service": install_service,
    "uninstall-service": uninstall_service,
    "mkdir": make_folder,
}


def run_op(operation: str, workspace: str) -> int:
    try:
        require_posix()
        answer = {"ok": True, **_HANDLERS[operation](workspace)}
        code = 0
    except (RemoteError, subprocess.SubprocessError, OSError) as error:
        answer, code = {"ok": False, "error": str(error)}, 1
    print(json.dumps(answer))
    return code
