# -*- coding: utf-8 -*-
"""``kvist-core --remote-attach``: the server end of the ssh pipe, a plain relay between stdio and the daemon's socket.

It has no protocol of its own. When ssh drops, stdin ends and the relay ends; the daemon keeps working.
"""
from __future__ import annotations

import os
import socket
import threading

from kernel.ssh import wire
from kernel.ssh.daemon import require_posix
from kernel.ssh.paths import daemon_paths


def _stdin_to_socket(connection: socket.socket) -> None:
    while True:
        chunk = os.read(0, 65536)
        if not chunk:
            break
        try:
            connection.sendall(chunk)
        except OSError:
            return
    try:
        connection.shutdown(socket.SHUT_WR)
    except OSError:
        pass


def run_attach(workspace: str) -> int:
    require_posix()
    paths = daemon_paths(workspace)
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(paths.socket))
    except (FileNotFoundError, ConnectionRefusedError):
        os.write(1, wire.encode({"type": wire.STATE, "state": "no_service"}))
        return wire.EXIT_NO_SERVICE
    threading.Thread(target=_stdin_to_socket, args=(connection,), daemon=True).start()
    while True:
        chunk = connection.recv(65536)
        if not chunk:
            return 0
        try:
            view = memoryview(chunk)
            while view:
                view = view[os.write(1, view):]
        except BrokenPipeError:
            return 0
