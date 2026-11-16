# -*- coding: utf-8 -*-
"""Where the part of a system prompt that every run shares ends.

A provider reuses its prompt cache only for the longest identical prefix. Runs
of one task that differ in working style therefore keep everything they share
first and put the style, the one thing that differs, after this marker.
"""
from __future__ import annotations

# Third level: the style stays inside the "## RESPONSE STYLE" section, which the role
# prompts' injection guard refers to by that name.
SELECTED_STYLE_MARKER = "### SELECTED STYLE"


def split_style_tail(system: str) -> tuple[str, str]:
    """Split a system prompt into the part shared between styles and the style tail.

    The tail starts at the marker line and runs to the end, so it also carries
    what follows the style (the voice-mode section). A prompt without the
    marker is all shared. The last marker line wins: the workspace instruction
    file, quoted earlier in the prompt, can then never move the boundary.
    """
    start = 0
    boundary = -1
    while True:
        found = system.find(SELECTED_STYLE_MARKER, start)
        if found < 0:
            break
        if (found == 0 or system[found - 1] == "\n") and _ends_line(system, found + len(SELECTED_STYLE_MARKER)):
            boundary = found
        start = found + 1
    if boundary < 0:
        return system, ""
    return system[:boundary], system[boundary:]


def _ends_line(text: str, index: int) -> bool:
    return index == len(text) or text[index] == "\n"
