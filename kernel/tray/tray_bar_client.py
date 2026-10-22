# -*- coding: utf-8 -*-
"""Commands the status bar runs: the state stream and the click toggle.

Both talk to the tray daemon over the endpoint in ``~/.kvist/tray.json`` as a
``bar`` client (see docs/UI_API.md §8e). Neither starts the daemon: it exists
while a terminal core runs, and the icon is hidden while it does not.
"""
from __future__ import annotations

import json
import socket
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TextIO

from kernel.tray.tray_wire import endpoint_file, render_state

_RETRY_SECONDS = 1.0
_CONNECT_TIMEOUT_SECONDS = 1.0


def _connect(endpoint: Path) -> socket.socket | None:
    try:
        stored = json.loads(endpoint.read_text(encoding="utf-8"))
        connection = socket.create_connection(("127.0.0.1", int(stored["port"])), _CONNECT_TIMEOUT_SECONDS)
        connection.settimeout(None)
        hello = {"type": "hello", "token": stored["token"], "role": "bar"}
        connection.sendall((json.dumps(hello) + "\n").encode("utf-8"))
    except (OSError, KeyError, ValueError):
        # No daemon, a stale endpoint file or a daemon that is starting: the icon stays hidden and the caller retries.
        return None
    return connection


def run_stream(
    bar: str,
    *,
    endpoint: Callable[[], Path] = endpoint_file,
    out: TextIO | None = None,
    sleep: Callable[[float], None] = time.sleep,
    keep_running: Callable[[], bool] = lambda: True,
) -> None:
    """Print one line per state change for ``bar`` until the process is killed (the bar owns its lifetime)."""
    out = out or sys.stdout
    hidden = render_state(bar, "idle", 0, "")
    while keep_running():
        print(hidden, file=out, flush=True)
        connection = _connect(endpoint())
        if connection is None:
            sleep(_RETRY_SECONDS)
            continue
        with connection, connection.makefile("r", encoding="utf-8") as lines:
            for line in lines:
                summary = json.loads(line)
                print(render_state(bar, summary["state"], summary["count"], summary["tooltip"]), file=out, flush=True)
        sleep(_RETRY_SECONDS)


def send_toggle(*, endpoint: Callable[[], Path] = endpoint_file) -> bool:
    """Ask the daemon to open or close the panel; False when no daemon is running."""
    connection = _connect(endpoint())
    if connection is None:
        return False
    with connection:
        connection.sendall((json.dumps({"type": "toggle"}) + "\n").encode("utf-8"))
    return True


def main(argv: list[str]) -> int:
    """Entry for ``__main__.py``: ``--tray-op bar-stream [--bar NAME]`` or ``--tray-op toggle``."""
    if argv[argv.index("--tray-op") + 1] == "toggle":
        return 0 if send_toggle() else 1
    bar = argv[argv.index("--bar") + 1] if "--bar" in argv else "waybar"
    try:
        run_stream(bar)
    except BrokenPipeError:
        # The bar closed our stdout: it is shutting this module down, so there is nobody left to print for.
        return 0
    return 0
