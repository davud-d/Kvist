# -*- coding: utf-8 -*-
"""Memory as its three users reach it: the model's ``memory`` tool, ``ctx.memory`` and the person.

The same operations and the same rules for all three; only the actor
differs, and the actor is set by the caller's code, never by an argument a
model or a skill passes. A record id names its store (``mem_`` this
workspace, ``memu_`` the person's preferences for every project), so no
argument can point an operation at another project.
"""
from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

from kernel.memory.errors import MemoryNotFound, MemoryPermissionError, MemoryValidationError
from kernel.memory.retrieval import relevant, render_block
from kernel.memory.schema import KINDS, STATUSES
from kernel.memory.store import PERSON, USER_PREFIX, WORKSPACE_PREFIX, MemoryStore, Source
from kernel.user_config import get_memory_paused, get_memory_processing_allowed

SCOPES = ("workspace", "user")


class MemoryService:
    def __init__(self, workspace: Path, *, with_user_scope: bool) -> None:
        self.workspace = MemoryStore.for_workspace(workspace)
        # Skills see only their workspace: the person's preferences are about the person.
        self.user = MemoryStore.for_user() if with_user_scope else None

    def remember(self, content: str, *, kind: str, trust: str, actor: str, scope: str = "workspace",
                 topic: str = "", files: list[str] | None = None, sources: list[Source] | None = None) -> dict[str, Any]:
        self._check_write(actor)
        outcome, record = self._scope(scope).remember(content, kind=kind, trust=trust, actor=actor, topic=topic,
                                                      files=files, sources=sources)
        return {"result": outcome, "record": record.public()}

    def search(self, query: str, *, kinds: tuple[str, ...] = KINDS, include_inactive: bool = False,
               limit: int = 10) -> list[dict[str, Any]]:
        statuses = STATUSES if include_inactive else ("active", "stale", "conflicted")
        hits = []
        for store in self._stores():
            hits.extend(store.search(query, statuses=statuses, kinds=kinds, limit=limit))
        hits.sort(key=lambda item: (item[0], item[1].updated_at), reverse=True)
        return [{"relevance_score": round(score, 3), **record.public()} for score, record in hits[:limit]]

    def get(self, record_id: str) -> dict[str, Any]:
        return self._store_of(record_id).get(record_id).public()

    def update(self, record_id: str, *, actor: str, expected_version: int, reason: str, **changes: Any) -> dict[str, Any]:
        self._check_write(actor)
        return self._store_of(record_id).update(record_id, actor=actor, expected_version=expected_version,
                                                reason=reason, **changes).public()

    def forget(self, record_id: str, *, actor: str) -> dict[str, Any]:
        self._check_write(actor)
        return self._store_of(record_id).forget(record_id, actor=actor)

    def link(self, source_id: str, target_id: str, relation: str, *, actor: str, reason: str = "") -> dict[str, Any]:
        self._check_write(actor)
        store = self._store_of(source_id)
        if self._store_of(target_id) is not store:
            raise MemoryValidationError("a project record and a preference for every project cannot be linked")
        return store.link(source_id, target_id, relation, actor=actor, reason=reason)

    def explain(self, record_id: str) -> dict[str, Any]:
        return self._store_of(record_id).explain(record_id)

    def status(self) -> dict[str, Any]:
        stores = {"workspace": self.workspace.counts()}
        if self.user is not None:
            stores["user"] = self.user.counts()
        return {"paused": get_memory_paused(), "processing_allowed": get_memory_processing_allowed(),
                "stores": stores}

    def prompt_block(self, text: str, count_tokens: Callable[[str], int]) -> str:
        return render_block(relevant(self._stores(), text, count_tokens))

    def _stores(self) -> list[MemoryStore]:
        return [self.workspace] + ([self.user] if self.user is not None else [])

    def _scope(self, scope: str) -> MemoryStore:
        if scope not in SCOPES:
            raise MemoryValidationError(f"scope must be one of {', '.join(SCOPES)}")
        if scope == "user":
            if self.user is None:
                raise MemoryPermissionError("this caller works with its workspace's memory only")
            return self.user
        return self.workspace

    def _store_of(self, record_id: str) -> MemoryStore:
        record_id = str(record_id)
        if record_id.startswith(USER_PREFIX) and self.user is not None:
            return self.user
        if record_id.startswith(WORKSPACE_PREFIX):
            return self.workspace
        # The same answer whether the record exists elsewhere or nowhere: an error is not a probe.
        raise MemoryNotFound(f"no record {record_id} in this memory")

    def _check_write(self, actor: str) -> None:
        if actor != PERSON and get_memory_paused():
            raise MemoryPermissionError("the person paused memory: nothing is written until they resume it "
                                        "(/memory resume)")
