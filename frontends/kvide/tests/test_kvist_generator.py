# -*- coding: utf-8 -*-
"""The kvide adapter turns kvide's generator payload into kvist-core's and back."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ADAPTER = Path(__file__).resolve().parent.parent / "kvist_generator.py"
sys.path.insert(0, str(ADAPTER.parent))

import kvist_generator  # noqa: E402

DIFF = (
    "diff --git a/src/a b.py b/src/a b.py\n"
    "--- a/src/a b.py\n+++ b/src/a b.py\n@@ -1 +1 @@\n-old\n+new\n"
    "diff --git a/b.py b/b.py\n"
    "--- a/b.py\n+++ b/b.py\n@@ -1 +1 @@\n-x\n+y\n"
    "New file: notes.md\n# Todo"
)


def test_split_diff_cuts_git_sections_and_untracked_blocks():
    patches = kvist_generator.split_diff(DIFF, ["src/a b.py", "b.py", "notes.md"])

    assert [item["path"] for item in patches] == ["src/a b.py", "b.py", "notes.md"]
    assert patches[1]["patch"].endswith("+y")
    assert patches[2]["patch"] == "New file: notes.md\n# Todo"


def test_new_file_marker_for_an_unselected_path_stays_in_the_patch():
    patches = kvist_generator.split_diff(
        "diff --git a/a b/a\n+New file: other", ["a"]
    )

    assert len(patches) == 1


def fake_core(tmp_path: Path, reply: dict) -> Path:
    script = tmp_path / "core.py"
    script.write_text(
        "import json, sys\n"
        "payload = json.load(sys.stdin)\n"
        f"reply = {reply!r}\n"
        "if reply.get('ok'): reply['message'] = ','.join(f['path'] for f in payload['files'])\n"
        "print(json.dumps(reply))\n",
        encoding="utf-8",
    )
    wrapper = tmp_path / "core"
    wrapper.write_text(f"#!/bin/sh\nexec {sys.executable} {script} \"$@\"\n", encoding="utf-8")
    wrapper.chmod(0o755)
    return wrapper


def run_adapter(core: Path, cwd: Path) -> subprocess.CompletedProcess:
    context = {"version": 1, "files": ["b.py"], "diff": "diff --git a/b.py b/b.py\n+y"}
    return subprocess.run(
        [sys.executable, str(ADAPTER), "--core", str(core)],
        input=json.dumps(context),
        capture_output=True,
        text=True,
        cwd=cwd,
    )


@pytest.mark.skipif(sys.platform == "win32", reason="the fake core is a shell script")
def test_a_core_answer_becomes_the_message(tmp_path):
    result = run_adapter(fake_core(tmp_path, {"ok": True}), tmp_path)

    assert result.returncode == 0
    assert result.stdout.strip() == "b.py"


@pytest.mark.skipif(sys.platform == "win32", reason="the fake core is a shell script")
def test_a_core_error_becomes_a_json_error_envelope(tmp_path):
    result = run_adapter(fake_core(tmp_path, {"ok": False, "error": "No provider"}), tmp_path)

    assert result.returncode == 1
    assert json.loads(result.stdout) == {"error": {"code": "kvist_error", "message": "No provider"}}


def test_a_missing_core_is_reported_not_raised(tmp_path):
    result = run_adapter(tmp_path / "absent", tmp_path)

    assert result.returncode == 1
    assert json.loads(result.stdout)["error"]["code"] == "core_missing"
