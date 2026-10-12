# -*- coding: utf-8 -*-
"""The core's HTTP proxy: the only network path out of a skill's sandbox.

A runner's sandbox has no network interface except its own loopback, where a
bridge forwards to this proxy's unix socket. Every request is decided here, in
the core, where the skill cannot change the rule:

* ``localhost:<port>`` reaches a server this task started and registered under
  that port (a dev server) — through that server's own socket, so the host's
  loopback is never opened to the sandbox;
* a domain reaches the internet only when the skill's grant lists it, and only
  if every address it resolves to is public — a name that points at a private,
  loopback or link-local address is refused, which also stops redirects and DNS
  rebinding towards the local network;
* anything else is answered with 403 and a reason the skill can show.

Each connection carries one request decision: an HTTP request through the
proxy is forwarded with ``Connection: close``, so a later request on the same
connection cannot reach a destination that was never checked.
"""
from __future__ import annotations

import ipaddress
import os
import socket
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

_MAX_HEAD = 32 * 1024
_CHUNK = 65536
_CONNECT_TIMEOUT = 15.0
_LOCAL_NAMES = frozenset({"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"})


@dataclass(frozen=True, slots=True)
class TaskService:
    """A server of the current task the broker may route ``localhost:<port>`` to."""

    port: int
    label: str
    unix_path: str = ""  # the in-sandbox bridge's socket (isolated server)
    tcp_port: int = 0  # a server on the host loopback (approved host network)
    resource_id: str = ""


