# -*- coding: utf-8 -*-
"""Model keys at rest (Fernet) and the scrubbing that keeps them out of logs and events.

The master key comes from KVIST_CLOUD_MASTER_KEY only. Several comma-separated keys rotate:
the first encrypts, all of them decrypt.
"""
from __future__ import annotations

import logging
import re
from collections import OrderedDict
from collections.abc import Iterable

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

REDACTED = "[redacted]"
# Shorter strings are too likely to occur in normal text to be replaced blindly.
MIN_SECRET_LENGTH = 8
# Well-known key shapes, scrubbed even when nobody registered them.
_KEY_PATTERNS = re.compile(
    r"(sk-ant-[A-Za-z0-9_\-]{8,}|sk-[A-Za-z0-9_\-]{16,}|gh[opsu]_[A-Za-z0-9]{20,}|"
    r"(?i:bearer)\s+[A-Za-z0-9._\-]{16,})"
)
_LIVE_LIMIT = 4096


class SecretError(ValueError):
    pass


class Vault:
    def __init__(self, master_key: str) -> None:
        keys = [part.strip() for part in master_key.split(",") if part.strip()]
        if not keys:
            raise SecretError("no master key")
        try:
            self._fernet = MultiFernet([Fernet(key.encode()) for key in keys])
        except ValueError:
            raise SecretError("KVIST_CLOUD_MASTER_KEY is not a valid Fernet key") from None

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            raise SecretError("nothing to encrypt")
        return self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")

    def decrypt(self, token: str) -> str:
        try:
            plaintext = self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            raise SecretError("stored secret cannot be decrypted with the current master key") from None
        remember(plaintext)
        return plaintext


def last4(secret: str) -> str:
    return secret[-4:] if len(secret) >= 4 else ""


# Plaintext secrets this process has seen recently; the log scrubber replaces them.
_live: OrderedDict[str, None] = OrderedDict()


def remember(secret: str) -> None:
    if len(secret) < MIN_SECRET_LENGTH:
        return
    _live[secret] = None
    _live.move_to_end(secret)
    while len(_live) > _LIVE_LIMIT:
        _live.popitem(last=False)


def redact(text: str, extra: Iterable[str] = ()) -> str:
    for secret in [*extra, *_live]:
        if len(secret) >= MIN_SECRET_LENGTH and secret in text:
            text = text.replace(secret, REDACTED)
    return _KEY_PATTERNS.sub(REDACTED, text)


def redact_obj(value: object, extra: Iterable[str] = ()) -> object:
    """The same, through a JSON-shaped value (an event)."""
    extra = list(extra)
    if isinstance(value, str):
        return redact(value, extra)
    if isinstance(value, list):
        return [redact_obj(item, extra) for item in value]
    if isinstance(value, dict):
        return {key: redact_obj(item, extra) for key, item in value.items()}
    return value


_previous_factory = None


def install_log_scrubber(static: Iterable[str] = ()) -> None:
    """Scrub every log record at creation: message, arguments and traceback text.

    A filter on one logger misses records propagated from others; the record factory sees all.
    The traceback is formatted here and the exception object dropped, so no handler can print
    the raw exception message later.
    """
    global _previous_factory
    for secret in static:
        remember(secret)
    if _previous_factory is not None:
        return
    _previous_factory = logging.getLogRecordFactory()
    formatter = logging.Formatter()

    def factory(*args, **kwargs) -> logging.LogRecord:
        record = _previous_factory(*args, **kwargs)
        try:
            message = record.getMessage()
        except (TypeError, ValueError):
            message = str(record.msg)
        record.msg = redact(message)
        record.args = None
        if record.exc_info:
            record.exc_text = redact(formatter.formatException(record.exc_info))
            record.exc_info = None
        return record

    logging.setLogRecordFactory(factory)
