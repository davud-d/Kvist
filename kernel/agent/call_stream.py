# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import re
from dataclasses import dataclass

# The text a call carries is what the person wants to watch arrive; paths, counts
# and flags are already in the call's card.
STREAMED_FIELDS: dict[str, tuple[str, ...]] = {
    "write_file": ("content",),
    "append_file": ("content",),
    "replace_in_file": ("old_text", "new_text"),
}

# Group 1 is the string so far, group 2 is the closing quote once it has arrived.
_FIELD_RES = {
    field: re.compile(rf'"{field}"\s*:\s*"((?:[^"\\]|\\.)*)("?)')
    for fields in STREAMED_FIELDS.values()
    for field in fields
}
# A chunk can end inside \uXXXX, so up to five characters may not decode yet.
_UNDECODED_TAIL = 6


@dataclass(slots=True)
class StreamedPiece:
    field: str
    first_line: int
    text: str
    done: bool


@dataclass(slots=True)
class _Delivered:
    chars: int = 0
    lines: int = 0
    done: bool = False


def decode_json_prefix(raw: str) -> str:
    """Decode the body of a JSON string that may end mid-escape."""
    for cut in range(len(raw), max(len(raw) - _UNDECODED_TAIL, -1), -1):
        try:
            return json.loads(f'"{raw[:cut]}"')
        except json.JSONDecodeError:
            continue
    return ""


class CallStream:
    """Turns the growing argument text of streamed calls into whole new lines.

    A half-written line is held back: a frontend that draws line by line would
    otherwise redraw the same line on every fragment.
    """

    def __init__(self) -> None:
        self._delivered: dict[tuple[str, str], _Delivered] = {}

    def pieces(self, call_id: str, tool: str, arguments: str, *, final: bool = False) -> list[StreamedPiece]:
        found: list[StreamedPiece] = []
        for field in STREAMED_FIELDS.get(tool, ()):
            piece = self._piece(call_id, field, arguments, final)
            if piece is not None:
                found.append(piece)
        return found

    def _piece(self, call_id: str, field: str, arguments: str, final: bool) -> StreamedPiece | None:
        match = _FIELD_RES[field].search(arguments)
        if match is None:
            return None
        state = self._delivered.setdefault((call_id, field), _Delivered())
        if state.done:
            return None
        text = decode_json_prefix(match.group(1))
        done = bool(match.group(2)) or final
        if not done:
            text = text[: text.rfind("\n") + 1]
        fresh = text[state.chars :]
        if not fresh and not done:
            return None
        piece = StreamedPiece(field, state.lines, fresh, done)
        state.chars = len(text)
        state.lines += fresh.count("\n")
        state.done = done
        return piece