class TaskServices:
    """Servers registered by the task, by the port the skill and the browser use."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._services: dict[int, TaskService] = {}

    def add(self, service: TaskService) -> None:
        with self._lock:
            self._services[service.port] = service

    def remove(self, port: int) -> None:
        with self._lock:
            self._services.pop(port, None)

    def get(self, port: int) -> TaskService | None:
        with self._lock:
            return self._services.get(port)

    def list(self) -> list[TaskService]:
        with self._lock:
            return sorted(self._services.values(), key=lambda item: item.port)


@dataclass(frozen=True, slots=True)
class Route:
    kind: str  # service | internet
    target: str  # unix path, or the checked IP address
    port: int
    host: str


@dataclass(slots=True)
class NetPolicy:
    allow_services: bool
    domains: list[str] = field(default_factory=list)  # exact, "*.example.com", or "*" for any public host


class Denied(Exception):
    """A destination the policy does not allow; the message is shown to the skill."""


def decide(host: str, port: int, policy: NetPolicy, services: TaskServices,
           resolve: Callable[[str, int], list[str]] | None = None) -> Route:
    """Where a connection to ``host:port`` goes, or Denied with the reason."""
    name = host.strip().strip("[]").rstrip(".").lower()
    if not name or not 0 < port < 65536:
        raise Denied("invalid destination")
    if name in _LOCAL_NAMES or name.endswith(".localhost"):
        return _service_route(name, port, policy, services)
    literal = _ip(name)
    if literal is not None:
        if not literal.is_global:
            raise Denied(f"{name} is a private or local address; only this task's servers are reachable locally")
        if "*" not in policy.domains and name not in policy.domains:
            raise Denied(f"{name} is not among the addresses this skill may reach")
        return Route("internet", name, port, name)
    if not _domain_allowed(name, policy.domains):
        raise Denied(f"{name} is not among the addresses this skill may reach")
    addresses = (resolve or _resolve)(name, port)
    if not addresses:
        raise Denied(f"{name} does not resolve")
    for address in addresses:
        ip = _ip(address)
        if ip is None or not ip.is_global:
            raise Denied(f"{name} resolves to a non-public address ({address}); refused")
    return Route("internet", addresses[0], port, name)


def _service_route(name: str, port: int, policy: NetPolicy, services: TaskServices) -> Route:
    if not policy.allow_services:
        raise Denied("this skill may not reach the task's local servers")
    service = services.get(port)
    if service is None:
        raise Denied(f"no server of this task listens on port {port}; start it with expose_port={port}")
    if service.unix_path:
        return Route("service", service.unix_path, port, name)
    return Route("service_tcp", "127.0.0.1", service.tcp_port or port, name)


def _domain_allowed(name: str, patterns: list[str]) -> bool:
    for pattern in patterns:
        pattern = pattern.strip().lower().rstrip(".")
        if pattern == "*" or pattern == name:
            return True
        if pattern.startswith("*.") and (name.endswith(pattern[1:]) or name == pattern[2:]):
            return True
    return False


def _ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _resolve(name: str, port: int) -> list[str]:
    try:
        infos = socket.getaddrinfo(name, port, type=socket.SOCK_STREAM)
    except socket.gaierror:
        return []
    return list(dict.fromkeys(info[4][0] for info in infos))


@dataclass(slots=True)
class Decision:
    at: float
    host: str
    port: int
    allowed: bool
    reason: str = ""


class NetBroker:
    """One proxy listener (per runner) on a unix socket in a core-owned directory."""

    def __init__(self, socket_path: Path, policy: NetPolicy, services: TaskServices,
                 resolve: Callable[[str, int], list[str]] | None = None) -> None:
        self.socket_path = Path(socket_path)
        self.policy = policy
        self.services = services
        self._resolve = resolve
        self._server: socket.socket | None = None
        self.decisions: deque[Decision] = deque(maxlen=200)
        self._closed = threading.Event()

    def start(self) -> None:
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(self.socket_path))
        os.chmod(self.socket_path, 0o600)
        server.listen(64)
        self._server = server
        threading.Thread(target=self._accept_loop, name="kvist-net-broker", daemon=True).start()

    def close(self) -> None:
        self._closed.set()
        if self._server is not None:
            self._server.close()
        try:
            self.socket_path.unlink()
        except FileNotFoundError:
            return

    def recent(self, limit: int = 20) -> list[dict[str, object]]:
        return [
            {"host": item.host, "port": item.port, "allowed": item.allowed, "reason": item.reason}
            for item in list(self.decisions)[-limit:]
        ]

    def _accept_loop(self) -> None:
        assert self._server is not None
        while not self._closed.is_set():
            try:
                client, _ = self._server.accept()
            except OSError:
                return
            threading.Thread(target=self._handle, args=(client,), daemon=True).start()

    def _handle(self, client: socket.socket) -> None:
        try:
            head, rest = _read_head(client)
            method, target, version, headers = _parse_head(head)
            if method == "CONNECT":
                host, port = _split_host_port(target, 443)
                upstream = self._open(host, port)
                client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                if rest:
                    upstream.sendall(rest)
            else:
                parts = urlsplit(target)
                if parts.scheme != "http" or not parts.hostname:
                    raise Denied("only absolute http:// requests and CONNECT are accepted")
                port = parts.port or 80
                upstream = self._open(parts.hostname, port)
                path = parts.path or "/"
                if parts.query:
                    path += "?" + parts.query
                upstream.sendall(_rewrite_head(method, path, version, headers, parts.netloc) + rest)
            _splice(client, upstream)
        except Denied as error:
            _reply(client, 403, str(error))
        except (OSError, ValueError, EOFError) as error:
            _reply(client, 502, f"proxy error: {error}")

    def _open(self, host: str, port: int) -> socket.socket:
        try:
            route = decide(host, port, self.policy, self.services, self._resolve)
        except Denied as error:
            self.decisions.append(Decision(time.time(), host, port, False, str(error)))
            raise
        self.decisions.append(Decision(time.time(), host, port, True))
        if route.kind == "service":
            upstream = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            upstream.settimeout(_CONNECT_TIMEOUT)
            upstream.connect(route.target)
        else:
            upstream = socket.create_connection((route.target, route.port), timeout=_CONNECT_TIMEOUT)
        upstream.settimeout(None)
        return upstream


def _read_head(client: socket.socket) -> tuple[bytes, bytes]:
    client.settimeout(30)
    data = b""
    while b"\r\n\r\n" not in data:
        chunk = client.recv(4096)
        if not chunk:
            raise EOFError("client closed before sending a request")
        data += chunk
        if len(data) > _MAX_HEAD:
            raise ValueError("request head too large")
    client.settimeout(None)
    head, _, rest = data.partition(b"\r\n\r\n")
    return head, rest


def _parse_head(head: bytes) -> tuple[str, str, str, list[tuple[str, str]]]:
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise ValueError("malformed request line")
    headers = []
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if separator:
            headers.append((name.strip(), value.strip()))
    return parts[0].upper(), parts[1], parts[2], headers


def _rewrite_head(method: str, path: str, version: str, headers: list[tuple[str, str]], netloc: str) -> bytes:
    dropped = {"proxy-connection", "proxy-authorization", "connection", "keep-alive", "host"}
    lines = [f"{method} {path} {version}", f"Host: {netloc}"]
    lines += [f"{name}: {value}" for name, value in headers if name.lower() not in dropped]
    lines.append("Connection: close")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")


def _split_host_port(target: str, default: int) -> tuple[str, int]:
    if target.startswith("["):
        host, _, rest = target[1:].partition("]")
        return host, int(rest.lstrip(":") or default)
    host, separator, port = target.rpartition(":")
    if not separator:
        return target, default
    return host, int(port)


def _splice(client: socket.socket, upstream: socket.socket) -> None:
    def pump(source: socket.socket, target: socket.socket) -> None:
        try:
            while True:
                data = source.recv(_CHUNK)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            # One side reset the connection; closing both below ends the pair.
            source.close()
        try:
            target.shutdown(socket.SHUT_WR)
        except OSError:
            target.close()

    other = threading.Thread(target=pump, args=(upstream, client), daemon=True)
    other.start()
    pump(client, upstream)
    other.join(timeout=60)
    client.close()
    upstream.close()


def _reply(client: socket.socket, status: int, message: str) -> None:
    reason = {403: "Forbidden", 502: "Bad Gateway"}.get(status, "Error")
    body = f"Kvist network policy: {message}\n".encode("utf-8")
    head = (f"HTTP/1.1 {status} {reason}\r\nContent-Type: text/plain; charset=utf-8\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\nX-Kvist-Denied: 1\r\n\r\n").encode("latin-1")
    try:
        client.sendall(head + body)
    except OSError:
        # The client already went away; there is no one left to tell.
        client.close()
        return
    client.close()
