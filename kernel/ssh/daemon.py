# -*- coding: utf-8 -*-
"""The core as a long-lived process on the server: ``launch_multi`` with a log for output and a socket for input.

One daemon serves one workspace. Frontends come and go over a Unix socket; the core never sees
stdin close. Only one attached frontend (the driver, see ``lease.py``) may write.
"""
from __future__ import annotations

import json
import os
import signal
import socket
import threading
import time
from collections.abc import Callable
from pathlib import Path

from kernel.config.version import kvist_version
from kernel.protocol.frontend_protocol import PROTOCOL_VERSION
from kernel.ssh import messages, wire
from kernel.ssh.event_log import EventLog, fresh_replay, lines_from, request_id
from kernel.ssh.inbox import ClientInbox
from kernel.ssh.lease import Lease
from kernel.ssh.paths import DaemonPaths, RemoteError, daemon_paths

_MAX_LINE = 64 * 1024 * 1024
_FLUSH_BYTES = 64 * 1024
_HELLO_SECONDS = 10.0
_REAP_SECONDS = 2.0


def require_posix() -> None:
    if os.name != "posix":
        raise RemoteError("The remote core runs on Linux, macOS and BSD systems, not on Windows.")


def private_directory(path: Path) -> None:
    """Create ``path`` for this user alone, or refuse a folder someone else could write to."""
    path.mkdir(parents=True, mode=0o700, exist_ok=True)
    status = path.stat()
    if status.st_uid != os.getuid() or status.st_mode & 0o077:
        raise RemoteError(f"{path} must belong to you and be closed to everyone else (chmod 700).")


def hold_lock(path: Path):
    """The lock lives as long as the returned file object; a held lock means a daemon is running."""
    import fcntl

    handle = open(path, "a+b")
    try:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as error:
        handle.close()
        raise RemoteError("A Kvist daemon already serves this workspace.") from error
    return handle


