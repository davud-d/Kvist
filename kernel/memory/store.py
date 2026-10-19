# -*- coding: utf-8 -*-
"""One memory database: records, their sources, the links between them and their history.

A workspace keeps its records in ``<workspace>/.kvist/memory/memory.db``; the
person's preferences that hold in every project live in
``<config dir>/memory.db``. Two files instead of a ``workspace_id`` column:
a query cannot reach another project's records by a wrong filter, because
they are in no file it opens.

Every rule about who may change what is here, in the runtime, never in a
prompt. ``trust`` says how a record came to be: ``person`` (the person wrote
or confirmed it; only an actor ``person`` can set it), ``observed`` (an agent
or a skill saw it happen) or ``inferred`` (a conclusion to check before
relying on it). Preferences are always the person's. A record the person
confirmed is changed, forgotten or replaced only by the person; anyone may
mark it stale or contradict it, which is how a later task says "this no
longer holds" without overruling the person.

Forgetting deletes the row, its sources, links and history, rebuilds the
full-text index and truncates the write-ahead log, with ``secure_delete`` on,
so the text does not survive in free pages; only the content hash stays, so
an agent cannot store the same thing again.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError, exclusive_storage_lock, validate_storage_path
from kernel.memory import privacy
from kernel.memory.errors import (
    MemoryBusyError,
    MemoryConflictError,
    MemoryNotFound,
    MemoryPermissionError,
    MemoryQuotaExceeded,
    MemoryStorageError,
    MemoryValidationError,
)
from kernel.memory.schema import DIRECTED_RELATIONS, KINDS, RELATIONS, SCHEMA_VERSION, STATUSES, TRUST_LEVELS, migrate
from kernel.memory.text import content_hash, query_terms

MAX_CONTENT = 2000
MAX_TOPIC = 80
MAX_REASON = 500
MAX_FILES = 20
MAX_RECORDS = 2000
PERSON = "person"
WORKSPACE_PREFIX = "mem_"
USER_PREFIX = "memu_"
# Not a stored relation: the extractor's "this candidate says what that record says".
DUPLICATE = "duplicates"
_TRUST_RANK = {"inferred": 0, "observed": 1, "person": 2}
_UPDATABLE_STATUSES = ("active", "stale")


@dataclass(slots=True)
class Record:
    id: str
    scope: str
    kind: str
    content: str
    topic: str
    files: list[str]
    status: str
    status_reason: str
    trust: str
    author: str
    version: int
    superseded_by: str | None
    created_at: float
    updated_at: float
    verified_at: float | None
    # The session a recap sums up; empty for every other kind.
    session: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class Source:
    origin: str
    session: str = ""
    event_seq: int | None = None
    run_id: str = ""
    added_at: float = field(default_factory=time.time)
    # Set only by runtime code that read this exact event from the journal a moment
    # ago (the extractor), so the store need not read the journal again per source.
    seen_in_journal: bool = False


@dataclass(slots=True)
class NewRecord:
    """A record that passed every check that needs no database."""

    content: str
    kind: str
    trust: str
    topic: str
    files: list[str]
    sources: list[Source]
    # A proposed relation to a known record ("mem_…") or to another candidate of the
    # same batch ("c0"): (relation or "duplicates", target, reason). The store decides.
    link: tuple[str, str, str] | None = None


class MemoryStore:
    def __init__(self, path: Path, *, scope: str, boundary: Path, workspace: Path | None = None) -> None:
        self.path = Path(path)
        self.scope = scope
        self.boundary = Path(boundary)
        self.workspace = workspace
        self.prefix = USER_PREFIX if scope == "user" else WORKSPACE_PREFIX

    @classmethod
    def for_workspace(cls, workspace: Path) -> "MemoryStore":
        workspace = Path(workspace)
        return cls(workspace / ".kvist" / "memory" / "memory.db", scope="workspace", boundary=workspace,
                   workspace=workspace)

    @classmethod
    def for_user(cls) -> "MemoryStore":
        from kernel.user_config import get_user_config_path

        folder = get_user_config_path().parent
        return cls(folder / "memory.db", scope="user", boundary=folder.parent)

    # --- writing --------------------------------------------------------

    def remember(self, content: str, *, kind: str, trust: str, actor: str, topic: str = "",
                 files: list[str] | None = None, sources: list[Source] | None = None) -> tuple[str, Record]:
        """Store a record, or merge it into an equal one; returns ("accepted" | "merged", record)."""
        record = self.checked_new_record(content, kind=kind, trust=trust, actor=actor, topic=topic, files=files,
                                         sources=sources)
        with self._write() as db:
            return self._remember_in(db, record, actor)

    def checked_new_record(self, content: str, *, kind: str, trust: str, actor: str, topic: str = "",
                           files: list[str] | None = None, sources: list[Source] | None = None) -> "NewRecord":
        """Every check that needs no database: fields, privacy, who may state it."""
        self._check_new_record(kind, trust, actor)
        return NewRecord(content=self._checked_content(content), kind=kind, trust=trust,
                         topic=self._checked_topic(topic), files=self._checked_files(files or []),
                         sources=list(sources or []))

    def checked_recap(self, content: str, sources: list[Source]) -> "NewRecord":
        """A session recap passes the same content and privacy checks as any record."""
        return NewRecord(content=self._checked_content(content), kind="session_recap", trust="inferred", topic="",
                         files=[], sources=list(sources))

    def processed_up_to(self, session: str) -> int:
        """The last journal event of ``session`` the extractor took in; 0 when none."""
        if not self._has_data():
            return 0
        with self._read() as db:
            row = db.execute("SELECT last_seq FROM processing WHERE session = ?", (session,)).fetchone()
        return int(row[0]) if row else 0

    def apply_extraction(self, *, session: str, first_seq: int, last_seq: int, key: str, records: list["NewRecord"],
                         actor: str, recap: "NewRecord | None" = None) -> dict[str, Any]:
        """Store one batch of extracted records with the session's new checkpoint, all in one transaction.

        A record the store refuses (forgotten, over quota) is listed as rejected and the
        rest go in; a database failure stores nothing and leaves the checkpoint where it was.
        A batch whose ``key`` is already recorded is not stored again. ``recap`` replaces
        the session's recap, unless the person had it forgotten.
        """
        now = time.time()
        outcome: dict[str, Any] = {"accepted": [], "merged": [], "rejected": [], "links": [], "already": False,
                                   "recap": None}
        with self._write() as db:
            if db.execute("SELECT 1 FROM processed_batches WHERE idempotency_key = ?", (key,)).fetchone():
                return {**outcome, "already": True}
            self._consolidate(db, records, actor, outcome)
            if recap is not None and not _is_forgotten(db, recap_marker(session)):
                outcome["recap"] = self._upsert_recap(db, session, recap, actor)
            db.execute("INSERT INTO processed_batches (idempotency_key, session, first_seq, last_seq, accepted, merged, "
                       "rejected, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (key, session, first_seq, last_seq, len(outcome["accepted"]), len(outcome["merged"]),
                        len(outcome["rejected"]), now))
            db.execute("INSERT INTO processing (session, last_seq, updated_at) VALUES (?, ?, ?) "
                       "ON CONFLICT(session) DO UPDATE SET last_seq = MAX(last_seq, excluded.last_seq), "
                       "updated_at = excluded.updated_at", (session, last_seq, now))
        return outcome

    def _consolidate(self, db: sqlite3.Connection, records: list["NewRecord"], actor: str,
                     outcome: dict[str, Any]) -> None:
        """NEW, MERGE, LINK, CONFLICT, SUPERSEDE or REJECT for each extracted record, inside the batch's transaction.

        A model's "this says the same as mem_x" only adds sources to mem_x: it never
        rewrites it, so a wrong guess costs a citation, not a fact. Links are applied
        after every record exists, so one candidate may point at another ("c0").
        """
        stored_ids: dict[str, str] = {}
        links: list[tuple[str, tuple[str, str, str]]] = []
        for index, record in enumerate(records):
            twin = self._duplicate_target(db, record)
            if twin is not None:
                self._add_sources(db, twin, record.sources)
                db.execute("UPDATE records SET version = version + 1, updated_at = ? WHERE id = ?", (time.time(), twin))
                _history(db, twin, actor, "merged", "an extracted finding said the same")
                outcome["merged"].append(twin)
                stored_ids[f"c{index}"] = twin
                continue
            try:
                result, stored = self._remember_in(db, record, actor)
            except (MemoryValidationError, MemoryQuotaExceeded) as error:
                outcome["rejected"].append({"content": record.content, "reason": str(error)})
                continue
            outcome[result].append(stored.id)
            stored_ids[f"c{index}"] = stored.id
            if record.link is not None:
                links.append((stored.id, record.link))
        for source_id, (relation, target, reason) in links:
            outcome["links"].append(self._extracted_link(db, source_id, stored_ids.get(target, target), relation,
                                                         actor, reason))

    def _duplicate_target(self, db: sqlite3.Connection, record: "NewRecord") -> str | None:
        if record.link is None or record.link[0] != DUPLICATE or not record.link[1].startswith(self.prefix):
            return None
        row = db.execute("SELECT id FROM records WHERE id = ? AND status != 'superseded' AND kind != 'session_recap'",
                         (record.link[1],)).fetchone()
        return row["id"] if row else None

    def _extracted_link(self, db: sqlite3.Connection, source_id: str, target_id: str, relation: str, actor: str,
                        reason: str) -> dict[str, str]:
        """Apply a proposed link; a replacement the rules refuse becomes a recorded conflict instead."""
        result = {"source": source_id, "relation": relation, "target": target_id}
        if source_id == target_id:
            return {**result, "result": "refused", "reason": "a record cannot be linked to itself"}
        try:
            self._link_in(db, source_id, target_id, relation, actor, reason)
            return {**result, "result": "linked"}
        except MemoryPermissionError as error:
            if relation != "supersedes":
                return {**result, "result": "refused", "reason": str(error)}
        except (MemoryValidationError, MemoryNotFound) as error:
            return {**result, "result": "refused", "reason": str(error)}
        # The record it would replace is the person's: say they disagree, and the person decides.
        self._link_in(db, source_id, target_id, "contradicts", actor, reason)
        return {**result, "relation": "contradicts", "result": "conflict",
                "reason": f"{target_id} was confirmed by the person, so it is marked as contradicted, not replaced"}

    def recaps(self, *, limit: int, exclude_session: str = "") -> list[Record]:
        """The newest session recaps, never the one of ``exclude_session`` (it is that session's own history)."""
        if not self._has_data():
            return []
        with self._read() as db:
            rows = db.execute("SELECT * FROM records WHERE kind = 'session_recap' AND status = 'active' "
                              "AND session != ? ORDER BY updated_at DESC LIMIT ?", (exclude_session, limit)).fetchall()
        return [self._record(row) for row in rows]

    def recap_of(self, session: str) -> Record | None:
        if not self._has_data():
            return None
        with self._read() as db:
            row = db.execute("SELECT * FROM records WHERE kind = 'session_recap' AND session = ?", (session,)).fetchone()
        return self._record(row) if row else None

    def try_lease(self, session: str, holder: str, seconds: float) -> bool:
        """Claim the right to process ``session`` for a while; False when another core holds it."""
        now = time.time()
        with self._write() as db:
            row = db.execute("SELECT holder, expires_at FROM leases WHERE session = ?", (session,)).fetchone()
            if row is not None and row["holder"] != holder and row["expires_at"] > now:
                return False
            db.execute("INSERT INTO leases (session, holder, expires_at) VALUES (?, ?, ?) ON CONFLICT(session) "
                       "DO UPDATE SET holder = excluded.holder, expires_at = excluded.expires_at",
                       (session, holder, now + seconds))
        return True

    def release_lease(self, session: str, holder: str) -> None:
        with self._write() as db:
            db.execute("DELETE FROM leases WHERE session = ? AND holder = ?", (session, holder))

    def take_model_calls(self, day: str, wanted: int, limit: int) -> int:
        """Reserve up to ``wanted`` model calls under the day's ``limit``; returns how many were granted."""
        key = f"model_calls:{day}"
        with self._write() as db:
            row = db.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
            used = int(row[0]) if row else 0
            granted = max(0, min(wanted, limit - used))
            db.execute("INSERT INTO meta (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                       (key, str(used + granted)))
        return granted

    def last_processed(self) -> dict[str, Any] | None:
        if not self._has_data():
            return None
        with self._read() as db:
            row = db.execute("SELECT session, updated_at FROM processing ORDER BY updated_at DESC LIMIT 1").fetchone()
        return dict(row) if row else None

    def _upsert_recap(self, db: sqlite3.Connection, session: str, recap: "NewRecord", actor: str) -> str:
        """One recap per session, rewritten as the session grows; its history keeps each rewrite."""
        now = time.time()
        existing = db.execute("SELECT id, content FROM records WHERE kind = 'session_recap' AND session = ?",
                              (session,)).fetchone()
        if existing is None:
            record_id = f"{self.prefix}{uuid.uuid4().hex[:12]}"
            db.execute(
                "INSERT INTO records (id, kind, content, topic, files, status, trust, author, content_hash, created_at, "
                "updated_at, session) VALUES (?, 'session_recap', ?, '', '[]', 'active', 'inferred', ?, ?, ?, ?, ?)",
                (record_id, recap.content, actor, content_hash(recap.content), now, now, session))
            _history(db, record_id, actor, "created", "session recap")
        else:
            record_id = existing["id"]
            if existing["content"] == recap.content:
                return record_id
            db.execute("UPDATE records SET content = ?, content_hash = ?, version = version + 1, updated_at = ? "
                       "WHERE id = ?", (recap.content, content_hash(recap.content), now, record_id))
            _history(db, record_id, actor, "updated", "the session went on")
        self._add_sources(db, record_id, recap.sources)
        return record_id

    def _remember_in(self, db: sqlite3.Connection, record: "NewRecord", actor: str) -> tuple[str, Record]:
        digest = content_hash(record.content)
        now = time.time()
        forgotten = db.execute("SELECT actor FROM forgotten WHERE content_hash = ?", (digest,)).fetchone()
        if forgotten is not None:
            if actor != PERSON:
                who = "the person asked Kvist to forget" if forgotten["actor"] == PERSON else "earlier Kvist forgot"
                raise MemoryValidationError(f"{who} exactly this; it is not stored again")
            db.execute("DELETE FROM forgotten WHERE content_hash = ?", (digest,))
        twin = db.execute("SELECT * FROM records WHERE content_hash = ? AND status != 'superseded'",
                          (digest,)).fetchone()
        if twin is not None:
            return "merged", self._merge(db, twin, record.trust, actor, record.sources, now)
        if db.execute("SELECT COUNT(*) FROM records").fetchone()[0] >= MAX_RECORDS:
            raise MemoryQuotaExceeded(f"this memory already holds {MAX_RECORDS} records; forget or merge some first")
        record_id = f"{self.prefix}{uuid.uuid4().hex[:12]}"
        db.execute(
            "INSERT INTO records (id, kind, content, topic, files, status, trust, author, content_hash, "
            "created_at, updated_at, verified_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, ?)",
            (record_id, record.kind, record.content, record.topic, json.dumps(record.files), record.trust, actor,
             digest, now, now, now if record.trust != "inferred" else None),
        )
        self._add_sources(db, record_id, record.sources or [Source(origin=actor)])
        _history(db, record_id, actor, "created", f"{record.kind}, {record.trust}")
        return "accepted", self._row(db, record_id)

    def update(self, record_id: str, *, actor: str, expected_version: int, reason: str,
               content: str | None = None, topic: str | None = None, files: list[str] | None = None,
               kind: str | None = None, status: str | None = None) -> Record:
        reason = self._checked_reason(reason, required=True)
        if kind is not None and kind not in KINDS:
            raise MemoryValidationError(f"kind must be one of {', '.join(KINDS)}")
        if status is not None and status not in _UPDATABLE_STATUSES:
            raise MemoryValidationError("status can be set to active or stale; a record is superseded or "
                                        "conflicted through link")
        content = self._checked_content(content) if content is not None else None
        topic = self._checked_topic(topic) if topic is not None else None
        files = self._checked_files(files) if files is not None else None
        with self._write() as db:
            current = self._row(db, record_id)
            if current.version != expected_version:
                raise MemoryConflictError(f"{record_id} is at version {current.version}, not {expected_version}; "
                                          "get it again and decide on the current text")
            rewrites = content is not None or kind is not None or files is not None or topic is not None
            if rewrites and current.trust == PERSON and actor != PERSON:
                raise MemoryPermissionError("a record the person confirmed is rewritten only by the person; "
                                            "mark it stale with a reason, or link a contradicting record")
            # Rewriting a record is the person stating it, so it becomes theirs.
            trust = PERSON if rewrites and actor == PERSON else current.trust
            if (kind or current.kind) == "preference" and trust != PERSON:
                raise MemoryPermissionError("a preference is the person's; only a record they confirmed can be one")
            if current.status == "superseded" and status is not None and actor != PERSON:
                raise MemoryPermissionError("a superseded record is brought back only by the person")
            if content is not None and actor != PERSON and _is_forgotten(db, content_hash(content)):
                raise MemoryValidationError("this text was forgotten; it is not stored again")
            changes: dict[str, Any] = {"trust": trust}
            if content is not None:
                changes["content"] = content
                changes["content_hash"] = content_hash(content)
            if topic is not None:
                changes["topic"] = topic
            if files is not None:
                changes["files"] = json.dumps(files)
            if kind is not None:
                changes["kind"] = kind
            if status is not None:
                changes["status"] = status
                changes["status_reason"] = reason if status == "stale" else ""
                if status == "active":
                    changes["verified_at"] = time.time()
                    changes["superseded_by"] = None
            changes["version"] = current.version + 1
            changes["updated_at"] = time.time()
            assignments = ", ".join(f"{column} = ?" for column in changes)
            db.execute(f"UPDATE records SET {assignments} WHERE id = ?", (*changes.values(), record_id))
            changed = sorted(set(changes) - {"version", "updated_at", "content_hash", "status_reason", "verified_at",
                                             "superseded_by", "trust"})
            _history(db, record_id, actor, "updated", f"{', '.join(changed)}: {reason}")
            return self._row(db, record_id)

    def forget(self, record_id: str, *, actor: str) -> dict[str, Any]:
        with self._write() as db:
            current = self._row(db, record_id)
            if current.trust == PERSON and actor != PERSON:
                raise MemoryPermissionError("a record the person confirmed is forgotten only by the person")
            links = db.execute("SELECT COUNT(*) FROM relations WHERE source_id = ? OR target_id = ?",
                               (record_id, record_id)).fetchone()[0]
            digest = db.execute("SELECT content_hash FROM records WHERE id = ?", (record_id,)).fetchone()[0]
            db.execute("INSERT OR REPLACE INTO forgotten (content_hash, forgotten_at, actor) VALUES (?, ?, ?)",
                       (digest, time.time(), actor))
            if current.kind == "session_recap":
                # A forgotten recap means "do not sum up this session", not just "not this wording".
                db.execute("INSERT OR REPLACE INTO forgotten (content_hash, forgotten_at, actor) VALUES (?, ?, ?)",
                           (recap_marker(current.session), time.time(), actor))
            reopened = _reopen_dependents(db, record_id, actor)
            db.execute("DELETE FROM records WHERE id = ?", (record_id,))
            # The index keeps deleted rows' tokens in old segments until they are merged; a rebuild drops them.
            db.execute("INSERT INTO records_fts(records_fts) VALUES ('rebuild')")
        return {"forgotten": record_id, "links_removed": links, "now_stale": reopened,
                "log_cleared": self._clear_log()}

    def link(self, source_id: str, target_id: str, relation: str, *, actor: str, reason: str = "") -> dict[str, Any]:
        if relation not in RELATIONS:
            raise MemoryValidationError(f"relation must be one of {', '.join(RELATIONS)}")
        if source_id == target_id:
            raise MemoryValidationError("a record cannot be linked to itself")
        reason = self._checked_reason(reason, required=relation in ("supersedes", "contradicts"))
        with self._write() as db:
            return self._link_in(db, source_id, target_id, relation, actor, reason)

    def _link_in(self, db: sqlite3.Connection, source_id: str, target_id: str, relation: str, actor: str,
                 reason: str) -> dict[str, Any]:
        """Every check runs before the first write, so a refused link leaves the transaction clean."""
        source, target = self._row(db, source_id), self._row(db, target_id)
        if _linked(db, source_id, target_id, relation):
            return {"linked": False, "already": True, "relation": relation}
        if relation in DIRECTED_RELATIONS and _reaches(db, target_id, source_id, relation):
            raise MemoryValidationError(f"{target_id} already leads back to {source_id} through {relation}; "
                                        "the link would make a cycle")
        if relation == "supersedes":
            self._supersede(db, source, target, actor, reason)
        if relation == "contradicts":
            self._mark_conflicted(db, (source, target), actor, reason)
        db.execute("INSERT INTO relations (source_id, target_id, relation, author, reason, created_at) "
                   "VALUES (?, ?, ?, ?, ?, ?)", (source_id, target_id, relation, actor, reason, time.time()))
        _history(db, source_id, actor, "linked", f"{relation} {target_id}")
        return {"linked": True, "relation": relation, "source": self._row(db, source_id).public(),
                "target": self._row(db, target_id).public()}

    # --- reading --------------------------------------------------------

    def get(self, record_id: str) -> Record:
        self._require_database(record_id)
        with self._read() as db:
            return self._row(db, record_id)

    def list(self, *, statuses: tuple[str, ...] = STATUSES, kinds: tuple[str, ...] = KINDS,
             limit: int = 50) -> list[Record]:
        if not self._has_data():
            return []
        with self._read() as db:
            rows = db.execute(
                f"SELECT * FROM records WHERE status IN ({_marks(statuses)}) AND kind IN ({_marks(kinds)}) "
                "ORDER BY updated_at DESC LIMIT ?", (*statuses, *kinds, limit)).fetchall()
            return [self._record(row) for row in rows]

    def search(self, query: str, *, statuses: tuple[str, ...] = STATUSES, kinds: tuple[str, ...] = KINDS,
               limit: int = 10) -> list[tuple[float, Record]]:
        """Records ranked by the share of query terms they contain; newest first without a query."""
        terms = query_terms(query)
        if not terms:
            return [(0.0, record) for record in self.list(statuses=statuses, kinds=kinds, limit=limit)]
        if not self._has_data():
            return []
        match = " OR ".join(f'"{term}"' for term in terms)
        with self._read() as db:
            rows = db.execute(
                f"SELECT records.* FROM records_fts JOIN records ON records.seq = records_fts.rowid "
                f"WHERE records_fts MATCH ? AND status IN ({_marks(statuses)}) AND kind IN ({_marks(kinds)}) "
                "ORDER BY bm25(records_fts) LIMIT ?", (match, *statuses, *kinds, limit * 4)).fetchall()
        ranked = []
        for row in rows:
            record = self._record(row)
            haystack = " ".join([record.content, record.topic, *record.files]).casefold()
            matched = sum(1 for term in terms if term in haystack)
            ranked.append((matched / len(terms), record))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return ranked[:limit]

    def explain(self, record_id: str) -> dict[str, Any]:
        """Where a record came from, built only from what is stored; a source that is gone is said to be gone."""
        self._require_database(record_id)
        with self._read() as db:
            record = self._row(db, record_id)
            sources = [dict(row) for row in db.execute(
                "SELECT origin, session, event_seq, run_id, added_at FROM sources WHERE record_id = ? ORDER BY id",
                (record_id,))]
            relations = [dict(row) for row in db.execute(
                "SELECT source_id, target_id, relation, author, reason, created_at FROM relations "
                "WHERE source_id = ? OR target_id = ? ORDER BY created_at", (record_id, record_id))]
            history = [dict(row) for row in db.execute(
                "SELECT at, actor, action, detail FROM history WHERE record_id = ? ORDER BY id", (record_id,))]
            for relation in relations:
                other = relation["target_id"] if relation["source_id"] == record_id else relation["source_id"]
                row = db.execute("SELECT id, kind, status, content FROM records WHERE id = ?", (other,)).fetchone()
                relation["other"] = dict(row) if row else None
        for source in sources:
            source["available"] = self._source_available(source)
        return {"record": record.public(), "sources": sources, "relations": relations, "history": history}

    def counts(self) -> dict[str, Any]:
        if not self._has_data():
            return {"total": 0, "by_status": {}, "by_kind": {}}
        with self._read() as db:
            by_status = dict(db.execute("SELECT status, COUNT(*) FROM records GROUP BY status").fetchall())
            by_kind = dict(db.execute("SELECT kind, COUNT(*) FROM records GROUP BY kind").fetchall())
        return {"total": sum(by_status.values()), "by_status": by_status, "by_kind": by_kind}

    # --- rules ----------------------------------------------------------

    def _check_new_record(self, kind: str, trust: str, actor: str) -> None:
        if kind not in KINDS:
            raise MemoryValidationError(f"kind must be one of {', '.join(KINDS)}")
        if trust not in TRUST_LEVELS:
            raise MemoryValidationError(f"trust must be one of {', '.join(TRUST_LEVELS)}")
        if trust == PERSON and actor != PERSON:
            raise MemoryPermissionError("only the person can confirm a record")
        if kind == "preference" and trust != PERSON:
            raise MemoryPermissionError("a preference is the person's: it is saved only when they confirm it")
        if self.scope == "user" and kind != "preference":
            raise MemoryValidationError("only preferences hold in every project; project facts and decisions "
                                        "stay with their workspace")

    def _checked_content(self, content: str) -> str:
        content = " ".join(str(content or "").split())
        if not content:
            raise MemoryValidationError("a record needs content")
        if len(content) > MAX_CONTENT:
            raise MemoryValidationError(f"a record is at most {MAX_CONTENT} characters")
        privacy.check_text(content, "content")
        return content

    def _checked_topic(self, topic: str) -> str:
        topic = " ".join(str(topic or "").split())
        if len(topic) > MAX_TOPIC:
            raise MemoryValidationError(f"a topic is at most {MAX_TOPIC} characters")
        privacy.check_text(topic, "topic")
        return topic

    def _checked_files(self, files: list[str]) -> list[str]:
        if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
            raise MemoryValidationError("files must be a list of workspace-relative paths")
        files = [item.strip() for item in files if item.strip()]
        if len(files) > MAX_FILES:
            raise MemoryValidationError(f"a record names at most {MAX_FILES} files")
        if files and self.workspace is None:
            raise MemoryValidationError("a preference for every project names no files")
        if files:
            privacy.check_files(self.workspace, files)
        return files

    def _checked_reason(self, reason: str, *, required: bool) -> str:
        reason = " ".join(str(reason or "").split())
        if required and not reason:
            raise MemoryValidationError("this change needs a reason; it is kept in the record's history")
        if len(reason) > MAX_REASON:
            raise MemoryValidationError(f"a reason is at most {MAX_REASON} characters")
        privacy.check_text(reason, "reason")
        return reason

    def _merge(self, db: sqlite3.Connection, twin: sqlite3.Row, trust: str, actor: str, sources: list[Source],
               now: float) -> Record:
        record_id = twin["id"]
        stronger = _TRUST_RANK[trust] > _TRUST_RANK[twin["trust"]]
        # Seen again is checked again: a stale record observed once more holds, a conflict stays for a decision.
        confirmed_again = twin["status"] == "stale" and trust != "inferred"
        db.execute("UPDATE records SET trust = ?, updated_at = ?, version = version + 1, "
                   "verified_at = COALESCE(?, verified_at), status = ?, status_reason = ? WHERE id = ?",
                   (trust if stronger else twin["trust"], now, now if trust != "inferred" else None,
                    "active" if confirmed_again else twin["status"],
                    "" if confirmed_again else twin["status_reason"], record_id))
        self._add_sources(db, record_id, sources or [Source(origin=actor)])
        _history(db, record_id, actor, "merged", f"the same content again ({trust})"
                 + ("; active again" if confirmed_again else ""))
        return self._row(db, record_id)

    def _supersede(self, db: sqlite3.Connection, source: Record, target: Record, actor: str, reason: str) -> None:
        if source.status != "active":
            raise MemoryValidationError(f"{source.id} is {source.status}; only an active record replaces another")
        if target.trust == PERSON and (source.trust != PERSON or actor != PERSON):
            raise MemoryPermissionError(f"{target.id} is the person's; only a record the person confirmed replaces "
                                        "it. Link it with contradicts instead, and the person decides")
        db.execute("UPDATE records SET status = 'superseded', superseded_by = ?, status_reason = ?, "
                   "version = version + 1, updated_at = ? WHERE id = ?", (source.id, reason, time.time(), target.id))
        _history(db, target.id, actor, "superseded", f"by {source.id}: {reason}")

    def _mark_conflicted(self, db: sqlite3.Connection, records: tuple[Record, Record], actor: str,
                         reason: str) -> None:
        for record in records:
            if record.status == "superseded":
                continue
            db.execute("UPDATE records SET status = 'conflicted', status_reason = ?, version = version + 1, "
                       "updated_at = ? WHERE id = ?", (reason, time.time(), record.id))
            _history(db, record.id, actor, "conflicted", reason)

    def _add_sources(self, db: sqlite3.Connection, record_id: str, sources: list[Source]) -> None:
        for source in sources:
            if source.event_seq is not None and not source.seen_in_journal:
                self._check_session_source(source.session, source.event_seq)
            db.execute("INSERT INTO sources (record_id, origin, session, event_seq, run_id, added_at) "
                       "VALUES (?, ?, ?, ?, ?, ?)",
                       (record_id, source.origin, source.session, source.event_seq, source.run_id or "",
                        source.added_at))

    def _check_session_source(self, session: str, seq: int) -> None:
        if not self._source_available({"session": session, "event_seq": seq}):
            raise MemoryValidationError(f"session {session!r} has no event {seq} in this workspace; "
                                        "cite only events a sessions search or read returned")

    def _source_available(self, source: dict[str, Any]) -> bool | None:
        """True or False for a cited journal event; None when the source names no event."""
        session, seq = source.get("session"), source.get("event_seq")
        if not session or seq is None or self.workspace is None:
            return None
        from kernel.agent_logging import get_journal_path, is_valid_session_name
        from kernel.session_events import read_history
        from kernel.session_store import SessionStore

        if not is_valid_session_name(session):
            return False
        try:
            page = read_history(get_journal_path(SessionStore(self.workspace), session), self.workspace,
                                limit=1, before=int(seq) + 1)
        except (JsonStorageError, OSError, ValueError):
            return False
        return bool(page["events"]) and page["events"][-1]["seq"] == int(seq)

    # --- storage --------------------------------------------------------

    def _require_database(self, record_id: str) -> None:
        if not self._has_data():
            raise MemoryNotFound(f"no record {record_id} in this memory")

    def _has_data(self) -> bool:
        """Reading never creates the file: a workspace without memory stays without a database.

        Old notes waiting to be moved in count as data, so the first read imports them.
        """
        if self.path.exists():
            return True
        if self.workspace is None:
            return False
        from kernel.memory.legacy import legacy_notes_path

        return legacy_notes_path(self.workspace).exists()

    def _row(self, db: sqlite3.Connection, record_id: str) -> Record:
        row = db.execute("SELECT * FROM records WHERE id = ?", (str(record_id),)).fetchone()
        if row is None:
            raise MemoryNotFound(f"no record {record_id} in this memory")
        return self._record(row)

    def _record(self, row: sqlite3.Row) -> Record:
        return Record(
            id=row["id"], scope=self.scope, kind=row["kind"], content=row["content"], topic=row["topic"],
            files=json.loads(row["files"]), status=row["status"], status_reason=row["status_reason"],
            trust=row["trust"], author=row["author"], version=row["version"], superseded_by=row["superseded_by"],
            created_at=row["created_at"], updated_at=row["updated_at"], verified_at=row["verified_at"],
            session=row["session"],
        )

    @contextmanager
    def _read(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            yield db

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        with self._connection() as db:
            try:
                db.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as error:
                raise MemoryBusyError(f"the memory database is busy: {error}") from None
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
            raise _storage_error(error) from None

    def _open(self) -> sqlite3.Connection:
        """This thread's connection to the database, reused while it still points at the same file.

        Opening one per operation cost a spinning disk ~130 ms per write: when the last
        connection to a WAL database closes, SQLite checkpoints it, syncs and deletes the
        log, every time. A connection is reused only while the file keeps its inode (a
        deleted database must not be written into a ghost) and its schema version (a
        newer Kvist may have migrated it meanwhile).
        """
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            validate_storage_path(self.path, self.boundary)
        except (JsonStorageError, OSError) as error:
            raise MemoryStorageError(f"the memory database path is unsafe: {error}") from None
        cached = _reusable(self.path)
        if cached is not None:
            return cached
        db = self._connect()
        _remember_connection(self.path, db)
        return db

    def _connect(self) -> sqlite3.Connection:
        created = not self.path.exists()
        try:
            db = sqlite3.connect(self.path, timeout=5.0, isolation_level=None)
        except sqlite3.DatabaseError as error:
            raise _storage_error(error) from None
        try:
            self._prepare(db, created)
        except sqlite3.DatabaseError as error:
            db.close()
            raise _storage_error(error) from None
        except MemoryStorageError:
            db.close()
            raise
        except JsonStorageError as error:
            db.close()
            raise MemoryStorageError(f"the memory database cannot be prepared: {error}") from None
        return db

    def _prepare(self, db: sqlite3.Connection, created: bool) -> None:
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
        db.execute("PRAGMA secure_delete = ON")
        # With WAL, NORMAL never corrupts and survives a killed process; a power cut may lose
        # the last few commits. A record can be learned again; a forget cannot be undone that
        # way, because forget ends with a TRUNCATE checkpoint, which syncs the file. FULL cost
        # a spinning disk 40 ms per write against 0.4 ms.
        db.execute("PRAGMA synchronous = NORMAL")
        if created:
            os.chmod(self.path, 0o600)
        # Checked on every open, not cached: a file deleted meanwhile is recreated with its schema.
        if not self._ready(db):
            # Switching a fresh file to WAL fails at once, without waiting, while another
            # connection does the same; the first setup is serialized across processes.
            with exclusive_storage_lock(self.path, boundary=self.boundary):
                if not self._ready(db):
                    db.execute("PRAGMA journal_mode = WAL")
                    migrate(db)
        if self.workspace is not None:
            from kernel.memory.legacy import import_legacy_notes

            import_legacy_notes(db, self.workspace)

    @staticmethod
    def _ready(db: sqlite3.Connection) -> bool:
        return (db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
                and db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION)

    def _clear_log(self) -> bool:
        """Fold the write-ahead log into the file and truncate it; False when a reader still holds it."""
        with self._connection() as db:
            busy, _log, _done = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        return busy == 0


def _history(db: sqlite3.Connection, record_id: str, actor: str, action: str, detail: str) -> None:
    db.execute("INSERT INTO history (record_id, at, actor, action, detail) VALUES (?, ?, ?, ?, ?)",
               (record_id, time.time(), actor, action, detail))


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
        same_file = path.stat().st_ino == inode
        current = same_file and db.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
    except (OSError, sqlite3.DatabaseError):
        current = False
    if current:
        return db
    _evict(path)
    return None


def _remember_connection(path: Path, db: sqlite3.Connection) -> None:
    _connections()[str(path)] = (db, path.stat().st_ino)


def _evict(path: Path) -> None:
    entry = _connections().pop(str(path), None)
    if entry is not None:
        entry[0].close()


def close_connections() -> None:
    """Close this thread's memory connections (a scratch folder can then be removed on Windows)."""
    for key in list(_connections()):
        _evict(Path(key))


def recap_marker(session: str) -> str:
    """The forgotten-table key that keeps a session from being summed up again."""
    return content_hash(f"session recap of {session}")


def _is_forgotten(db: sqlite3.Connection, digest: str) -> bool:
    return db.execute("SELECT 1 FROM forgotten WHERE content_hash = ?", (digest,)).fetchone() is not None


def _reopen_dependents(db: sqlite3.Connection, record_id: str, actor: str) -> list[str]:
    """Records whose state rested on the one being forgotten become stale, not orphaned.

    A record it replaced would otherwise stay superseded by nothing, and one it
    contradicted would stay conflicted with nothing; both are worth a fresh look.
    """
    replaced = [row[0] for row in db.execute(
        "SELECT id FROM records WHERE superseded_by = ? AND status = 'superseded'", (record_id,))]
    contradicted = [row[0] for row in db.execute(
        "SELECT records.id FROM relations JOIN records ON records.id = "
        "CASE WHEN relations.source_id = ? THEN relations.target_id ELSE relations.source_id END "
        "WHERE relation = 'contradicts' AND ? IN (source_id, target_id) AND records.status = 'conflicted'",
        (record_id, record_id))]
    still_conflicted = {
        other for other in contradicted
        if db.execute("SELECT 1 FROM relations WHERE relation = 'contradicts' AND ? IN (source_id, target_id) "
                      "AND ? NOT IN (source_id, target_id)", (other, record_id)).fetchone()
    }
    reopened = replaced + [other for other in contradicted if other not in still_conflicted]
    for other in reopened:
        reason = f"{record_id}, which this record depended on, was forgotten; check it again"
        db.execute("UPDATE records SET status = 'stale', status_reason = ?, superseded_by = NULL, "
                   "version = version + 1, updated_at = ? WHERE id = ?", (reason, time.time(), other))
        _history(db, other, actor, "reopened", reason)
    return reopened


def _reaches(db: sqlite3.Connection, start: str, goal: str, relation: str) -> bool:
    seen, frontier = {start}, [start]
    while frontier:
        current = frontier.pop()
        if current == goal:
            return True
        for (following,) in db.execute("SELECT target_id FROM relations WHERE source_id = ? AND relation = ?",
                                       (current, relation)):
            if following not in seen:
                seen.add(following)
                frontier.append(following)
    return False


def _linked(db: sqlite3.Connection, source_id: str, target_id: str, relation: str) -> bool:
    pairs = [(source_id, target_id)]
    if relation not in DIRECTED_RELATIONS:
        pairs.append((target_id, source_id))
    return any(db.execute("SELECT 1 FROM relations WHERE source_id = ? AND target_id = ? AND relation = ?",
                          (source, target, relation)).fetchone() for source, target in pairs)


def _marks(values: tuple[str, ...]) -> str:
    return ", ".join("?" for _ in values)


def _storage_error(error: sqlite3.DatabaseError) -> MemoryStorageError:
    text = str(error)
    if "locked" in text or "busy" in text:
        return MemoryBusyError(f"the memory database is busy: {text}")
    return MemoryStorageError(f"the memory database cannot be used ({text}); it was left as is")
