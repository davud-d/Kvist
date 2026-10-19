# -*- coding: utf-8 -*-
"""Long-term memory: records that outlive a session, with their sources, links and history.

``store`` holds one SQLite database and every rule about who may change what,
``privacy`` refuses secrets and personal data before anything is stored,
``retrieval`` picks the few records a task's prompt gets, and ``service`` is
what the model's tool, ``ctx.memory`` and the person call.
"""
from __future__ import annotations

from kernel.memory.errors import (
    MemoryConflictError,
    MemoryNotFound,
    MemoryPermissionError,
    MemoryPrivacyError,
    MemoryStoreError,
    MemoryValidationError,
)
from kernel.memory.service import MemoryService
from kernel.memory.store import MemoryStore, Record, Source

__all__ = [
    "MemoryConflictError",
    "MemoryNotFound",
    "MemoryPermissionError",
    "MemoryPrivacyError",
    "MemoryService",
    "MemoryStore",
    "MemoryStoreError",
    "MemoryValidationError",
    "Record",
    "Source",
]
