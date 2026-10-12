# -*- coding: utf-8 -*-
"""Approved capabilities per skill content and workspace.

A grant names the exact package digest it was given for, so new content never
inherits trust: an update with an unchanged permission list still needs its own
approval. Grants live in the user's configuration directory, which no skill
runner and no workspace action can write.
"""
from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.durable_json import read_json_object, update_json_object
from kernel.plugins.paths import config_dir, grants_path

GRANTS_FORMAT = 1


@dataclass(frozen=True, slots=True)
class Grant:
    skill_id: str
    digest: str
    workspace: str
    capabilities: dict[str, dict[str, Any]]
    granted_at: float

    def allows(self, name: str) -> bool:
        return name in self.capabilities

    def scope(self, name: str) -> dict[str, Any]:
        return self.capabilities.get(name, {})


def workspace_key(workspace: Path) -> str:
    return hashlib.sha256(str(Path(workspace).resolve()).encode("utf-8")).hexdigest()[:20]


class GrantStore:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or grants_path()
        self._boundary = (path.parent if path is not None else config_dir())

    def get(self, skill_id: str, digest: str, workspace: Path) -> Grant | None:
        record = self._records().get(skill_id, {}).get(digest, {}).get(workspace_key(workspace))
        if not isinstance(record, dict) or not isinstance(record.get("capabilities"), dict):
            return None
        return Grant(skill_id, digest, str(workspace), record["capabilities"], float(record.get("granted_at", 0)))

    def put(
        self, skill_id: str, digest: str, workspace: Path, capabilities: dict[str, dict[str, Any]]
    ) -> Grant:
        now = time.time()
        key = workspace_key(workspace)

        def update(document: dict[str, Any]) -> dict[str, Any]:
            grants = document.setdefault("grants", {})
            grants.setdefault(skill_id, {}).setdefault(digest, {})[key] = {
                "workspace": str(Path(workspace).resolve()),
                "capabilities": capabilities,
                "granted_at": now,
                "decided_by": "user",
            }
            document["format"] = GRANTS_FORMAT
            return document

        self._update(update)
        return Grant(skill_id, digest, str(workspace), capabilities, now)

    def revoke(self, skill_id: str, workspace: Path | None = None) -> int:
        """Remove grants of a skill (in one workspace, or everywhere); returns how many."""
        removed = 0

        def update(document: dict[str, Any]) -> dict[str, Any]:
            nonlocal removed
            by_digest = document.get("grants", {}).get(skill_id, {})
            for digest in list(by_digest):
                if workspace is None:
                    removed += len(by_digest[digest])
                    del by_digest[digest]
                    continue
                if by_digest[digest].pop(workspace_key(workspace), None) is not None:
                    removed += 1
                if not by_digest[digest]:
                    del by_digest[digest]
            if not by_digest:
                document.get("grants", {}).pop(skill_id, None)
            return document

        self._update(update)
        return removed

    def granted_digests(self, skill_id: str) -> list[str]:
        return sorted(self._records().get(skill_id, {}))

    def _records(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        document = read_json_object(self.path, boundary=self._boundary, empty_as_missing=True)
        grants = document.get("grants")
        return grants if isinstance(grants, dict) else {}

    def _update(self, update) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        update_json_object(self.path, update, boundary=self._boundary, empty_as_missing=True)
