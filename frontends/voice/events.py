# -*- coding: utf-8 -*-
"""JSON-lines events on stdout: the only channel to the process that launched the voice input.

Logging goes to stderr, so stdout carries nothing but these events.
"""
from __future__ import annotations

import json
import sys
import threading

_write_lock = threading.Lock()


def emit(**fields: object) -> None:
    """Write one event object as a line."""
    line = json.dumps(fields, ensure_ascii=False)
    with _write_lock:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def emit_state(state: str) -> None:
    """Announce `loading`, `idle`, `listening` or `transcribing`."""
    emit(event="state", state=state)


def emit_text(text: str) -> None:
    """Deliver one recognized phrase."""
    emit(event="text", text=text)


def emit_heard(text: str) -> None:
    """Report a phrase that was not addressed to Kvist, so the screen can show what the room sounds like."""
    emit(event="heard", text=text)
