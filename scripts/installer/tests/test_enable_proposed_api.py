# -*- coding: utf-8 -*-
"""The VS Code installer lists the extension in argv.json without disturbing what the user wrote there."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

import install_vscode
import jsonc_settings

KEY = "enable-proposed-api"
ID = "kvist.kvist"


def add(text: str) -> str:
    return jsonc_settings.add_string_to_array(text, KEY, ID)


def test_the_member_is_added_beside_the_users_own_flags_and_comments():
    before = '{\n\t// keep me\n\t"locale": "en",\n\t"password-store": "basic"\n}\n'
    assert add(before) == (
        '{\n\t// keep me\n\t"locale": "en",\n\t"password-store": "basic",\n\t"enable-proposed-api": ["kvist.kvist"]\n}\n'
    )


def test_an_existing_array_gets_the_id_in_its_own_layout():
    assert add('{\n\t"enable-proposed-api": ["a.b"],\n\t"x": 1\n}') == '{\n\t"enable-proposed-api": ["a.b", "kvist.kvist"],\n\t"x": 1\n}'
    assert add('{\n\t"enable-proposed-api": [\n\t\t"a.b", // mine\n\t\t"c.d",\n\t],\n}') == (
        '{\n\t"enable-proposed-api": [\n\t\t"a.b", // mine\n\t\t"c.d",\n\t\t"kvist.kvist",\n\t],\n}'
    )
    assert add('{"enable-proposed-api": []}') == '{"enable-proposed-api": ["kvist.kvist"]}'


@pytest.mark.parametrize("text", [
    '{\n\t"enable-proposed-api": ["kvist.kvist"]\n}',
    '{\n\t"enable-proposed-api": [\n\t\t"a.b",\n\t\t"kvist.kvist" // there\n\t]\n}',
])
def test_a_file_that_already_lists_the_id_is_returned_untouched(text):
    assert add(text) == text


def test_a_commented_out_line_is_not_the_setting():
    assert add('{\n\t// "enable-proposed-api": ["old"]\n}').count("kvist.kvist") == 1


def test_something_that_is_not_an_array_is_left_for_the_user():
    with pytest.raises(jsonc_settings.JsoncError):
        add('{"enable-proposed-api": "kvist.kvist"}')


@pytest.fixture
def home(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    (tmp_path / ".vscode").mkdir()
    return tmp_path


def test_the_installer_edits_argv_json_once_and_keeps_a_backup(home):
    argv = home / ".vscode" / "argv.json"
    original = '{\n\t// flags\n\t"locale": "en"\n}\n'
    argv.write_bytes(original.encode())
    assert install_vscode.enable_proposed_api("VS Code") is True
    assert argv.read_text(encoding="utf-8") == '{\n\t// flags\n\t"locale": "en",\n\t"enable-proposed-api": ["kvist.kvist"]\n}\n'
    assert (home / ".vscode" / "argv.json.kvist-backup").read_bytes() == original.encode()
    changed = argv.read_bytes()
    assert install_vscode.enable_proposed_api("VS Code") is False
    assert argv.read_bytes() == changed


def test_crlf_line_endings_survive(home):
    argv = home / ".vscode" / "argv.json"
    argv.write_bytes(b'{\r\n\t"locale": "en"\r\n}\r\n')
    install_vscode.enable_proposed_api("VS Code")
    assert b"\r\n" in argv.read_bytes() and b'"locale": "en",' in argv.read_bytes()


def test_a_missing_argv_json_is_created_and_an_editor_that_was_never_run_is_skipped(home):
    assert install_vscode.enable_proposed_api("VS Code") is True
    assert '"kvist.kvist"' in (home / ".vscode" / "argv.json").read_text(encoding="utf-8")
    assert install_vscode.enable_proposed_api("VSCodium") is False
    assert not (home / ".vscode-oss").exists()


def test_a_broken_argv_json_is_never_overwritten(home):
    argv = home / ".vscode" / "argv.json"
    argv.write_bytes(b'{"enable-proposed-api": "oops"}')
    assert install_vscode.enable_proposed_api("VS Code") is False
    assert argv.read_bytes() == b'{"enable-proposed-api": "oops"}'
