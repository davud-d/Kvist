# -*- coding: utf-8 -*-
"""One-time move of the old ``notes.json`` into the memory database.

Old notes keep their ids, so a note cited in an earlier session still resolves.
Their kinds map onto the new ones: an observation or assumption becomes a
project fact the agent observed or inferred, a decision (only ever the
person's) a decision the person confirmed, and "outdated" becomes "stale"
with its reason. The old file and its backup are removed right after the
commit that holds every note: otherwise a note forgotten later would live on
in them.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError, read_json_object
from kernel.memory.errors import MemoryStorageError
from kernel.memory.text import content_hash

_KIND_AND_TRUST = {
    "observation": ("project_fact", "observed"),
    "assumption": ("project_fact", "inferred"),
    "decision": ("decision", "person"),
}


def legacy_notes_path(workspace: Path) -> Path:
    return Path(workspace) / ".kvist" / "memory" / "notes.json"


def import_legacy_notes(db: sqlite3.Connection, workspace: Path) -> int:
    path = legacy_notes_path(workspace)
    if not path.exists():
        return 0
    # The write lock is taken before reading, so two cores opening the workspace import it once.
    db.execute("BEGIN IMMEDIATE")
    try:
        notes = _read_notes(path, Path(workspace))
        for note in notes:
            _insert(db, note)
        db.execute("COMMIT")
    except BaseException:
        db.execute("ROLLBACK")
        raise
    path.unlink(missing_ok=True)
    path.with_suffix(path.suffix + ".bak").unlink(missing_ok=True)
    return len(notes)


def _read_notes(path: Path, workspace: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    try:
        document = read_json_object(path, boundary=workspace, empty_as_missing=True)
    except JsonStorageError as error:
        raise MemoryStorageError(f"the old notes file .kvist/memory/notes.json is damaged ({error}); "
                                 "it was left as is and memory stays unavailable until it is repaired or removed")
    notes = document.get("notes")
    return list(notes.values()) if isinstance(notes, dict) else []


def _insert(db: sqlite3.Connection, note: dict[str, Any]) -> None:
    if db.execute("SELECT 1 FROM records WHERE id = ?", (note["id"],)).fetchone():
        return
    kind, trust = _KIND_AND_TRUST.get(note.get("kind"), ("project_fact", "inferred"))
    if trust == "person" and note.get("author") != "person":
        trust = "observed"
    outdated = note.get("status") == "outdated"
    created = float(note.get("created_at") or time.time())
    updated = float(note.get("updated_at") or created)
    db.execute(
        "INSERT INTO records (id, kind, content, topic, files, status, status_reason, trust, author, content_hash, "
        "created_at, updated_at, verified_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (note["id"], kind, note["text"], note.get("topic") or "", json.dumps(note.get("files") or []),
         "stale" if outdated else "active", (note.get("outdated_reason") or "") if outdated else "", trust,
         note.get("author") or "agent", content_hash(note["text"]), created, updated,
         created if trust != "inferred" else None),
    )
    source = note.get("source") or {}
    db.execute("INSERT INTO sources (record_id, origin, session, run_id, added_at) VALUES (?, ?, ?, ?, ?)",
               (note["id"], note.get("author") or "agent", str(source.get("session") or ""),
                str(source.get("run_id") or ""), created))
    db.execute("INSERT INTO history (record_id, at, actor, action, detail) VALUES (?, ?, 'import', 'imported', ?)",
               (note["id"], time.time(), f"from notes.json ({note.get('kind')}, {note.get('status')})"))
