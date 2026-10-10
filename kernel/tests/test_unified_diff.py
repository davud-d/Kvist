# -*- coding: utf-8 -*-
from __future__ import annotations

from io import StringIO

from rich.console import Console

from kernel.unified_diff import confirmation_renderable, old_and_new_text, pair_rows, parse_unified_diff

DIFF = "--- a/x.py\n+++ b/x.py\n@@ -2,3 +2,3 @@\n import os\n-DEBUG = False\n+DEBUG = True\n PORT = 1"


def test_parse_numbers_rows_from_hunk_header_and_skips_file_names():
    rows = parse_unified_diff(DIFF)
    assert [(r.kind, r.text, r.old_no, r.new_no) for r in rows] == [
        ("ctx", "import os", 2, 2),
        ("del", "DEBUG = False", 3, None),
        ("add", "DEBUG = True", None, 3),
        ("ctx", "PORT = 1", 4, 4),
    ]


def test_pairing_puts_a_removal_opposite_its_addition():
    pairs = pair_rows(parse_unified_diff(DIFF))
    left, right = pairs[1]
    assert (left.text, right.text) == ("DEBUG = False", "DEBUG = True")


def test_old_and_new_text_split_the_sides():
    assert old_and_new_text(parse_unified_diff(DIFF)) == (
        "import os\nDEBUG = False\nPORT = 1",
        "import os\nDEBUG = True\nPORT = 1",
    )


def _render(body: str, columns: int) -> str:
    console = Console(file=StringIO(), width=columns, force_terminal=False)
    console.print(confirmation_renderable(body, columns))
    return console.file.getvalue()


def test_confirmation_shows_removed_and_added_not_raw_diff():
    out = _render("The agent wants to overwrite x.py:\n\n" + DIFF, 120)
    assert "DEBUG = False" in out and "DEBUG = True" in out
    assert "@@" not in out and "+++" not in out
    same_row = next(line for line in out.splitlines() if "DEBUG = False" in line)
    assert "DEBUG = True" in same_row


def test_body_without_hunks_stays_plain_text():
    assert "just a question" in _render("just a question", 120)
