# -*- coding: utf-8 -*-
"""Service connections the person configured: a base URL and the secret headers that go with it.

A skill names a connection (``ctx.connections.request("crm", "GET", "/v1/items")``);
the core adds the stored headers, sends the request to that connection's own
host only and follows redirects only on that host. The skill never sees the
secret, and a connection it was not granted (``connections.use`` scope) does
not exist for it. Connections live in ``<config_dir>/plugins/connections.json``
and are managed with ``kvist --connection-op``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlsplit

from kernel.session.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.plugins.paths import plugins_root

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,31}$")
METHODS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE"})


class ConnectionError_(ValueError):
    pass


@dataclass(frozen=True, slots=True)
class Connection:
    name: str
    base_url: str
    headers: dict[str, str]
    methods: frozenset[str]
    description: str

    def public(self) -> dict[str, Any]:
        return {"name": self.name, "base_url": self.base_url, "methods": sorted(self.methods),
                "description": self.description, "header_names": sorted(self.headers)}


def path() -> Path:
    return plugins_root() / "connections.json"


def load_connections(target: Path | None = None) -> dict[str, Connection]:
    target = target or path()
    if not target.is_file():
        return {}
    try:
        document = read_json_object(target, boundary=target.parent, empty_as_missing=True)
    except JsonStorageError as error:
        raise ConnectionError_(f"connections.json is damaged: {error}") from None
    return {name: _parse(name, raw) for name, raw in (document.get("connections") or {}).items()}


def save_connection(name: str, raw: dict[str, Any], target: Path | None = None) -> Connection:
    connection = _parse(name, raw)
    target = target or path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def update(document: dict[str, Any]) -> dict[str, Any]:
        document.setdefault("connections", {})[name] = raw
        return document

    update_json_object(target, update, boundary=target.parent, empty_as_missing=True)
    return connection


def remove_connection(name: str, target: Path | None = None) -> bool:
    target = target or path()
    removed = {"value": False}

    def update(document: dict[str, Any]) -> dict[str, Any]:
        removed["value"] = document.get("connections", {}).pop(name, None) is not None
        return document

    if target.is_file():
        update_json_object(target, update, boundary=target.parent, empty_as_missing=True)
    return removed["value"]


def _parse(name: str, raw: Any) -> Connection:
    if not NAME_RE.match(name or ""):
        raise ConnectionError_(f"connection name {name!r} must be lowercase letters, digits, - or _")
    if not isinstance(raw, dict):
        raise ConnectionError_(f"connection {name} must be an object")
    base = raw.get("base_url")
    parts = urlsplit(base) if isinstance(base, str) else None
    if parts is None or parts.scheme != "https" or not parts.hostname:
        raise ConnectionError_(f"connection {name}: base_url must be an https:// address")
    headers = raw.get("headers", {})
    if not isinstance(headers, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in headers.items()):
        raise ConnectionError_(f"connection {name}: headers must be an object of strings")
    methods = raw.get("methods", ["GET"])
    if not isinstance(methods, list) or not set(methods) <= METHODS:
        raise ConnectionError_(f"connection {name}: methods must be from {sorted(METHODS)}")
    return Connection(name, base.rstrip("/") + "/", dict(headers), frozenset(methods), str(raw.get("description", ""))[:300])


def request(connection: Connection, *, method: str, path_or_url: str, body: Any, json_body: Any,
            headers: dict[str, str], timeout: float) -> dict[str, Any]:
    """Send one request to the connection's own host, with its stored headers added by the core."""
    from kernel.plugins.http_client import HttpPolicyError, request as http_request

    if method not in connection.methods:
        raise ConnectionError_(f"connection {connection.name} allows {', '.join(sorted(connection.methods))}, not {method}")
    url = urljoin(connection.base_url, path_or_url.lstrip("/"))
    host = urlsplit(connection.base_url).hostname
    if urlsplit(url).hostname != host or urlsplit(url).scheme != "https":
        raise ConnectionError_(f"connection {connection.name} reaches only https://{host}")
    merged = {**headers, **connection.headers}
    try:
        result = http_request(method=method, url=url, headers=merged, body=body, json_body=json_body,
                              timeout=timeout, domains=[host], services=None)
    except HttpPolicyError as error:
        raise ConnectionError_(str(error)) from None
    result.pop("headers", None)
    return result
