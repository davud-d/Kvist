# -*- coding: utf-8 -*-
"""Memory errors: each says what went wrong in words the model and the person can act on.

A message never quotes the refused content, so an error cannot carry a secret
into a log or a prompt.
"""
from __future__ import annotations


class MemoryStoreError(ValueError):
    code = "memory_error"
    retryable = False

    def public(self) -> dict[str, object]:
        return {"code": self.code, "message": str(self), "retryable": self.retryable}


class MemoryPermissionError(MemoryStoreError):
    code = "permission"


class MemoryPrivacyError(MemoryStoreError):
    code = "privacy"


class MemoryValidationError(MemoryStoreError):
    code = "validation"


class MemoryConflictError(MemoryStoreError):
    code = "conflict"
    retryable = True


class MemoryNotFound(MemoryStoreError):
    code = "not_found"


class MemoryQuotaExceeded(MemoryStoreError):
    code = "quota"


class MemoryStorageError(MemoryStoreError):
    code = "storage"


class MemoryProviderError(MemoryStoreError):
    code = "provider"
    retryable = True


class MemoryBusyError(MemoryStorageError):
    code = "busy"
    retryable = True
