# -*- coding: utf-8 -*-
"""A skill's own key/value data in one workspace, with scopes and versioned writes.

Scopes: ``call`` (dropped when the call ends), ``task`` (the run), ``session``
and ``workspace``. Every write is one SQLite transaction; a write with
``expected_version`` fails when someone changed the key meanwhile, which is how
two calls avoid losing each other's update. A value may carry a TTL: once it
passes, the value reads as absent (its version stays) and is cleared on the
next write. Nothing here is ever put in a model prompt, and nothing that
memory would refuse (credentials, personal data) is stored here either.

The database records which version of the skill last wrote it. An older
version (after a rollback) refuses to read it instead of misreading a newer
layout.

It is SQLite, not ``durable_json``, because a write there cost ~8 fsyncs and
over a second at p95 on a spinning disk; the connection handling follows
``kernel.memory.store`` (WAL, ``synchronous = NORMAL``, one connection per thread
reused while the file keeps its inode).
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from kernel.session.durable_json import (
    JsonStorageError,
    exclusive_storage_lock,
    read_json_object,
    validate_storage_path,
)
from kernel.memory.errors import MemoryPrivacyError
from kernel.memory.privacy import check_text
from kernel.plugins.paths import workspace_skill_dir

SCOPES = ("call", "task", "session", "workspace")
MAX_TTL_SECONDS = 30 * 24 * 3600
MAX_VALUE_BYTES = 256 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024
MAX_KEYS = 1000
SCHEMA_VERSION = 1
_KEY_RE = re.compile(r"^[A-Za-z0-9_.:/-]{1,200}$")

_SCHEMA = """
CREATE TABLE entries (
    scope TEXT NOT NULL,
    owner TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    version INTEGER NOT NULL,
    expires_at REAL,
    PRIMARY KEY (scope, owner, key)
) WITHOUT ROWID;
CREATE TABLE meta (name TEXT PRIMARY KEY, value TEXT NOT NULL) WITHOUT ROWID;
"""


class StorageError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class SkillStorage:
    def __init__(self, workspace: Path, skill_id: str, skill_version: str) -> None:
        self.workspace = Path(workspace)
        skill_dir = workspace_skill_dir(self.workspace, skill_id)
        self.path = skill_dir / "storage.db"
        self.legacy_path = skill_dir / "storage.json"
        self.version = skill_version

    def get(self, scope: str, owner: str, key: str) -> dict[str, Any]:
        _check(scope, key)
        if not self._exists():
            return {"value": None, "version": 0}
        with self._connection() as db:
            self._check_writer(db)
            row = db.execute("SELECT value, version, expires_at FROM entries WHERE scope = ? AND owner = ? AND key = ?",
                             (scope, owner, key)).fetchone()
        if row is None:
            return {"value": None, "version": 0}
        # An expired value is gone, but its version stays, so a compare-and-set holder still sees the change.
        if _expired(row["expires_at"], time.time()):
            return {"value": None, "version": row["version"]}
        return {"value": json.loads(row["value"]), "version": row["version"]}

    def keys(self, scope: str, owner: str) -> list[str]:
        _check(scope, "x")
        if not self._exists():
            return []
        with self._connection() as db:
            self._check_writer(db)
            rows = db.execute("SELECT key FROM entries WHERE scope = ? AND owner = ? "
                              "AND (expires_at IS NULL OR expires_at > ?) ORDER BY key",
                              (scope, owner, time.time())).fetchall()
        return [row["key"] for row in rows]

    def set(self, scope: str, owner: str, key: str, value: Any, expected_version: int | None,
            ttl_seconds: int | None = None) -> dict[str, Any]:
        _check(scope, key)
        encoded = json.dumps(value, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > MAX_VALUE_BYTES:
            raise StorageError("too_large", f"a value is at most {MAX_VALUE_BYTES} bytes")
        _check_private(encoded)
        if ttl_seconds is not None and not 1 <= ttl_seconds <= MAX_TTL_SECONDS:
            raise StorageError("bad_request", f"ttl_seconds is 1..{MAX_TTL_SECONDS}")
        now = time.time()
        with self._write() as db:
            self._check_writer(db)
            # Physical cleanup of expired values; their versions are kept.
            db.execute("UPDATE entries SET value = 'null' WHERE expires_at <= ? AND value != 'null'", (now,))
            row = db.execute("SELECT version FROM entries WHERE scope = ? AND owner = ? AND key = ?",
                             (scope, owner, key)).fetchone()
            current_version = row["version"] if row else 0
            if expected_version is not None and expected_version != current_version:
                raise StorageError("conflict", f"{key} is at version {current_version}, not {expected_version}")
            if row is None and db.execute("SELECT COUNT(*) FROM entries").fetchone()[0] >= MAX_KEYS:
                raise StorageError("too_many_keys", f"at most {MAX_KEYS} keys per skill and workspace")
            expires_at = now + ttl_seconds if ttl_seconds is not None else None
            db.execute("INSERT OR REPLACE INTO entries (scope, owner, key, value, version, expires_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)", (scope, owner, key, encoded, current_version + 1, expires_at))
            total = db.execute("SELECT COALESCE(SUM(LENGTH(CAST(value AS BLOB))), 0) FROM entries").fetchone()[0]
            if total > MAX_TOTAL_BYTES:
                raise StorageError("too_large", f"a skill keeps at most {MAX_TOTAL_BYTES} bytes per workspace")
            db.execute("INSERT OR REPLACE INTO meta (name, value) VALUES ('written_by', ?)", (self.version,))
        return {"version": current_version + 1}

    def delete(self, scope: str, owner: str, key: str) -> bool:
        _check(scope, key)
        if not self._exists():
            return False
        with self._write() as db:
            self._check_writer(db)
            removed = db.execute("DELETE FROM entries WHERE scope = ? AND owner = ? AND key = ?",
                                 (scope, owner, key)).rowcount
        return removed > 0

    def drop_owner(self, scope: str, owner: str) -> None:
        """Forget a finished call's or task's keys."""
        if not self._exists():
            return
        with self._write() as db:
            db.execute("DELETE FROM entries WHERE scope = ? AND owner = ?", (scope, owner))

    def _exists(self) -> bool:
        """A read never creates the database, but a legacy file still has to be imported."""
        return self.path.exists() or self.legacy_path.exists()

    def _check_writer(self, db: sqlite3.Connection) -> None:
        row = db.execute("SELECT value FROM meta WHERE name = 'written_by'").fetchone()
        if row is not None and _version_tuple(row["value"]) > _version_tuple(self.version):
            raise StorageError(
                "newer_state",
                f"this data was written by version {row['value']} of the skill; version {self.version} will not "
                "read it. Activate the newer version again, or clear the skill's data for this workspace.",
            )

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                raise StorageError("busy", f"the skill's storage is busy: {error}") from None
            try:
                yield db
            except BaseException:
                db.execute("ROLLBACK")
                raise
            db.execute("COMMIT")

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        db = self._open()
        try:
            yield db
        except sqlite3.DatabaseError as error:
            # A connection that failed is not trusted again.
            _evict(self.path)
            raise _damaged(error) from None

    def _open(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            validate_storage_path(self.path, self.workspace)
        except (JsonStorageError, OSError) as error:
            raise StorageError("damaged", f"the skill's storage path is unsafe: {error}") from None
        cached = _reusable(self.path)
        if cached is not None:
            return cached
        db = self._connect()
        _connections()[str(self.path)] = (db, self.path.stat().st_ino)
        return db

    def _connect(self) -> sqlite3.Connection:
        created = not self.path.exists()
        try:
            db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        except sqlite3.DatabaseError as error:
            raise _damaged(error) from None
        try:
            self._prepare(db, created)
        except (sqlite3.DatabaseError, StorageError, JsonStorageError) as error:
            db.close()
            if isinstance(error, StorageError):
                raise
            raise _damaged(error) from None
        return db

    def _prepare(self, db: sqlite3.Connection, created: bool) -> None:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous = NORMAL")
        if created:
            os.chmod(self.path, 0o600)
        if self._ready(db):
            return
        # Switching a fresh file to WAL fails at once, without waiting, while another
        # connection does the same; the first setup is serialized across processes.
        with exclusive_storage_lock(self.path, boundary=self.workspace):
            if self._ready(db):
                return
            user_version = db.execute("PRAGMA user_version").fetchone()[0]
            if user_version > SCHEMA_VERSION:
                raise StorageError("newer_state", "the skill's storage has a newer layout; it was left as is")
            db.execute("PRAGMA journal_mode = WAL")
            if user_version == 0:
                self._create(db)

    @staticmethod
    def _ready(db: sqlite3.Connection) -> bool:
        return (db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                and db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION)

    def _create(self, db: sqlite3.Connection) -> None:
        """Create the tables and carry over what the JSON file held, then delete it with its backup."""
        document = self._read_legacy()
        db.execute("BEGIN IMMEDIATE")
        try:
            for statement in _SCHEMA.split(";"):
                if statement.strip():
                    db.execute(statement)
            _import_legacy(db, document)
            db.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        except BaseException:
            db.execute("ROLLBACK")
            raise
        db.execute("COMMIT")
        for leftover in (self.legacy_path, self.legacy_path.with_suffix(".json.bak")):
            leftover.unlink(missing_ok=True)

    def _read_legacy(self) -> dict[str, Any]:
        if not self.legacy_path.exists():
            return {}
        try:
            return read_json_object(self.legacy_path, boundary=self.workspace, empty_as_missing=True)
        except JsonStorageError as error:
            raise StorageError("damaged", f"the skill's old storage file is damaged: {error}") from None


def _import_legacy(db: sqlite3.Connection, document: dict[str, Any]) -> None:
    for scope, owners in document.get("scopes", {}).items():
        for owner, bucket in owners.items():
            for key, entry in bucket.items():
                db.execute("INSERT INTO entries (scope, owner, key, value, version, expires_at) "
                           "VALUES (?, ?, ?, ?, ?, ?)",
                           (scope, owner, key, json.dumps(entry["value"], ensure_ascii=False), entry["version"],
                            entry.get("expires_at")))
    writer = document.get("written_by")
    if isinstance(writer, str):
        db.execute("INSERT INTO meta (name, value) VALUES ('written_by', ?)", (writer,))


_THREAD = threading.local()


def _connections() -> dict[str, tuple[sqlite3.Connection, int]]:
    if not hasattr(_THREAD, "connections"):
        _THREAD.connections = {}
    return _THREAD.connections


def _reusable(path: Path) -> sqlite3.Connection | None:
    entry = _connections().get(str(path))
    if entry is None:
        return None
    db, inode = entry
    try:
        current = path.stat().st_ino == inode and db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    except (OSError, sqlite3.DatabaseError):
        current = False
    if current:
        return db
    _evict(path)
    return None


def _evict(path: Path) -> None:
    entry = _connections().pop(str(path), None)
    if entry is not None:
        entry[0].close()


def close_connections() -> None:
    """Close this thread's connections (a scratch folder can then be removed on Windows)."""
    for key in list(_connections()):
        _evict(Path(key))


def _damaged(error: Exception) -> StorageError:
    return StorageError("damaged", f"the skill's storage cannot be used ({error}); it was left as is")


def _expired(expires_at: float | None, now: float) -> bool:
    return expires_at is not None and expires_at <= now


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
