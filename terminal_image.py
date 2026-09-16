# -*- coding: utf-8 -*-
"""Inline image output for terminals that can draw pictures.

Only two protocols are spoken: kitty's graphics protocol (kitty, Ghostty,
Konsole) and iTerm2's inline images (iTerm2, WezTerm). Both accept a PNG as
is, so no imaging library is needed at runtime. Capability is decided from
the environment alone: querying the terminal would mean reading its reply
from stdin while the shell is about to take the terminal back.
"""
from __future__ import annotations

import base64
import os
import sys
from pathlib import Path
from typing import Mapping, TextIO

EXIT_LOGO_PATH = Path(__file__).resolve().parent / "assets" / "exit_logo.png"
EXIT_LOGO_ROWS = 8

_KITTY_CHUNK = 4096
_KITTY_TERM_PROGRAMS = frozenset({"ghostty"})
_ITERM_TERM_PROGRAMS = frozenset({"iterm.app", "wezterm"})


def image_protocol(env: Mapping[str, str]) -> str | None:
    """Name the image protocol the terminal speaks, or ``None``."""
    term = env.get("TERM", "").lower()
    # A multiplexer swallows graphics escapes unless passthrough is set up,
    # and TERM_PROGRAM still names the outer terminal inside it.
    if env.get("TMUX") or term.startswith(("screen", "tmux")):
        return None
    program = env.get("TERM_PROGRAM", "").lower()
    if term == "xterm-kitty" or env.get("KITTY_WINDOW_ID"):
        return "kitty"
    if program in _KITTY_TERM_PROGRAMS or env.get("GHOSTTY_RESOURCES_DIR"):
        return "kitty"
    if env.get("KONSOLE_VERSION"):
        return "kitty"
    if program in _ITERM_TERM_PROGRAMS:
        return "iterm"
    return None


def kitty_image_sequence(png: bytes, rows: int) -> str:
    payload = base64.standard_b64encode(png).decode("ascii")
    chunks = [
        payload[start:start + _KITTY_CHUNK]
        for start in range(0, len(payload), _KITTY_CHUNK)
    ]
    parts = []
    for index, chunk in enumerate(chunks):
        more = 1 if index < len(chunks) - 1 else 0
        # Only the first chunk carries the control keys; q=2 keeps the
        # terminal from answering into the shell's input.
        keys = f"f=100,a=T,r={rows},q=2,m={more}" if index == 0 else f"m={more}"
        parts.append(f"\x1b_G{keys};{chunk}\x1b\\")
    return "".join(parts)


def iterm_image_sequence(png: bytes, rows: int) -> str:
    payload = base64.standard_b64encode(png).decode("ascii")
    return (
        f"\x1b]1337;File=inline=1;size={len(png)};height={rows};"
        f"preserveAspectRatio=1:{payload}\x07"
    )


_SEQUENCE_BUILDERS = {
    "kitty": kitty_image_sequence,
    "iterm": iterm_image_sequence,
}


def print_exit_logo(
    stream: TextIO | None = None,
    env: Mapping[str, str] = os.environ,
) -> bool:
    """Draw the exit logo when the terminal can; report whether it did."""
    # Resolved per call: the session swaps sys.stdout while its screen is up.
    stream = stream or sys.stdout
    if not stream.isatty():
        return False
    protocol = image_protocol(env)
    if protocol is None:
        return False
    try:
        png = EXIT_LOGO_PATH.read_bytes()
    except OSError:
        return False
    stream.write(_SEQUENCE_BUILDERS[protocol](png, EXIT_LOGO_ROWS) + "\n")
    stream.flush()
    return True