def daemon_running(paths: DaemonPaths) -> bool:
    import fcntl

    if not paths.lock.exists():
        return False
    with open(paths.lock, "a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        return False


class _Client:
    """One attached frontend connection."""

    def __init__(self, sock: socket.socket, client_id: str, language: str) -> None:
        self.sock = sock
        self.id = client_id
        self.language = language
        # A frontend refuses a stream that does not open with ``hello``, so nothing else goes before it.
        self.greeted = False
        self.closed = False
        self._send_lock = threading.Lock()

    def send(self, data: bytes | dict) -> bool:
        payload = data if isinstance(data, bytes) else wire.encode(data)
        with self._send_lock:
            if self.closed:
                return False
            try:
                self.sock.sendall(payload)
            except OSError:
                self.closed = True
                return False
        return True

    def close(self) -> None:
        self.closed = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.sock.close()


class Daemon:
    def __init__(self, paths: DaemonPaths, log: EventLog, inbox: ClientInbox, lease: Lease | None = None) -> None:
        self.paths = paths
        self.log = log
        self.inbox = inbox
        self.lease = lease or Lease()
        self.session = None
        self._clients: list[_Client] = []
        self._clients_lock = threading.Lock()
        self._server: socket.socket | None = None
        self._stopping = threading.Event()
        self.since = time.time()

    # --- the core side -------------------------------------------------------

    def adopt_session(self, session) -> None:
        self.session = session

    def _open_requests(self) -> set[str]:
        return set(self.session.open_request_ids()) if self.session is not None else set()

    # --- sockets -------------------------------------------------------------

    def listen(self) -> None:
        if self.paths.socket.exists():
            self.paths.socket.unlink()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        previous = os.umask(0o177)
        try:
            server.bind(str(self.paths.socket))
        finally:
            os.umask(previous)
        server.listen(16)
        self._server = server
        threading.Thread(target=self._accept_loop, daemon=True, name="remote-accept").start()
        threading.Thread(target=self._reap_loop, daemon=True, name="remote-reap").start()

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._stopping.is_set():
            try:
                connection, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(connection,), daemon=True, name="remote-client").start()

    def _reap_loop(self) -> None:
        while not self._stopping.wait(_REAP_SECONDS):
            lost = self.lease.reap()
            if lost is None:
                continue
            for client in self._clients_named(lost):
                client.close()
            self._broadcast_control()

    def _clients_named(self, client_id: str) -> list[_Client]:
        with self._clients_lock:
            return [client for client in self._clients if client.id == client_id]

    def close(self) -> None:
        self._stopping.set()
        if self._server is not None:
            self._server.close()
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            client.close()
        self.paths.socket.unlink(missing_ok=True)

    # --- one connection ------------------------------------------------------

    def _handle(self, connection: socket.socket) -> None:
        reader = connection.makefile("rb")
        connection.settimeout(_HELLO_SECONDS)
        try:
            hello = wire.decode(reader.readline(_MAX_LINE))
        except OSError:
            hello = None
        connection.settimeout(None)
        if hello is None or hello.get("type") != wire.HELLO:
            connection.close()
            return
        operation = hello.get("op")
        if operation == "attach":
            self._attach(connection, reader, hello)
        elif operation == "stop":
            self._stop_requested(connection)
        elif operation == "status":
            self._send_once(connection, self.status())
        else:
            connection.close()

    def _send_once(self, connection: socket.socket, payload: dict) -> None:
        try:
            connection.sendall(wire.encode(payload))
        except OSError:
            pass
        connection.close()

    def status(self) -> dict:
        with self._clients_lock:
            attached = len(self._clients)
        return {
            "type": "remote_status",
            "pid": os.getpid(),
            "kvist_version": kvist_version(),
            "protocol_version": PROTOCOL_VERSION,
            "segment": self.log.segment,
            "since": self.since,
            "attached": attached,
            "driver_idle_seconds": self.lease.idle_seconds(),
            "open_requests": len(self._open_requests()),
        }

    def request_stop(self) -> None:
        # A turn in flight would keep the loop from noticing the end of input.
        if self.session is not None:
            self.session.cancel()
        self.inbox.close()

    def _stop_requested(self, connection: socket.socket) -> None:
        self.request_stop()
        self._send_once(connection, {"type": "remote_stopped"})

    # --- an attached frontend ------------------------------------------------

    def _attach(self, connection: socket.socket, reader, hello: dict) -> None:
        start = hello.get("from")
        wanted = hello.get("segment")
        client_id = hello.get("client") if isinstance(hello.get("client"), str) else f"anonymous-{id(connection)}"
        client = _Client(connection, client_id, "ru" if hello.get("lang") == "ru" else "en")
        if start is not None and wanted != self.log.segment:
            client.send({"type": wire.ATTACHED, "ok": False, "reason": "segment"})
            client.close()
            return
        segment = self.log.attach_reader()
        try:
            self._register(client)
            self.lease.claim_if_free(client.id)
            client.send({"type": wire.ATTACHED, "ok": True, "segment": segment})
            threading.Thread(target=self._stream, args=(client, start), daemon=True, name="remote-stream").start()
            self._read_loop(client, reader)
        finally:
            client.close()
            self.log.detach_reader()
            if self._unregister(client) and self.lease.release(client.id):
                self._broadcast_control()

    def _register(self, client: _Client) -> None:
        # A frontend that reconnects supersedes its own half-dead earlier connection.
        for stale in self._clients_named(client.id):
            stale.close()
        with self._clients_lock:
            self._clients.append(client)

    def _unregister(self, client: _Client) -> bool:
        """True when no other connection of the same frontend remains."""
        with self._clients_lock:
            if client in self._clients:
                self._clients.remove(client)
            return all(other.id != client.id for other in self._clients)

    def _read_loop(self, client: _Client, reader) -> None:
        while not client.closed:
            try:
                raw = reader.readline(_MAX_LINE)
            except OSError:
                return
            if not raw:
                return
            self._route(client, raw)

    def _route(self, client: _Client, raw: bytes) -> None:
        message = wire.decode(raw)
        kind = message.get("type", "") if message is not None else ""
        if kind == wire.PING:
            self.lease.touch(client.id)
        elif kind == wire.TAKEOVER:
            self.lease.take(client.id)
            self._broadcast_control()
            self._resend_open_requests(client)
        elif kind.startswith("remote_"):
            return
        elif self.lease.is_driver(client.id):
            self.lease.touch(client.id)
            self.inbox.put(raw.decode("utf-8", "replace"))
        else:
            self._push_control(client)

    def _resend_open_requests(self, client: _Client) -> None:
        """A frontend that only watched never drew the dialogs that were open; answering them is its job now."""
        open_requests = self._open_requests()
        if not open_requests:
            return
        for raw, _ in lines_from(self.paths.log, 0):
            if wire.event_type(raw) == "input_request" and request_id(raw) in open_requests:
                client.send(raw)

    # --- what a frontend is told about its role ------------------------------

    def _control(self, client: _Client) -> dict:
        if self.lease.is_driver(client.id):
            return {"type": wire.CONTROL, "role": "driver", "notice": ""}
        with self._clients_lock:
            driver_present = any(
                other is not client and self.lease.is_driver(other.id) for other in self._clients
            )
        key = "observer" if driver_present else "observer_gone"
        return {
            "type": wire.CONTROL,
            "role": "observer",
            "notice": messages.text(key, client.language),
            "holder_idle_seconds": round(self.lease.idle_seconds(), 1),
        }

    def _push_control(self, client: _Client) -> None:
        if client.greeted:
            client.send(self._control(client))

    def _broadcast_control(self) -> None:
        with self._clients_lock:
            clients = list(self._clients)
        for client in clients:
            self._push_control(client)

    # --- the stream: replay, then tail ---------------------------------------

    def _stream(self, client: _Client, start: int | None) -> None:
        buffer = bytearray()

        def flush() -> None:
            if buffer:
                client.send(bytes(buffer))
                buffer.clear()

        def deliver(raw: bytes) -> None:
            buffer.extend(raw)
            if wire.event_type(raw) == "manifest" and not client.greeted:
                flush()
                client.greeted = True
                self._push_control(client)
            elif len(buffer) >= _FLUSH_BYTES:
                flush()

        position = start or 0
        if start is not None:
            client.greeted = True
            self._push_control(client)
            lines = lines_from(self.paths.log, position)
        else:
            lines = fresh_replay(self.paths.log, self._open_requests())
        while not client.closed:
            moved = False
            for raw, end in lines:
                if raw is not None:
                    deliver(raw)
                position, moved = end, True
            flush()
            if moved:
                client.send({"type": wire.OFFSET, "segment": self.log.segment, "offset": position})
            self.log.wait_for_growth(position, 1.0)
            lines = lines_from(self.paths.log, position)
        flush()


