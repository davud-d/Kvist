# -*- coding: utf-8 -*-
"""The kvide completion extension talks to a core process over the --complete-server protocol."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="Node.js is not installed")

EXTENSION = Path(__file__).resolve().parents[1] / "completion-extension"

# Stands in for kvide's `vscode` module: only what the extension touches.
HARNESS = r"""
const Module = require('module');
const path = require('path');
class Position { constructor(line, character) { this.line = line; this.character = character; } }
class Range { constructor(start, end) { this.start = start; this.end = end; } }
class InlineCompletionItem { constructor(insertText, range) { this.insertText = insertText; this.range = range; } }
let provider;
const vscode = {
    Range, InlineCompletionItem,
    workspace: { rootPath: process.argv[2] },
    languages: { registerInlineCompletionItemProvider: (selector, p) => { provider = p; return { dispose() {} }; } },
};
const load = Module._load;
Module._load = (request, ...rest) => (request === 'vscode' ? vscode : load(request, ...rest));
const extension = require(path.resolve(process.argv[3], 'extension.cjs'));
const context = { subscriptions: [] };
extension.activate(context);
const [file, text, offset] = [process.argv[4], process.argv[5], Number(process.argv[6])];
const document = {
    fileName: path.join(process.argv[2], file), languageId: 'python',
    getText: () => text, offsetAt: () => offset,
};
provider.provideInlineCompletionItems(document, new Position(0, offset))
    .then(items => { console.log(JSON.stringify(items)); context.subscriptions.forEach(s => s.dispose()); });
"""

FAKE_CORE = """\
import json, sys
for line in sys.stdin:
    message = json.loads(line)
    print(json.dumps({"id": message["id"], "delta": REPLY}), flush=True)
    print(json.dumps({"id": message["id"], "done": True}), flush=True)
"""


def fake_core(tmp_path: Path, reply: str) -> Path:
    """A program the extension can spawn as kvist-core, answering every request with ``reply``."""
    source = f"REPLY = {reply!r}\n{FAKE_CORE}"
    if os.name != "nt":
        core = tmp_path / "core.py"
        core.write_text(source, encoding="utf-8")
        launcher = tmp_path / "core"
        launcher.write_text(f"#!/bin/sh\nexec {sys.executable} {core} \"$@\"\n", encoding="utf-8")
        launcher.chmod(0o755)
        return launcher
    # Windows spawns only real executables (kvide gets kvist-core.exe there), so the script is
    # wrapped the way pip wraps console scripts: its .exe launcher runs the script with this Python.
    # The maker wraps only a file whose first line names Python.
    scripts = pytest.importorskip("pip._vendor.distlib.scripts", reason="pip's script launchers are needed on Windows")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "core.py").write_text(f"#!python\n{source}", encoding="utf-8")
    [launcher] = scripts.ScriptMaker(str(tmp_path / "src"), str(tmp_path / "bin")).make("core.py")
    return Path(launcher)


def run_provider(tmp_path: Path, reply: str, text: str = "pri", offset: int = 3, file: str = "a.py") -> list[dict]:
    env = {"KVIST_CORE": str(fake_core(tmp_path, reply)), "PATH": "/usr/bin:/bin"}
    # Without SystemRoot, Node on Windows cannot seed its random generator and aborts at start.
    if "SYSTEMROOT" in os.environ:
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    harness = tmp_path / "harness.cjs"
    harness.write_text(HARNESS, encoding="utf-8")
    result = subprocess.run(
        [NODE, str(harness), str(tmp_path), str(EXTENSION), file, text, str(offset)],
        env=env, capture_output=True, text=True, timeout=15,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_the_completion_becomes_one_item_inserted_at_the_cursor(tmp_path):
    [item] = run_provider(tmp_path, "nt(value)\nprint(b)")

    assert item["insertText"] == "nt(value)\nprint(b)"
    assert item["range"] == {"start": {"line": 0, "character": 3}, "end": {"line": 0, "character": 3}}


def test_an_empty_completion_gives_no_items(tmp_path):
    assert run_provider(tmp_path, "  ") == []


def test_a_file_outside_the_workspace_gives_no_items(tmp_path):
    assert run_provider(tmp_path, "x", file="../elsewhere.py") == []
