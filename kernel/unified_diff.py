# -*- coding: utf-8 -*-
"""Unified-diff text from the overwrite approval gate, as rows for display.

``SafeExecutor._confirm_overwrite`` hands frontends a ``difflib`` unified diff.
Frontends show removed lines opposite added lines; this module turns the text
into rows and pairs them. The Ink app has the same parser in
``frontends/tui/ink/diff.ts``.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

_HUNK = re.compile(r"^@@ -(\d+)(?:,\d+)? \+(\d+)(?:,\d+)? @@")


@dataclass(slots=True)
class DiffRow:
    kind: str  # "ctx" | "del" | "add"
    text: str
    old_no: int | None
    new_no: int | None


def parse_unified_diff(text: str) -> list[DiffRow]:
    """Rows of the hunks in ``text``; file-naming lines before the first hunk are dropped."""
    rows: list[DiffRow] = []
    old_no = new_no = 0
    in_hunk = False
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        header = _HUNK.match(line)
        if header:
            old_no, new_no = int(header.group(1)), int(header.group(2))
            in_hunk = True
        elif not in_hunk:
            continue
        elif line.startswith("+"):
            rows.append(DiffRow("add", line[1:], None, new_no))
            new_no += 1
        elif line.startswith("-"):
            rows.append(DiffRow("del", line[1:], old_no, None))
            old_no += 1
        elif line.startswith(" "):
            rows.append(DiffRow("ctx", line[1:], old_no, new_no))
            old_no += 1
            new_no += 1
    return rows


def pair_rows(rows: list[DiffRow]) -> list[tuple[DiffRow | None, DiffRow | None]]:
    """Side-by-side pairs: a run of removals sits opposite the run of additions after it."""
    pairs: list[tuple[DiffRow | None, DiffRow | None]] = []
    index = 0
    while index < len(rows):
        row = rows[index]
        if row.kind == "ctx":
            pairs.append((row, row))
            index += 1
            continue
        removed: list[DiffRow] = []
        added: list[DiffRow] = []
        while index < len(rows) and rows[index].kind != "ctx":
            (removed if rows[index].kind == "del" else added).append(rows[index])
            index += 1
        for offset in range(max(len(removed), len(added))):
            pairs.append(
                (
                    removed[offset] if offset < len(removed) else None,
                    added[offset] if offset < len(added) else None,
                )
            )
    return pairs


def old_and_new_text(rows: list[DiffRow]) -> tuple[str, str]:
    """The old and the new side of the shown hunks, one line per row."""
    old = [row.text for row in rows if row.kind != "add"]
    new = [row.text for row in rows if row.kind != "del"]
    return "\n".join(old), "\n".join(new)


MAX_ROWS = 30
SPLIT_MIN_COLUMNS = 100


def confirmation_renderable(body: str, columns: int):
    """Rich renderable for an approval body: the diff as removed/added columns, else plain text.

    ``body`` is "<intro>\\n\\n<unified diff>" as built by ``_confirm_overwrite``;
    a body without hunks is shown as it is.
    """
    from rich.console import Group
    from rich.table import Table
    from rich.text import Text

    from kernel.redaction import sanitize_display_text

    intro, _, diff = body.partition("\n\n")
    rows = parse_unified_diff(diff)
    if not rows:
        return Text(sanitize_display_text(body))

    number_width = len(str(max([row.old_no or 0 for row in rows] + [row.new_no or 0 for row in rows])))
    split = columns >= SPLIT_MIN_COLUMNS

    def cell(row: DiffRow | None, number: int | None) -> tuple[Text, Text]:
        if row is None:
            return Text(""), Text("")
        style = {"del": "red", "add": "green", "ctx": "dim"}[row.kind]
        sign = {"del": "- ", "add": "+ ", "ctx": "  "}[row.kind]
        return (
            Text("" if number is None else str(number), style="dim", justify="right"),
            Text(sign + sanitize_display_text(row.text), style=style, no_wrap=True, overflow="ellipsis"),
        )

    table = Table.grid(padding=(0, 1), expand=True)
    if split:
        pairs = pair_rows(rows)
        table.add_column(width=number_width)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        table.add_column(width=number_width)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        for left, right in pairs[:MAX_ROWS]:
            table.add_row(*cell(left, left.old_no if left else None), *cell(right, right.new_no if right else None))
        hidden = len(pairs) - MAX_ROWS
    else:
        table.add_column(width=number_width)
        table.add_column(ratio=1, no_wrap=True, overflow="ellipsis")
        for row in rows[:MAX_ROWS]:
            table.add_row(*cell(row, row.new_no if row.kind != "del" else row.old_no))
        hidden = len(rows) - MAX_ROWS
    parts: list = [Text(sanitize_display_text(intro)), Text(""), table]
    if hidden > 0:
        parts.append(Text(f"… {hidden} more lines", style="dim"))
    return Group(*parts)
