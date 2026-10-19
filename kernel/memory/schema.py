# -*- coding: utf-8 -*-
"""The memory database's schema and its numbered migrations.

A database written by a newer Kvist is refused, never rewritten: recreating
it would silently drop what the person kept. Migrations run inside one
``BEGIN IMMEDIATE`` transaction, so two cores opening the same workspace
cannot both migrate it.
"""
from __future__ import annotations

import sqlite3

from kernel.memory.errors import MemoryStorageError

SCHEMA_VERSION = 2
KINDS = ("project_fact", "decision", "preference")
STATUSES = ("active", "stale", "conflicted", "superseded")
TRUST_LEVELS = ("person", "observed", "inferred")
DIRECTED_RELATIONS = ("caused_by", "resolved_by", "justified_by", "implemented_in", "verified_by", "depends_on",
                      "supersedes")
SYMMETRIC_RELATIONS = ("related_to", "contradicts")
RELATIONS = DIRECTED_RELATIONS + SYMMETRIC_RELATIONS
# What the model's memory tool, ctx.memory and the person can do.
OPERATIONS = ("search", "get", "remember", "update", "forget", "explain", "link")


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join(f"'{value}'" for value in values)


# ``seq`` is the explicit integer key the full-text index follows: an implicit
# rowid may be renumbered by VACUUM, which would point the index at other rows.
_V1 = f"""
CREATE TABLE records (
    seq INTEGER PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ({_quoted(KINDS)})),
    content TEXT NOT NULL,
    topic TEXT NOT NULL DEFAULT '',
    files TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL CHECK (status IN ({_quoted(STATUSES)})),
    status_reason TEXT NOT NULL DEFAULT '',
    trust TEXT NOT NULL CHECK (trust IN ({_quoted(TRUST_LEVELS)})),
    author TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1,
    content_hash TEXT NOT NULL,
    superseded_by TEXT REFERENCES records(id) ON DELETE SET NULL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    verified_at REAL
);
CREATE INDEX records_status ON records(status);
CREATE INDEX records_hash ON records(content_hash);

CREATE TABLE sources (
    id INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    origin TEXT NOT NULL,
    session TEXT NOT NULL DEFAULT '',
    event_seq INTEGER,
    run_id TEXT NOT NULL DEFAULT '',
    added_at REAL NOT NULL
);
CREATE INDEX sources_record ON sources(record_id);

CREATE TABLE relations (
    source_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    target_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    relation TEXT NOT NULL CHECK (relation IN ({_quoted(RELATIONS)})),
    author TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    PRIMARY KEY (source_id, target_id, relation)
);
CREATE INDEX relations_target ON relations(target_id);

CREATE TABLE history (
    id INTEGER PRIMARY KEY,
    record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE,
    at REAL NOT NULL,
    actor TEXT NOT NULL,
    action TEXT NOT NULL,
    detail TEXT NOT NULL DEFAULT ''
);
CREATE INDEX history_record ON history(record_id);

CREATE TABLE forgotten (
    content_hash TEXT PRIMARY KEY,
    forgotten_at REAL NOT NULL,
    actor TEXT NOT NULL
);

CREATE VIRTUAL TABLE records_fts USING fts5(
    content, topic, files, content='records', content_rowid='seq', tokenize='trigram'
);
CREATE TRIGGER records_fts_insert AFTER INSERT ON records BEGIN
    INSERT INTO records_fts(rowid, content, topic, files) VALUES (new.seq, new.content, new.topic, new.files);
END;
CREATE TRIGGER records_fts_delete AFTER DELETE ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, content, topic, files)
    VALUES ('delete', old.seq, old.content, old.topic, old.files);
END;
CREATE TRIGGER records_fts_update AFTER UPDATE OF content, topic, files ON records BEGIN
    INSERT INTO records_fts(records_fts, rowid, content, topic, files)
    VALUES ('delete', old.seq, old.content, old.topic, old.files);
    INSERT INTO records_fts(rowid, content, topic, files) VALUES (new.seq, new.content, new.topic, new.files);
END;

CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

# What the extractor has read: the last journal event of each session it took in,
# and every batch by its idempotency key, so a batch read twice is stored once.
_V2 = """
CREATE TABLE processing (
    session TEXT PRIMARY KEY,
    last_seq INTEGER NOT NULL,
    updated_at REAL NOT NULL
);

CREATE TABLE processed_batches (
    idempotency_key TEXT PRIMARY KEY,
    session TEXT NOT NULL,
    first_seq INTEGER NOT NULL,
    last_seq INTEGER NOT NULL,
    accepted INTEGER NOT NULL,
    merged INTEGER NOT NULL,
    rejected INTEGER NOT NULL,
    at REAL NOT NULL
);
"""

MIGRATIONS = {1: _V1, 2: _V2}


def migrate(connection: sqlite3.Connection) -> None:
    """Bring the database to ``SCHEMA_VERSION``; refuse one from a newer Kvist."""
    connection.execute("BEGIN IMMEDIATE")
    try:
        current = connection.execute("PRAGMA user_version").fetchone()[0]
        if current > SCHEMA_VERSION:
            raise MemoryStorageError(
                f"the memory database has schema {current}, newer than this Kvist knows ({SCHEMA_VERSION}); "
                "it was left untouched — update Kvist to use it"
            )
        for version in range(current + 1, SCHEMA_VERSION + 1):
            # executescript would commit the open transaction; statements run one by one instead.
            for statement in _statements(MIGRATIONS[version]):
                connection.execute(statement)
            connection.execute(f"PRAGMA user_version = {version}")
        connection.execute("COMMIT")
    except BaseException:
        connection.execute("ROLLBACK")
        raise


def _statements(script: str) -> list[str]:
    """Split a migration into statements, keeping trigger bodies whole."""
    statements: list[str] = []
    buffer = ""
    for line in script.splitlines(keepends=True):
        buffer += line
        if sqlite3.complete_statement(buffer):
            statements.append(buffer.strip())
            buffer = ""
    if buffer.strip():
        raise MemoryStorageError("a migration ends inside a statement")
    return statements
