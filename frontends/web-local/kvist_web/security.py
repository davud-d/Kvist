"""Browser session, origin checks, secret storage and log redaction."""
from __future__ import annotations

import hashlib
import hmac
import re
import secrets
import threading
import time
from dataclasses import dataclass
from typing import Any


_SECRET_RE = re.compile(
    r"(?i)(authorization\s*:\s*bearer\s+|api[_-]?key\s*[=:]\s*|password\s*[=:]\s*)\S+"
    r"|\bsk-(?:proj-)?[A-Za-z0-9_-]{12,}\b"
)


def redact(value: Any) -> str:
    def replacement(match: re.Match) -> str:
        prefix = match.group(1) or ""
        return prefix + "[REDACTED]"

    return _SECRET_RE.sub(replacement, str(value))[-12_000:]


@dataclass(slots=True)
class BrowserSession:
    cookie: str
    csrf: str
    created_at: float


class BrowserAuth:
    def __init__(self) -> None:
        self._sessions: dict[str, BrowserSession] = {}
        self._lock = threading.Lock()

    def create(self) -> BrowserSession:
        session = BrowserSession(
            cookie=secrets.token_urlsafe(32),
            csrf=secrets.token_urlsafe(24),
            created_at=time.time(),
        )
        with self._lock:
            self._sessions[session.cookie] = session
            self._prune()
        return session

    def resolve(self, cookie: str | None) -> BrowserSession | None:
        if not cookie:
            return None
        with self._lock:
            return self._sessions.get(cookie)

    def verify_csrf(self, session: BrowserSession, supplied: str | None) -> bool:
        return bool(supplied and hmac.compare_digest(session.csrf, supplied))

    def _prune(self) -> None:
        cutoff = time.time() - 7 * 24 * 60 * 60
        for token, session in list(self._sessions.items()):
            if session.created_at < cutoff:
                self._sessions.pop(token, None)


class SecretStore:
    """Keep SSH passwords out of config; use OS keyring with memory fallback."""

    SERVICE = "kvist-web"

    def __init__(self) -> None:
        self._memory: dict[str, str] = {}
        try:
            import keyring  # type: ignore
        except ImportError:
            keyring = None
        self._keyring = keyring

    def set(self, key: str, value: str, *, persist: bool = True) -> bool:
        self._memory[key] = value
        if not persist or self._keyring is None:
            return False
        try:
            self._keyring.set_password(self.SERVICE, key, value)
        except Exception:
            return False
        return True

    def get(self, key: str) -> str | None:
        value = self._memory.get(key)
        if value is not None:
            return value
        if self._keyring is None:
            return None
        try:
            return self._keyring.get_password(self.SERVICE, key)
        except Exception:
            return None

    def delete(self, key: str) -> None:
        self._memory.pop(key, None)
        if self._keyring is not None:
            try:
                self._keyring.delete_password(self.SERVICE, key)
            except Exception:
                pass


def fingerprint_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()[:12]
