# -*- coding: utf-8 -*-
"""Terminal window title ("Kvist | session name"), set with the OSC 2 sequence.

i3 and Hyprland read the window title the terminal publishes, so without this
every Kvist window is named after the shell or the working directory.
"""
from __future__ import annotations

import sys

_PRODUCT = "Kvist"
_MAX_TITLE_CHARS = 80

_last_written: str | None = None


def format_window_title(session_title: str | None) -> str:
    """``Kvist | <title>``, or plain ``Kvist`` while the session has no title.

    The title comes from a model, so control characters (an ESC or BEL would
    end the sequence early and let the rest run as terminal input) are dropped.
    """
    clean = "".join(ch for ch in (session_title or "") if ch.isprintable()).strip()
    if not clean:
        return _PRODUCT
    return f"{_PRODUCT} | {clean[:_MAX_TITLE_CHARS]}"


def _write(sequence: str) -> None:
    if not sys.__stdout__ or not sys.__stdout__.isatty():
        return
    try:
        sys.__stdout__.write(sequence)
        sys.__stdout__.flush()
    except (OSError, ValueError):
        # A closed or broken terminal has no title to set; the title is cosmetic.
        return


def set_window_title(session_title: str | None) -> None:
    """Publish the window title; repeated calls with the same title write nothing."""
    global _last_written
    title = format_window_title(session_title)
    if title == _last_written:
        return
    _last_written = title
    _write(f"\x1b]2;{title}\x07")


def push_window_title() -> None:
    """Remember the terminal's own title so ``pop_window_title`` can bring it back."""
    _write("\x1b[22;2t")


def pop_window_title() -> None:
    global _last_written
    _last_written = None
    _write("\x1b[23;2t")
