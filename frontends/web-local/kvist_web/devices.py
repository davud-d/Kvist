# -*- coding: utf-8 -*-
"""The phones this computer trusts. A phone is trusted once its owner allowed it on this computer; from then
on it holds a token of its own, so one lost phone is forgotten without touching the others."""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import tempfile
import threading
import time
from pathlib import Path

from .instance import controller_home


def devices_path() -> Path:
    return controller_home() / "devices.json"


def _digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


class DeviceStore:
    """Only a hash of each token is kept: the file cannot be used to act as a phone."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or devices_path()
        self._lock = threading.Lock()

    def _read(self) -> dict[str, dict[str, object]]:
        try:
            value = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        devices = value.get("devices") if isinstance(value, dict) else None
        return devices if isinstance(devices, dict) else {}

    def _write(self, devices: dict[str, dict[str, object]]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # mkstemp creates the file private before anything is written to it.
        descriptor, name = tempfile.mkstemp(prefix=f".{self._path.name}.", dir=self._path.parent)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"devices": devices}, stream, separators=(",", ":"))
            temporary.replace(self._path)
        finally:
            temporary.unlink(missing_ok=True)

    def approve(self, device_id: str, name: str) -> str:
        """Trust this phone and return its token. A phone that is trusted already gets a new one."""
        token = secrets.token_urlsafe(32)
        with self._lock:
            devices = self._read()
            devices[device_id] = {"name": name, "hash": _digest(token), "added": int(time.time())}
            self._write(devices)
        return token

    def verify(self, token: str) -> str | None:
        """The device id the token belongs to, or None."""
        wanted = _digest(token)
        with self._lock:
            for device_id, entry in self._read().items():
                stored = entry.get("hash") if isinstance(entry, dict) else None
                if isinstance(stored, str) and hmac.compare_digest(stored, wanted):
                    return device_id
        return None

    def forget_all(self) -> int:
        with self._lock:
            count = len(self._read())
            self._write({})
        return count

    def names(self) -> list[str]:
        with self._lock:
            return [str(entry.get("name", "")) for entry in self._read().values() if isinstance(entry, dict)]
