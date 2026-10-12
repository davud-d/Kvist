# -*- coding: utf-8 -*-
"""Notes about one workspace that later tasks can find: decisions, observations, assumptions.

Memory never leaves its workspace: notes live in ``<workspace>/.kvist/memory/``
and a skill installed for every project still sees only the notes of the
project it runs in. A note says how much it can be trusted:

* ``decision`` — the person confirmed it (only the person can make one);
* ``observation`` — the agent saw it happen (a test needed a database);
* ``assumption`` — the agent's inference, to be checked before relying on it.

A note can be marked outdated with a reason instead of deleted, so the history
of a decision survives while no one acts on it any more. Notes are redacted
before they are stored, and only the few relevant to a task are put in its
prompt, each with its source.
"""
from __future__ import annotations

import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from kernel.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.redaction import redact_text

KINDS = ("decision", "observation", "assumption")
STATUSES = ("active", "outdated")
MAX_TEXT = 2000
MAX_TOPIC = 80
MAX_NOTES = 2000
MAX_FILES = 20
_WORD_RE = re.compile(r"[\w.-]{3,}", re.UNICODE)
_ID_RE = re.compile(r"^mem_[0-9a-f]{12}$")
_STOPWORDS = frozenset({
    "the", "and", "for", "with", "that", "this", "from", "into", "are", "was", "not", "but",
    "что", "это", "как", "для", "все", "или", "его", "она", "они", "при", "без", "уже", "так",
})


class NoteError(ValueError):
    """A note operation that cannot be done; the message is shown as is."""


@dataclass(slots=True)
class Note:
    id: str
    text: str
    kind: str
    topic: str
    status: str
    author: str
    created_at: float
    updated_at: float
    source: dict[str, Any] = field(default_factory=dict)
    files: list[str] = field(default_factory=list)
    outdated_reason: str = ""

    def public(self) -> dict[str, Any]:
        return asdict(self)


