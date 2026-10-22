# -*- coding: utf-8 -*-
"""The bundled design skill's recipes run as written: the model copies them, so a broken one fails silently."""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import kernel
from kernel.sandbox.terminal_interpreters import _interpreter_target
from kernel.sandbox.terminal_limits import _MAX_COMMAND_CHARS

PACKAGE = Path(kernel.__file__).parent / "skills_bundled" / "design"
requires_pty = pytest.mark.skipif(not hasattr(os, "fork") or sys.platform == "win32", reason="needs a POSIX pty")


def _code_block(page: str) -> str:
    text = (PACKAGE / "references" / page).read_text(encoding="utf-8")
    blocks = re.findall(r"```python\n(.*?)```", text, re.S)
    assert len(blocks) == 1, f"{page} should hold exactly one python block"
    return blocks[0]


def test_the_contrast_script_is_one_inline_argument_kvist_accepts():
    code = _code_block("visual.md")
    command = ["python3", "-c", code]

    assert _interpreter_target(command, "python3") == ("inline", code)
    assert sum(len(item) for item in command) + len(command) - 1 <= _MAX_COMMAND_CHARS


def test_the_contrast_script_gives_the_reference_values_it_quotes():
    code = _code_block("visual.md")
    pairs = 'P = [("#767676", "#fff"), ("#888", "#fff"), ("#fff", "#888"), ("oklch(0.628 0.2577 29.23)", "#ff0000")]'
    code = re.sub(r"^P = .*$", pairs, code, count=1, flags=re.M)

    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, check=True).stdout

    lines = out.splitlines()
    assert "WCAG 4.54:1" in lines[0] and "text pass" in lines[0]
    assert "WCAG 3.54:1" in lines[1] and "text FAIL" in lines[1] and "Lc 63.1" in lines[1]
    assert "Lc -68.5" in lines[2]
    assert "WCAG 1.00:1" in lines[3]


@requires_pty
def test_the_terminal_harness_shows_the_screen_and_the_modes_a_program_leaves_on(tmp_path):
    harness = tmp_path / "tui_snapshot.py"
    harness.write_text(_code_block("terminal.md"), encoding="utf-8")
    program = tmp_path / "app.py"
    program.write_text(
        "import os, sys, tty\n"
        "tty.setraw(0)\n"
        "sys.stdout.write('\\x1b[?1049h\\x1b[2J\\x1b[3;5H\\x1b[7mSelected\\x1b[0m \\u4f60\\u597d')\n"
        "sys.stdout.write('\\x1b[6n'); sys.stdout.flush()\n"
        "answer = os.read(0, 16).decode()\n"
        "sys.stdout.write('\\x1b[5;1Hcursor at ' + answer[2:-1]); sys.stdout.flush()\n",
        encoding="utf-8",
    )
    spec = f'{{"command": ["{sys.executable}", "-I", "{program}"], "size": [8, 40], "steps": [["", 1.5]]}}'

    out = subprocess.run([sys.executable, "-I", str(harness), spec], capture_output=True, text=True,
                         timeout=30, check=True).stdout

    screen = out.splitlines()
    assert "    ⟦Selected⟧ 你好" in screen  # row 3, column 5, reverse video, wide characters
    assert "cursor at 3;18" in screen  # the position query was answered: 4 + "Selected " + two wide cells
    assert "terminal modes left on: [1049]" in screen[-1]


def test_the_core_instructions_stay_small_enough_for_every_step():
    """SKILL.md is re-sent with every text-protocol step; detail belongs in reference pages."""
    assert (PACKAGE / "SKILL.md").stat().st_size <= 16 * 1024
