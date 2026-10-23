# -*- coding: utf-8 -*-
"""Source-neutral form of a conversation read from another coding agent."""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from urllib.request import url2pathname


@dataclass(slots=True)
class ImportedTurn:
    """One user request and what the other agent answered to it.

    ``files_changed`` and ``commands`` are all Kvist keeps of the tool calls:
    their arguments and output belong to another agent's sandbox and are not
    replayed into this one's prompt.
    """

    user: str
    answer: str = ""
    files_changed: list[str] = field(default_factory=list)
    commands: int = 0
    stopped: bool = False
    ts: float = 0.0

    def note_file(self, path: object) -> None:
        if isinstance(path, str) and path and path not in self.files_changed:
            self.files_changed.append(path)


@dataclass(slots=True)
class SessionSummary:
    """A session found on disk, described without parsing its whole body."""

    source: str
    id: str
    title: str
    cwd: str
    updated_at: float
    location: str


@dataclass(slots=True)
class ImportedSession:
    summary: SessionSummary
    turns: list[ImportedTurn]


@dataclass(frozen=True, slots=True)
class Source:
    """One supported agent: how to find its sessions and how to read one.

    ``prefix`` starts the Kvist session name, so a re-import finds the session
    it made before. ``find`` gets the user's home and the workspace: most
    agents keep every session under the home, but Aider writes its history
    into the project folder.
    """

    key: str
    label: str
    prefix: str
    find: Callable[[Path, Path], list[SessionSummary]]
    load: Callable[[SessionSummary], ImportedSession]


def home_dir() -> Path:
    return Path.home()


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """The JSON objects of a JSONL file; a torn or foreign line costs only itself."""
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            try:
                record = json.loads(line)
            except ValueError:
                continue
            if isinstance(record, dict):
                records.append(record)
    return records


def head_jsonl(path: Path, max_bytes: int = 65536) -> list[dict[str, Any]]:
    """The JSON objects in the first ``max_bytes`` of a JSONL file (for a cheap listing)."""
    with path.open("rb") as handle:
        chunk = handle.read(max_bytes)
    records: list[dict[str, Any]] = []
    lines = chunk.decode("utf-8", errors="replace").splitlines()
    # Only a chunk cut short can end in a torn line; a whole small file ends in a real record.
    for line in lines[:-1] if len(chunk) == max_bytes else lines:
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def iso_to_epoch(value: object) -> float:
    if not isinstance(value, str):
        return 0.0
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return 0.0


def millis_to_epoch(value: object) -> float:
    if isinstance(value, (int, float)):
        return float(value) / 1000.0
    if isinstance(value, str) and value.isdigit():
        return int(value) / 1000.0
    return 0.0


_ESCAPED_DRIVE_RE = re.compile(r"^/([A-Za-z])%3[Aa]")


def file_uri_path(value: str) -> str:
    """A ``file:`` URI as this system's path, percent-escapes decoded; any other text unchanged.

    ``url2pathname`` applies the local rules, so ``file:///D:/x`` and VS Code's
    ``file:///d%3A/x`` become drive paths on Windows instead of ``/D:/x``.
    """
    if not value.startswith("file:"):
        return value
    parts = urlsplit(value)
    # url2pathname finds a drive only by its literal colon, and VS Code escapes it.
    path = _ESCAPED_DRIVE_RE.sub(r"/\1:", parts.path)
    if parts.netloc and parts.netloc != "localhost":
        path = f"//{parts.netloc}{path}"
    return url2pathname(path)


def short_title(text: str, limit: int = 80) -> str:
    """First line of ``text`` for a session list row."""
    line = next((row.strip() for row in text.splitlines() if row.strip()), "")
    return line if len(line) <= limit else line[: limit - 1].rstrip() + "…"


_PATCH_FILE_RE = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+)$", re.MULTILINE)


def patch_paths(patch: str) -> list[str]:
    """Paths named by an ``apply_patch`` envelope."""
    return [match.strip() for match in _PATCH_FILE_RE.findall(patch)]


def is_inside(cwd: str, workspace: Path) -> bool:
    """Whether the folder a session ran in is the workspace or lies below it."""
    if not cwd:
        return False
    try:
        Path(cwd).resolve().relative_to(workspace)
    except (ValueError, OSError):
        return False
    return True