def write_info(paths: DaemonPaths, workspace: str, daemon: Daemon) -> None:
    info = {
        "pid": os.getpid(),
        "workspace": workspace,
        "kvist_version": kvist_version(),
        "protocol_version": PROTOCOL_VERSION,
        "since": daemon.since,
    }
    paths.info.write_text(json.dumps(info), encoding="utf-8")
    paths.info.chmod(0o600)


def redirect_stderr(paths: DaemonPaths) -> None:
    descriptor = os.open(paths.stderr, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    os.dup2(descriptor, 2)
    os.close(descriptor)


def run_daemon(
    workspace: str,
    *,
    serve: Callable[..., None],
    **serve_options,
) -> None:
    """Serve ``workspace`` until a stop request; ``serve`` is ``event_stdio.launch_multi`` with its agent builder bound."""
    require_posix()
    paths = daemon_paths(workspace)
    private_directory(paths.root)
    lock = hold_lock(paths.lock)
    redirect_stderr(paths)
    language = messages.ui_language()
    log = EventLog(paths.log, after_manifest=_restart_line(language) if paths.info.exists() else None)
    daemon = Daemon(paths, log, ClientInbox())
    # The service manager stops a daemon with SIGTERM; ending like a stop request lets the agent clean up.
    signal.signal(signal.SIGTERM, lambda *_: daemon.request_stop())
    try:
        daemon.listen()
        write_info(paths, workspace, daemon)
        serve(
            workspace,
            in_stream=daemon.inbox,
            out_stream=log,
            wake=True,
            on_ready=daemon.adopt_session,
            **serve_options,
        )
    finally:
        daemon.close()
        paths.info.unlink(missing_ok=True)
        lock.close()


def _restart_line(language: str) -> str:
    event = {"type": "message", "markdown": messages.text("restarted", language), "source": "notice"}
    return json.dumps(event) + "\n"
