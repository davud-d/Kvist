# -*- coding: utf-8 -*-
"""Keeps one WebDriver BiDi session with a Firefox-family browser for as long as the browser runs.

Firefox allows one session at a time, and up to at least 140 it cannot end a session
while the window has a tab that was restored but never opened: ``session.end`` answers
success and the session stays, so the browser refuses every later client until it
restarts. Kvist starts an MCP server per task and stops it with the task, so the
session cannot belong to that server. It belongs to this relay instead, started once by
the person's desktop (``kvist --browser-relay --browser-endpoint 9223``).

MCP servers talk to the relay over a private Unix socket in newline-delimited JSON,
in BiDi's own message format. The relay answers ``session.new`` and ``session.end``
itself, renumbers commands, and when a server goes away for any reason it removes the
preload scripts that server added and releases the keys and buttons it held down.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
import queue
import socket
import sys
import tempfile
import threading
from pathlib import Path
from typing import Any

from kernel.browser_mcp.websocket import WebSocket, WebSocketError

START_TIMEOUT = 15.0
MAX_LINE = 64 * 1024 * 1024


def socket_path(port: int) -> Path:
    # MCP servers run with a minimal environment, so XDG_RUNTIME_DIR may be unset there.
    runtime = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    folder = Path(runtime) if Path(runtime).is_dir() else Path(tempfile.gettempdir())
    return folder / f"kvist-browser-relay-{port}.sock"


class LineSocket:
    """The client end: the same send/recv/close as a WebSocket, so drivers.Rpc can use either."""

    def __init__(self, path: Path, timeout: float = 3.0) -> None:
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.settimeout(timeout)
        try:
            self._sock.connect(str(path))
        except OSError as error:
            self._sock.close()
            raise WebSocketError(f"the browser relay at {path} is not running: {error}") from None
        self._sock.settimeout(None)
        self._reader = self._sock.makefile("rb")
        self._lock = threading.Lock()

    def send(self, text: str) -> None:
        with self._lock:
            try:
                self._sock.sendall(text.encode("utf-8") + b"\n")
            except OSError as error:
                raise WebSocketError(f"the browser relay went away: {error}") from None

    def recv(self) -> str:
        try:
            line = self._reader.readline(MAX_LINE)
        except OSError as error:
            raise WebSocketError(f"the browser relay went away: {error}") from None
        if not line:
            raise WebSocketError("the browser relay closed the connection")
        return line.decode("utf-8", errors="replace")

    def close(self) -> None:
        # close() alone keeps the connection open while the reader file still refers to it.
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # already disconnected
        self._sock.close()


class Client:
    def __init__(self, conn: socket.socket) -> None:
        self.conn = conn
        self.lock = threading.Lock()
        self.preloads: set[str] = set()
        self.contexts: set[str] = set()

    def send(self, message: dict[str, Any]) -> None:
        data = json.dumps(message).encode("utf-8") + b"\n"
        with self.lock:
            try:
                self.conn.sendall(data)
            except OSError:
                pass  # the client is leaving; its reader thread cleans up


class Relay:
    def __init__(self, ws_url: str, connect: Any = WebSocket) -> None:
        self.ws_url = ws_url
        self._connect = connect
        self._ws: Any = None
        self._session: dict[str, Any] = {}
        self._ids = itertools.count(1)
        self._pending: dict[int, tuple[Client | None, Any, str, dict[str, Any], queue.Queue | None]] = {}
        self._clients: set[Client] = set()
        self._lock = threading.Lock()
        self._link_lock = threading.Lock()

    # --- the browser side ------------------------------------------------------------

    def _link(self) -> None:
        """Connect and start the session on first use, and again after the browser restarted."""
        with self._link_lock:
            if self._ws is not None:
                return
            ws = self._connect(self.ws_url)
            self._ws = ws
            threading.Thread(target=self._read_browser, args=(ws,), name="kvist-relay-browser", daemon=True).start()
            reply = self._ask("session.new", {"capabilities": {"alwaysMatch": {
                "unhandledPromptBehavior": {"default": "ignore"}}}})
            if reply.get("type") != "success":
                self._drop(ws)
                raise WebSocketError(f"session.new: {reply.get('message') or reply.get('error')}")
            self._session = reply.get("result") or {}

    def _ask(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        answer: queue.Queue = queue.Queue(maxsize=1)
        self._forward(None, None, method, params, answer)
        try:
            return answer.get(timeout=START_TIMEOUT)
        except queue.Empty:
            return {"type": "error", "message": f"the browser did not answer {method}"}

    def _forward(self, client: Client | None, client_id: Any, method: str, params: dict[str, Any],
                 answer: queue.Queue | None = None) -> None:
        ws = self._ws
        if ws is None:
            raise WebSocketError("no browser connection")
        relay_id = next(self._ids)
        with self._lock:
            self._pending[relay_id] = (client, client_id, method, params, answer)
        ws.send(json.dumps({"id": relay_id, "method": method, "params": params}))

    def _read_browser(self, ws: WebSocket) -> None:
        try:
            while True:
                message = json.loads(ws.recv())
                if isinstance(message, dict):
                    self._route(message)
        except (WebSocketError, json.JSONDecodeError) as error:
            print(f"kvist-browser-relay: browser connection ended: {error}", file=sys.stderr, flush=True)
            self._drop(ws)

    def _route(self, message: dict[str, Any]) -> None:
        if "id" not in message:
            with self._lock:
                clients = list(self._clients)
            for client in clients:
                client.send(message)
            return
        with self._lock:
            entry = self._pending.pop(message["id"], None)
        if entry is None:
            return
        client, client_id, method, params, answer = entry
        if answer is not None:
            answer.put(message)
            return
        if client is None:
            return
        if message.get("type") == "success":
            self._note(client, method, params, message.get("result") or {})
        client.send({**message, "id": client_id})

    @staticmethod
    def _note(client: Client, method: str, params: dict[str, Any], result: dict[str, Any]) -> None:
        if method == "script.addPreloadScript" and result.get("script"):
            client.preloads.add(result["script"])
        elif method == "script.removePreloadScript":
            client.preloads.discard(params.get("script"))
        elif method == "input.performActions" and params.get("context"):
            client.contexts.add(params["context"])

    def _drop(self, ws: WebSocket) -> None:
        with self._link_lock:
            if self._ws is not ws:
                return
            self._ws = None
            self._session = {}
        ws.close()
        with self._lock:
            pending, self._pending = self._pending, {}
            clients = list(self._clients)
        for client, client_id, _method, _params, answer in pending.values():
            failure = {"type": "error", "error": "unknown error", "message": "the browser connection closed"}
            if answer is not None:
                answer.put(failure)
            elif client is not None:
                client.send({**failure, "id": client_id})
        # Servers notice the closed socket and reconnect through a fresh link on their next call.
        for client in clients:
            try:
                client.conn.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass  # that client is gone already

    # --- the client side -------------------------------------------------------------

    def serve_client(self, conn: socket.socket) -> None:
        client = Client(conn)
        with self._lock:
            self._clients.add(client)
        try:
            for line in conn.makefile("rb"):
                self._handle(client, line)
        except OSError:
            pass  # the client is gone; the cleanup below runs either way
        finally:
            with self._lock:
                self._clients.discard(client)
            self._release(client)
            conn.close()

    def _handle(self, client: Client, line: bytes) -> None:
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            return
        if not isinstance(message, dict) or "id" not in message:
            return
        request_id, method = message["id"], str(message.get("method", ""))
        params = message.get("params") if isinstance(message.get("params"), dict) else {}
        try:
            if method == "session.new":
                self._link()
                client.send({"id": request_id, "type": "success", "result": self._session})
            elif method == "session.end":
                self._release(client)
                client.send({"id": request_id, "type": "success", "result": {}})
            else:
                self._link()
                self._forward(client, request_id, method, params)
        except WebSocketError as error:
            client.send({"id": request_id, "type": "error", "error": "unknown error", "message": str(error)})

    def _release(self, client: Client) -> None:
        """What a server leaves behind must not outlive it: its page scripts and any held key or button."""
        scripts, client.preloads = client.preloads, set()
        contexts, client.contexts = client.contexts, set()
        if self._ws is None:
            return
        try:
            for script in scripts:
                self._forward(None, None, "script.removePreloadScript", {"script": script})
            for context in contexts:
                self._forward(None, None, "input.releaseActions", {"context": context})
        except WebSocketError:
            pass  # the browser is gone, and everything it held went with it


def listen(path: Path) -> socket.socket:
    if path.exists():
        probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            probe.connect(str(path))
        except OSError:
            path.unlink()  # left by a relay that did not exit cleanly
        else:
            probe.close()
            raise SystemExit(f"kvist-browser-relay: another relay already serves {path}")
        finally:
            probe.close()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    old_umask = os.umask(0o177)
    try:
        server.bind(str(path))
    finally:
        os.umask(old_umask)
    server.listen(8)
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="kvist --browser-relay",
                                     description="Hold one WebDriver BiDi session for Kvist's browser MCP servers.")
    parser.add_argument("--browser-endpoint", default="9222", help="Port or host:port of the browser's debugging endpoint.")
    args, _unknown = parser.parse_known_args(argv)
    spec = args.browser_endpoint.strip()
    host, _, port = spec.rpartition(":") if ":" in spec else ("127.0.0.1", "", spec)
    if not port.isdigit():
        raise SystemExit(f"kvist-browser-relay: cannot read a port from {spec!r}")
    path = socket_path(int(port))
    server = listen(path)
    relay = Relay(f"ws://{host or '127.0.0.1'}:{port}/session")
    print(f"kvist-browser-relay: serving {path} for {host or '127.0.0.1'}:{port}", file=sys.stderr, flush=True)
    try:
        while True:
            conn, _ = server.accept()
            threading.Thread(target=relay.serve_client, args=(conn,), name="kvist-relay-client", daemon=True).start()
    except KeyboardInterrupt:
        return 0
    finally:
        server.close()
        path.unlink(missing_ok=True)
