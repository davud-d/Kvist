"""Controller descriptor and authenticated lifecycle calls."""
from __future__ import annotations

import json
import os
import secrets
import urllib.error
import urllib.request
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
        temporary = self.path.with_name(f".{self.path.name}.{os.getpid()}.tmp")
        temporary.write_text(
            json.dumps(
                {"pid": os.getpid(), "host": host, "port": port, "admin_token": admin_token},
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        if os.name != "nt":
            temporary.chmod(0o600)
        temporary.replace(self.path)

    def clear_if_owner(self) -> None:
        value = self.read()
        if value and value.get("pid") == os.getpid():
            self.path.unlink(missing_ok=True)

    def health(self) -> bool:
        value = self.read()
        if not value:
            return False
        try:
            with urllib.request.urlopen(
                f"http://{value['host']}:{value['port']}/health", timeout=1.0
            ) as response:
                payload = json.loads(response.read())
        except Exception:
            return False
        return payload.get("service") == "kvist-web"

    def stop(self) -> bool:
        value = self.read()
        if not value:
            return False
        request = urllib.request.Request(
            f"http://{value['host']}:{value['port']}/api/admin/stop",
            method="POST",
            headers={"Authorization": f"Bearer {value.get('admin_token', '')}"},
            data=b"{}",
        )
        try:
            urllib.request.urlopen(request, timeout=3).read()
        except Exception:
            return False
        return True

    @staticmethod
    def token() -> str:
        return secrets.token_urlsafe(40)

