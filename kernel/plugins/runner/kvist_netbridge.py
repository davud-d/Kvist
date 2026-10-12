# -*- coding: utf-8 -*-
"""Byte bridges between a sandbox's private loopback and a core-owned unix socket.

A sandbox has its own network namespace with nothing but loopback, so a
program in it (a browser, a dev server) reaches the outside only through a
unix socket the core bind-mounted in. The bridge copies bytes and decides
nothing: every destination check happens in the core's proxy, on the other
side of the socket, where the skill cannot change it.
"""
from __future__ import annotations

import socket
import threading

_CHUNK = 65536


def start_tcp_to_unix_bridge(port: int, unix_path: str) -> threading.Thread:
    """Accept on 127.0.0.1:``port`` and forward each connection to ``unix_path``."""
    server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(("127.0.0.1", port))
    server.listen(64)

    def connect() -> socket.socket:
        upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        upstream.connect(unix_path)
        return upstream

    return _serve(server, connect)


def start_unix_to_tcp_bridge(unix_path: str, port: int) -> threading.Thread:
    """Accept on ``unix_path`` and forward each connection to 127.0.0.1:``port`` (a task service)."""
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(unix_path)
    server.listen(64)
    return _serve(server, lambda: socket.create_connection(("127.0.0.1", port), timeout=10))


def _serve(server: socket.socket, connect) -> threading.Thread:
    def accept_loop() -> None:
        while True:
            try:
                client, _address = server.accept()
            except OSError:
                return
            threading.Thread(target=_handle, args=(client, connect), daemon=True).start()

    thread = threading.Thread(target=accept_loop, name="kvist-netbridge", daemon=True)
    thread.start()
    return thread


def _handle(client: socket.socket, connect) -> None:
    try:
        upstream = connect()
    except OSError:
        client.close()
        return
    upstream.settimeout(None)
    threading.Thread(target=_pump, args=(client, upstream), daemon=True).start()
    _pump(upstream, client)


def _pump(source: socket.socket, target: socket.socket) -> None:
    try:
        while True:
            data = source.recv(_CHUNK)
            if not data:
                break
            target.sendall(data)
    except OSError:
        # Either side closed or reset; the shutdown below ends the other direction.
        source.close()
    try:
        target.shutdown(socket.SHUT_WR)
    except OSError:
        target.close()
