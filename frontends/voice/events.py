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


def emit_phrase(on: bool) -> None:
    """Say that somebody in the room began to speak, or that the phrase is over."""
    emit(event="phrase", on=on)


def emit_level(value: float, pause: float) -> None:
    """Report a block of the phrase being said: its loudness, and how much of the pause that ends the phrase has passed."""
    emit(event="level", value=round(value, 3), pause=round(pause, 3))


def emit_missed() -> None:
    """Admit that words meant for Kvist could not be recognized, so the person repeats them instead of waiting."""
    emit(event="missed")
