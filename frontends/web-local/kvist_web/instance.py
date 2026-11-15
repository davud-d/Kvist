"""Controller descriptor and authenticated lifecycle calls."""
from __future__ import annotations

import json
import http.client
import ipaddress
import os
import re
import secrets
import tempfile
from pathlib import Path
from typing import Any


def controller_home() -> Path:
    return Path.home() / ".kvist" / "web"


class InstanceDescriptor:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or controller_home() / "controller.json"

    def read(self) -> dict[str, Any] | None:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        return value if isinstance(value, dict) else None

    def write(self, host: str, port: int, admin_token: str) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, name = tempfile.mkstemp(prefix=f".{self.path.name}.", dir=self.path.parent)
        temporary = Path(name)
        try:
            # mkstemp sets private permissions before any token is written.
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(
                    {"pid": os.getpid(), "host": host, "port": port, "admin_token": admin_token},
                    stream, separators=(",", ":"),
                )
            temporary.replace(self.path)
        finally:
            temporary.unlink(missing_ok=True)

    def clear_if_owner(self) -> None:
        value = self.read()
        if value and value.get("pid") == os.getpid():
            self.path.unlink(missing_ok=True)

    def health(self) -> bool:
        return self._health() is not None

    def lan(self) -> bool:
        """Whether the running controller serves phones on the local network."""
        payload = self._health()
        return bool(payload and payload.get("lan") is True)

    def _health(self) -> dict[str, Any] | None:
        value = self.read()
        if not value:
            return None
        try:
            connection = self._connection(value, timeout=1.0)
            try:
                connection.request("GET", "/health")
                response = connection.getresponse()
                if response.status != 200:
                    return None
                raw = response.read(65537)
                if len(raw) > 65536:
                    return None
                payload = json.loads(raw)
            finally:
                connection.close()
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
            return None
        return payload if isinstance(payload, dict) and payload.get("service") == "kvist-web" else None

    def open(self, timeout: float) -> tuple[http.client.HTTPConnection, dict[str, Any]] | None:
        """A connection to the running controller and its descriptor, or None when none is written."""
        value = self.read()
        if not value:
            return None
        try:
            return self._connection(value, timeout), value
        except ValueError:
            return None

    def stop(self) -> bool:
        value = self.read()
        if not value:
            return False
        try:
            connection = self._connection(value, timeout=3.0)
            try:
                # Direct HTTP does not follow redirects or use environment proxies.
                connection.request("POST", "/api/admin/stop", body=b"{}",
                                   headers={"Authorization": f"Bearer {value.get('admin_token', '')}",
                                            "Host": self._host_header(value)})
                return 200 <= connection.getresponse().status < 300
            finally:
                connection.close()
        except (OSError, ValueError, TypeError, KeyError, http.client.HTTPException):
            return False

    @staticmethod
    def _host_header(value: dict[str, Any]) -> str:
        host = value["host"]
        if ":" in host:
            host = f"[{host}]"
        return f"{host}:{value['port']}"

    @staticmethod
    def _connection(value: dict[str, Any], timeout: float) -> http.client.HTTPConnection:
        host = value["host"]
        if host == "localhost":
            host = "127.0.0.1"
        if not isinstance(host, str):
            raise ValueError("Invalid controller address")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            if not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?\.?", host):
                raise ValueError("Invalid controller hostname") from None
            address = None
        if address is not None and address.is_unspecified:
            host = "::1" if address.version == 6 else "127.0.0.1"
        port = value["port"]
        if type(port) is not int or not 1 <= port <= 65535:
            raise ValueError("Invalid controller port")
        return http.client.HTTPConnection(host, port, timeout=timeout)

    @staticmethod
    def token() -> str:
        return secrets.token_urlsafe(40)