class WorkspaceMemory:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace)
        self.path = self.workspace / ".kvist" / "memory" / "notes.json"

    def add(self, text: str, *, kind: str, author: str, topic: str = "", files: list[str] | None = None,
            source: dict[str, Any] | None = None) -> Note:
        text = _clean_text(text)
        if kind not in KINDS:
            raise NoteError(f"kind must be one of {', '.join(KINDS)}")
        if kind == "decision" and author != "person":
            raise NoteError("only the person can record a decision; save it as an observation or assumption")
        now = time.time()
        note = Note(
            id=f"mem_{uuid.uuid4().hex[:12]}", text=text, kind=kind, topic=_clean_topic(topic), status="active",
            author=author, created_at=now, updated_at=now, source=dict(source or {}), files=_clean_files(files),
        )

        def change(document: dict[str, Any]) -> dict[str, Any]:
            notes = document.setdefault("notes", {})
            if len(notes) >= MAX_NOTES:
                raise NoteError(f"this workspace already has {MAX_NOTES} notes; delete or merge some first")
            notes[note.id] = asdict(note)
            return document

        self._update(change)
        return note

    def update(self, note_id: str, *, author: str, text: str | None = None, topic: str | None = None,
               status: str | None = None, reason: str = "", kind: str | None = None,
               files: list[str] | None = None) -> Note:
        if status is not None and status not in STATUSES:
            raise NoteError(f"status must be one of {', '.join(STATUSES)}")
        if kind is not None and kind not in KINDS:
            raise NoteError(f"kind must be one of {', '.join(KINDS)}")
        result: dict[str, Any] = {}

        def change(document: dict[str, Any]) -> dict[str, Any]:
            notes = document.setdefault("notes", {})
            record = notes.get(note_id)
            if record is None:
                raise NoteError(f"note {note_id} does not exist")
            if author != "person" and (record.get("kind") == "decision" or kind == "decision"):
                if text is not None or kind is not None:
                    raise NoteError("a decision the person confirmed can be changed only by the person; "
                                       "mark it outdated with a reason instead")
            if text is not None:
                record["text"] = _clean_text(text)
            if topic is not None:
                record["topic"] = _clean_topic(topic)
            if kind is not None:
                record["kind"] = kind
            if files is not None:
                record["files"] = _clean_files(files)
            if status is not None:
                record["status"] = status
                record["outdated_reason"] = redact_text(reason.strip())[:MAX_TEXT] if status == "outdated" else ""
            record["updated_at"] = time.time()
            result.update(record)
            return document

        self._update(change)
        return Note(**result)

    def delete(self, note_id: str, *, author: str) -> None:
        def change(document: dict[str, Any]) -> dict[str, Any]:
            notes = document.setdefault("notes", {})
            record = notes.get(note_id)
            if record is None:
                raise NoteError(f"note {note_id} does not exist")
            if record.get("kind") == "decision" and author != "person":
                raise NoteError("only the person can delete a decision; mark it outdated instead")
            del notes[note_id]
            return document

        self._update(change)

    def get(self, note_id: str) -> Note:
        record = self._notes().get(note_id)
        if record is None:
            raise NoteError(f"note {note_id} does not exist")
        return Note(**record)

    def list(self, include_outdated: bool = False) -> list[Note]:
        notes = [Note(**record) for record in self._notes().values()]
        if not include_outdated:
            notes = [note for note in notes if note.status == "active"]
        return sorted(notes, key=lambda note: note.updated_at, reverse=True)

    def search(self, query: str, limit: int = 10, include_outdated: bool = True) -> list[tuple[float, Note]]:
        """Notes ranked by shared words with ``query`` (text, topic, files), newest first on ties."""
        words = _words(query)
        if not words:
            return [(0.0, note) for note in self.list(include_outdated)[:limit]]
        ranked: list[tuple[float, Note]] = []
        for note in self.list(include_outdated):
            haystack = _words(" ".join([note.text, note.topic, *note.files]))
            shared = words & haystack
            if not shared:
                continue
            score = len(shared) / len(words) + (0.25 if note.kind == "decision" else 0.0)
            if note.status == "outdated":
                score *= 0.5
            ranked.append((round(score, 3), note))
        ranked.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return ranked[:limit]

    def relevant(self, text: str, limit: int = 5) -> list[Note]:
        """Active notes that share at least two words with the task (or one, for short tasks)."""
        words = _words(text)
        needed = 1 if len(words) <= 4 else 2
        notes = []
        for score, note in self.search(text, limit=limit * 3, include_outdated=False):
            if len(_words(" ".join([note.text, note.topic, *note.files])) & words) >= needed:
                notes.append(note)
        return notes[:limit]

    def _notes(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            document = read_json_object(self.path, boundary=self.workspace, empty_as_missing=True)
        except JsonStorageError as error:
            raise NoteError(f"the memory file is damaged: {error}") from None
        notes = document.get("notes")
        return notes if isinstance(notes, dict) else {}

    def _update(self, change) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        update_json_object(self.path, change, boundary=self.workspace, empty_as_missing=True)


def render_for_prompt(notes: list[Note]) -> str:
    """The prompt section; each note with its trust level and source, never as an order."""
    if not notes:
        return ""
    lines = []
    for note in notes:
        source = note.source.get("session") or note.author
        files = f" · files: {', '.join(note.files[:5])}" if note.files else ""
        topic = f"[{note.topic}] " if note.topic else ""
        lines.append(f"- {note.id} ({note.kind}, from {source}, {time.strftime('%Y-%m-%d', time.localtime(note.created_at))}"
                     f"{files}): {topic}{note.text}")
    return "\n".join(lines)


def _clean_text(text: str) -> str:
    cleaned = redact_text(" ".join(str(text or "").split()))
    if not cleaned:
        raise NoteError("a note needs text")
    if len(cleaned) > MAX_TEXT:
        raise NoteError(f"a note is at most {MAX_TEXT} characters")
    return cleaned


def _clean_topic(topic: str) -> str:
    return redact_text(" ".join(str(topic or "").split()))[:MAX_TOPIC]


def _clean_files(files: list[str] | None) -> list[str]:
    if not files:
        return []
    if not isinstance(files, list) or not all(isinstance(item, str) for item in files):
        raise NoteError("files must be a list of paths")
    return [item.strip()[:300] for item in files if item.strip()][:MAX_FILES]


def _words(text: str) -> set[str]:
    return {word.lower().strip(".-") for word in _WORD_RE.findall(text or "")} - _STOPWORDS
