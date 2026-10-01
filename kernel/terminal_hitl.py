# -*- coding: utf-8 -*-
"""Human-in-the-Loop prompt state: the process-wide active flag and the alternate-screen guard."""
from __future__ import annotations

import sys
import threading
from contextlib import contextmanager
from typing import Any, Iterator


# ── Global HITL lock ─────────────────────────────────────────────
# Prevents background TUI rendering while the user is typing y/N.
_hitl_lock = threading.Lock()
_hitl_active = False


def is_hitl_active() -> bool:
    """True if a Human-in-the-Loop prompt is currently waiting for input."""
    with _hitl_lock:
        return _hitl_active


def _enter_hitl() -> None:
    global _hitl_active
    with _hitl_lock:
        _hitl_active = True


def _exit_hitl() -> None:
    global _hitl_active
    with _hitl_lock:
        _hitl_active = False


@contextmanager
def _alt_screen(console: Any) -> Iterator[None]:
    """Draw an approval prompt inside the terminal's alternate screen buffer.

    Entering the alternate buffer preserves the current screen; leaving it
    restores that screen exactly, so the prompt has hard boundaries and can
    never erase output above it. Outside a TTY it is a no-op.
    """
    use_alt = sys.stdin.isatty() and console.is_terminal
    if use_alt:
        console.set_alt_screen(True)
    try:
        yield
    finally:
        if use_alt:
            console.set_alt_screen(False)
