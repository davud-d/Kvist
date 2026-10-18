# -*- coding: utf-8 -*-
"""Spots the wake word at the start of a recognized phrase."""
from __future__ import annotations

import re
from difflib import SequenceMatcher

from config import WAKE_MATCH_RATIO, WAKE_WORDS

_FIRST_WORD = re.compile(r"\W*(\w+)\W*(.*)", re.DOTALL)


def strip_wake_word(text: str) -> str | None:
    """Return the text after a leading wake word, or None when the phrase does not start with one.

    An empty string means the phrase was the wake word alone.
    """
    match = _FIRST_WORD.match(text)
    if match is None:
        return None
    first = match.group(1).casefold()
    if any(SequenceMatcher(None, first, word).ratio() >= WAKE_MATCH_RATIO for word in WAKE_WORDS):
        return match.group(2).strip()
    return None
