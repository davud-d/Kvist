# -*- coding: utf-8 -*-
"""HTTP requests a skill makes through the core (``ctx.http.request``).

The destination rule is the network broker's (``net_broker.decide``): the
skill's listed domains on public addresses, or a server of this task. Every
redirect is followed by hand and decided again, so a redirect cannot carry a
request to a destination the first check would have refused. The connection
goes to the address that was checked — a second DNS answer cannot swap it —
with the original host name for TLS verification and the Host header.
"""
from __future__ import annotations

import http.client
import json
import socket
import ssl
from typing import Any
from urllib.parse import urljoin, urlsplit

from kernel.plugins.net_broker import Denied, NetPolicy, TaskServices, decide

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 1024 * 1024
MAX_REDIRECTS = 5
_METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})
_BLOCKED_HEADERS = frozenset({"host", "connection", "proxy-authorization", "transfer-encoding", "content-length"})


class HttpPolicyError(Exception):
    pass


def request(*, method: str, url: str, headers: dict[str, Any], body: Any, json_body: Any, timeout: float,
            domains: list[str], services: TaskServices | None) -> dict[str, Any]:
    if method not in _METHODS:
        raise HttpPolicyError(f"method must be one of {', '.join(sorted(_METHODS))}")
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise HttpPolicyError("headers must be an object of strings")
    payload = _payload(body, json_body)
    clean_headers = {k: v for k, v in headers.items() if k.lower() not in _BLOCKED_HEADERS}
    if json_body is not None:
        clean_headers.setdefault("Content-Type", "application/json")
    policy = NetPolicy(allow_services=services is not None, domains=domains)
    hops: list[str] = []
    for _hop in range(MAX_REDIRECTS + 1):
        status, response_headers, raw, truncated = _one(method, url, clean_headers, payload, timeout, policy,
                                                        services or TaskServices())
        hops.append(url)
        location = response_headers.get("location")
        if status in (301, 302, 303, 307, 308) and location:
            url = urljoin(url, location)
            if status == 303 or (status in (301, 302) and method == "POST"):
                method, payload = "GET", None
            continue
        content_type = response_headers.get("content-type", "")
        text = raw.decode(_charset(content_type), errors="replace")
        return {"status": status, "headers": response_headers, "text": text, "truncated": truncated,
                "url": url, "redirects": hops[:-1], "untrusted": True}
    raise HttpPolicyError(f"more than {MAX_REDIRECTS} redirects")


def _payload(body: Any, json_body: Any) -> bytes | None:
    if body is not None and json_body is not None:
        raise HttpPolicyError("give body or json, not both")
    if json_body is not None:
        data = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
    elif body is not None:
        if not isinstance(body, str):
            raise HttpPolicyError("body must be a string")
        data = body.encode("utf-8")
    else:
        return None
    if len(data) > MAX_REQUEST_BYTES:
        raise HttpPolicyError(f"the request body is larger than {MAX_REQUEST_BYTES} bytes")
    return data


def _one(method: str, url: str, headers: dict[str, str], payload: bytes | None, timeout: float, policy: NetPolicy,
         services: TaskServices) -> tuple[int, dict[str, str], bytes, bool]:
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HttpPolicyError("only http and https URLs are allowed")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        route = decide(parts.hostname, port, policy, services)
    except Denied as error:
        raise HttpPolicyError(str(error)) from None
    sock = _connect(route, timeout)
    if parts.scheme == "https":
        sock = ssl.create_default_context().wrap_socket(sock, server_hostname=parts.hostname)
    connection = http.client.HTTPConnection(parts.hostname, port, timeout=timeout)
    connection.sock = sock
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    try:
        connection.request(method, path, body=payload, headers={**headers, "Host": parts.netloc})
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE_BYTES + 1)
        return (response.status, {k.lower(): v for k, v in response.getheaders()},
                raw[:MAX_RESPONSE_BYTES], len(raw) > MAX_RESPONSE_BYTES)
    except (OSError, http.client.HTTPException) as error:
        raise HttpPolicyError(f"request failed: {error}") from None
    finally:
        connection.close()


def _connect(route, timeout: float) -> socket.socket:
    try:
        if route.kind == "service":
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(timeout)
            sock.connect(route.target)
            return sock
        return socket.create_connection((route.target, route.port), timeout=timeout)
    except OSError as error:
        raise HttpPolicyError(f"cannot connect: {error}") from None


def _charset(content_type: str) -> str:
    for part in content_type.split(";"):
        key, _, value = part.strip().partition("=")
        if key.lower() == "charset" and value:
            return value.strip('"')
    return "utf-8"
