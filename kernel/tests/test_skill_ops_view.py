"""``--skill-op`` output: drawn for a person at a terminal, JSON for programs and with --json."""
from __future__ import annotations

import json
import re
import sys
from argparse import Namespace

import pytest
from rich.console import Console

from kernel.skill_ops import main_skill_op
from kernel.skill_ops_view import render
from kernel.tests.skill_fixtures import write_skill

READY = {"id": "browser", "name": "Browser", "summary": "Its own Chromium.", "version": "0.1.1", "kind": "tools",
         "available": True, "reason": "", "approved_here": True, "capabilities": ["files.read"],
         "tools": ["open_page", "snapshot", "click", "type", "scroll", "wait_for"]}
BLOCKED = {"id": "documents", "name": "Documents", "summary": "Reads PDF.", "version": "0.1.0", "kind": "tools",
           "available": False, "reason": "Python dependencies are not installed; run: kvist --skill-op install-deps "
           "documents", "approved_here": False, "capabilities": ["files.read"], "tools": ["info", "read"]}
GUIDE = {"id": "debugging", "name": "Debugging", "summary": "How to debug.", "version": "0.1.0",
         "kind": "instructions", "available": True, "reason": "", "approved_here": False, "capabilities": [],
         "tools": []}


def plain(text):
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def drawn(op, payload, language="en", width=70):
    console = Console(record=True, width=width, force_terminal=False, color_system=None)
    render(op, payload, console, language)
    return console.export_text()


def test_list_shows_state_permissions_tools_and_the_way_out():
    text = drawn("list", {"ok": True, "skills": [READY, BLOCKED, GUIDE]})

    assert "● browser  0.1.1" in text and "✓ approved here" in text
    assert "open_page, snapshot, click, type +2" in text
    assert "○ documents" in text and "→ Python dependencies are not installed" in text
    assert "install-deps" in text and "asks on first use" in text
    assert "● debugging  0.1.0  instructions  ·  —" in text
    assert "2 of 3 ready" in text


def test_list_in_russian_and_empty():
    assert "Готово 2 из 3" in drawn("list", {"ok": True, "skills": [READY, BLOCKED, GUIDE]}, "ru")
    assert "No skills are installed." in drawn("list", {"ok": True, "skills": []})


def test_install_check_and_error_lines():
    installed = drawn("install", {"ok": True, "installed": "browser", "version": "0.1.1", "created": True,
                                  "active_version": "0.1.1", "available": True, "reason": "", "note": "Approve it."})
    again = drawn("install", {"ok": True, "installed": "browser", "version": "0.1.1", "created": False,
                              "active_version": "0.1.1", "available": True, "reason": ""})
    failed = drawn("check", {"ok": False, "report": {"skill": "x", "version": "0.1.0", "findings": [
        {"check": "manifest", "ok": True, "detail": "", "severity": "error"},
        {"check": "undeclared_ctx_call", "ok": False, "detail": "plugin.py:3: needs files.read",
         "severity": "error"}], "author_tests": {"status": "passed"}, "similar_installed": []}})
    error = drawn("list", {"ok": False, "error": "no installed skill 'x'"})

    assert "✓ browser 0.1.1 — installed, active 0.1.1" in installed and "Approve it." in installed
    assert "already installed, same content" in again
    assert "✗ x 0.1.0 — failed" in failed and "✗ undeclared_ctx_call  plugin.py:3: needs files.read" in failed
    assert "manifest" not in failed  # a passing check without detail is noise
    assert "✗ Error: no installed skill 'x'" in error


def test_an_unknown_shape_falls_back_to_json():
    text = drawn("something-new", {"ok": True, "value": 3})

    assert '"value": 3' in text


@pytest.fixture
def config(tmp_path, monkeypatch):
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path / "cfg"))
    monkeypatch.setenv("NO_COLOR", "1")
    return tmp_path


def args(config, op, **extra):
    base = {"skill_op": op, "workspace": str(config), "skill": None, "skill_path": None, "skill_version": None,
            "yes": False, "language": None, "json": False}
    return Namespace(**{**base, **extra})


def test_programs_get_json_when_stdout_is_not_a_terminal(config, capsys):
    folder = write_skill(config / "src", skill_id="guide", kind="instructions")

    main_skill_op(args(config, "install", skill_path=str(folder)))
    payload = json.loads(capsys.readouterr().out)

    assert payload["ok"] is True and payload["installed"] == "guide"


def test_a_terminal_gets_the_drawn_result_and_json_flag_overrides(config, capsys, monkeypatch):
    folder = write_skill(config / "src", skill_id="guide", kind="instructions")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)

    main_skill_op(args(config, "install", skill_path=str(folder)))
    drawn_out = plain(capsys.readouterr().out)
    main_skill_op(args(config, "list", json=True))
    json_out = capsys.readouterr().out

    assert drawn_out.lstrip().startswith("✓ guide 0.1.0")
    assert json.loads(json_out)["skills"][0]["id"] == "guide"


def test_a_failure_exits_1_in_both_modes(config, capsys, monkeypatch):
    with pytest.raises(SystemExit) as piped:
        main_skill_op(args(config, "show", skill="nope"))
    assert json.loads(capsys.readouterr().out)["ok"] is False

    monkeypatch.setattr(sys.stdout, "isatty", lambda: True, raising=False)
    with pytest.raises(SystemExit) as terminal:
        main_skill_op(args(config, "show", skill="nope"))

    assert piped.value.code == 1 and terminal.value.code == 1
    assert "✗ Error: no installed skill 'nope'" in plain(capsys.readouterr().out)
