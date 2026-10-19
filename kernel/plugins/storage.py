# -*- coding: utf-8 -*-
"""A skill's own key/value data in one workspace, with scopes and versioned writes.

Scopes: ``call`` (dropped when the call ends), ``task`` (the run), ``session``
and ``workspace``. Writes are atomic and serialized by a file lock; a write
with ``expected_version`` fails when someone changed the key meanwhile, which
is how two calls avoid losing each other's update. A value may carry a TTL:
once it passes, the value reads as absent and is dropped on the next write.
Nothing here is ever put in a model prompt, and nothing that memory would
refuse (credentials, personal data) is stored here either.

The file records which version of the skill last wrote it. An older version
(after a rollback) refuses to read it instead of misreading a newer layout.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.memory.errors import MemoryPrivacyError
from kernel.memory.privacy import check_text
from kernel.plugins.paths import workspace_skill_dir

SCOPES = ("call", "task", "session", "workspace")
MAX_TTL_SECONDS = 30 * 24 * 3600
MAX_VALUE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_KEYS = 1000
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")


class StorageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillStorage:
    def __init__(self, workspace: Path, skill_id: str, skill_version: str) -> None:
        self.workspace = Path(workspace)
        self.path = workspace_skill_dir(self.workspace, skill_id) / "storage.json"
        self.version = skill_version

    def get(self, scope: str, owner: str, key: str) -> dict[str, Any]:
        _check(scope, key)
        entry = self._read().get("scopes", {}).get(scope, {}).get(owner, {}).get(key)
        if entry is None:
            return {"value": None, "version": 0}
        # An expired value is gone, but its version stays, so a compare-and-set holder still sees the change.
        if _expired(entry, time.time()):
            return {"value": None, "version": entry["version"]}
        return {"value": entry["value"], "version": entry["version"]}

    def keys(self, scope: str, owner: str) -> list[str]:
        _check(scope, "x")
        now = time.time()
        bucket = self._read().get("scopes", {}).get(scope, {}).get(owner, {})
        return sorted(key for key, entry in bucket.items() if not _expired(entry, now))

    def set(self, scope: str, owner: str, key: str, value: Any, expected_version: int | None,
            ttl_seconds: int | None = None) -> dict[str, Any]:
        _check(scope, key)
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
            raise StorageError("too_large", f"a value is at most {MAX_VALUE_BYTES} bytes")
        _check_private(encoded)
        if ttl_seconds is not None and not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise StorageError("bad_request", f"ttl_seconds is 1..{MAX_TTL_SECONDS}")
        result: dict[str, Any] = {}

        def change(document: dict[str, Any]) -> dict[str, Any]:
            self._check_writer(document)
            _drop_expired(document)
            bucket = document.setdefault("scopes", {}).setdefault(scope, {}).setdefault(owner, {})
            current = bucket.get(key)
            current_version = current["version"] if current else 0
            if expected_version is not None and expected_version != current_version:
                raise StorageError("conflict", f"{key} is at version {current_version}, not {expected_version}")
            if current is None and sum(len(b) for s in document["scopes"].values() for b in s.values()) >= MAX_KEYS:
                raise StorageError("too_many_keys", f"at most {MAX_KEYS} keys per skill and workspace")
            bucket[key] = {"value": value, "version": current_version + 1}
            if ttl_seconds is not None:
                bucket[key]["expires_at"] = time.time() + ttl_seconds
            document["written_by"] = self.version
            if len(json.dumps(document, ensure_ascii=False).encode("utf-8")) > MAX_TOTAL_BYTES:
                raise StorageError("too_large", f"a skill keeps at most {MAX_TOTAL_BYTES} bytes per workspace")
            result["version"] = current_version + 1
            return document

        self._update(change)
        return result

    def delete(self, scope: str, owner: str, key: str) -> bool:
        _check(scope, key)
        removed = {"value": False}

        def change(document: dict[str, Any]) -> dict[str, Any]:
            self._check_writer(document)
            bucket = document.get("scopes", {}).get(scope, {}).get(owner, {})
            removed["value"] = bucket.pop(key, None) is not None
            return document

        self._update(change)
        return removed["value"]

    def drop_owner(self, scope: str, owner: str) -> None:
        """Forget a finished call's or task's keys."""
        if not self.path.exists():
            return

        def change(document: dict[str, Any]) -> dict[str, Any]:
            document.get("scopes", {}).get(scope, {}).pop(owner, None)
            return document

        self._update(change)

    def _read(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            document = read_json_object(self.path, boundary=self.workspace, empty_as_missing=True)
        except JsonStorageError as error:
            raise StorageError("damaged", f"the skill's storage is damaged: {error}") from None
        self._check_writer(document)
        return document

    def _check_writer(self, document: dict[str, Any]) -> None:
        writer = document.get("written_by")
        if isinstance(writer, str) and _version_tuple(writer) > _version_tuple(self.version):
            raise StorageError(
                "newer_state",
                f"this data was written by version {writer} of the skill; version {self.version} will not "
                "read it. Activate the newer version again, or clear the skill's data for this workspace.",
            )

    def _update(self, change) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            update_json_object(self.path, change, boundary=self.workspace, empty_as_missing=True)
        except JsonStorageError as error:
            raise StorageError("damaged", f"the skill's storage cannot be written: {error}") from None


def _expired(entry: dict[str, Any], now: float) -> bool:
    expires_at = entry.get("expires_at")
    return isinstance(expires_at, (int, float)) and expires_at <= now


def _drop_expired(document: dict[str, Any]) -> None:
    """Physical cleanup of expired values, done on the next write; their versions are kept."""
    now = time.time()
    for owners in document.get("scopes", {}).values():
        for bucket in owners.values():
            for entry in bucket.values():
                if _expired(entry, now):
                    entry["value"] = None


def _check_private(encoded: str) -> None:
    """Storage is not a way around memory's rule: no credentials or personal data, even for a skill's own use."""
    try:
        check_text(encoded, "value", flag_random_strings=False)
    except MemoryPrivacyError as error:
        raise StorageError("privacy", str(error)) from None


def _check(scope: str, key: str) -> None:
    if scope not in SCOPES:
        raise StorageError("bad_request", f"scope must be one of {', '.join(SCOPES)}")
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise StorageError("bad_request", "a key is 1-200 characters of letters, digits and _.:/-")


def _version_tuple(version: str) -> tuple[int, ...]:
    core = re.split(r"[-+]", version, maxsplit=1)[0]
    try:
        return tuple(int(part) for part in core.split("."))
    except ValueError:
        return (0,)
