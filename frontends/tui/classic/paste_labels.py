# -*- coding: utf-8 -*-
"""Short labels that stand for large pasted text inside the input line."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

PASTE_MIN_LINES = 4
PASTE_MIN_CHARS = 800

_LABEL = re.compile(r"\[Pasted text #(\d+) (?:\+\d+ lines|\d+ chars)\]")
_LABEL_AT_END = re.compile(r"\[Pasted text #\d+ (?:\+\d+ lines|\d+ chars)\]$")


def normalize_newlines(text: str) -> str:
    return text.replace("\r\n", "\n").replace("\r", "\n")


def is_large_paste(text: str) -> bool:
    body = normalize_newlines(text)
    return len(body) >= PASTE_MIN_CHARS or body.count("\n") + 1 >= PASTE_MIN_LINES


def label_length_before(line: str, cursor: int) -> int:
    """Length of the label that ends exactly at ``cursor``, else 0."""
    match = _LABEL_AT_END.search(line[:cursor])
    return len(match.group(0)) if match else 0


@dataclass(slots=True)
class PasteStore:
    """Pasted texts of one screen, kept so a label recalled from history still expands."""

    next_id: int = 1
    texts: dict[int, str] = field(default_factory=dict)

    def collapse(self, text: str) -> str:
        body = normalize_newlines(text)
        paste_id = self.next_id
        self.next_id += 1
        self.texts[paste_id] = body
        extra_lines = body.count("\n")
        if extra_lines:
            return f"[Pasted text #{paste_id} +{extra_lines} lines]"
        return f"[Pasted text #{paste_id} {len(body)} chars]"

    def find_repeated_label(self, line: str, pasted: str) -> tuple[int, int] | None:
        """Range of the label in ``line`` that already stands for ``pasted``.

        Pasting the same text again asks to see it instead of a second label.
        """
        body = normalize_newlines(pasted)
        for match in _LABEL.finditer(line):
            if self.texts.get(int(match.group(1))) == body:
                return match.start(), match.end()
        return None

    def expand(self, line: str) -> str:
        """Put stored text back; a label with no stored text stays as written."""
        return _LABEL.sub(
            lambda match: self.texts.get(int(match.group(1)), match.group(0)), line
        )

