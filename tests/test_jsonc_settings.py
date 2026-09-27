# -*- coding: utf-8 -*-
"""Zed's settings file is JSONC: the editor must never lose a comment.

These exercise ``installer/jsonc_settings.py``, which replaces only the
character range of the one member it writes.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent / "installer"
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

import jsonc_settings
from jsonc_settings import JsoncError, has_agent_server, remove_agent_server, set_agent_server

ENTRY = json.dumps(
    {"type": "custom", "command": "/opt/kvist-core", "args": ["--acp"]}, indent=2
)


def comments(text: str) -> list[str]:
    return [
        text[token.start : token.end]
        for token in jsonc_settings._tokenize(text)
        if token.kind == "comment"
    ]


def member_names(text: str) -> list[str]:
    open_index, close_index = jsonc_settings._root_object(text)
    return [
        member.key
        for member in jsonc_settings._object_members(
            text, jsonc_settings._tokenize(text), open_index, close_index
        )
    ]


def inner_names(text: str) -> list[str]:
    open_index, close_index = jsonc_settings._root_object(text)
    servers = jsonc_settings._find_member(text, open_index, close_index, "agent_servers")
    return [
        member.key
        for member in jsonc_settings._object_members(
            text, jsonc_settings._tokenize(text), servers.value_start, servers.value_end - 1
        )
    ]


# --- Writing -----------------------------------------------------------------


def test_a_new_entry_keeps_every_other_key_and_comment():
    text = '{\n  "theme": "one" // keep me\n}\n'

    out = set_agent_server(text, "Kvist", ENTRY)

    assert '"theme": "one"' in out
    assert "// keep me" in out
    assert has_agent_server(out, "Kvist") is True
    assert jsonc_settings._root_object(out)


def test_a_comment_stays_on_the_line_that_carries_it():
    """Inserting before the comment would make it look like it describes us."""
    text = '{\n  "theme": "one" // keep me\n}\n'

    out = set_agent_server(text, "Kvist", ENTRY)

    assert '"theme": "one", // keep me' in out


def test_a_comment_about_the_next_member_is_not_stolen():
    text = '{\n  "a": 1,\n  // about b\n  "b": 2\n}\n'

    out = set_agent_server(text, "Kvist", ENTRY)

    assert '// about b\n  "b": 2' in out


def test_an_existing_entry_replaces_only_its_value():
    text = (
        '{\n  "agent_servers": {\n    // about kvist\n'
        '    "Kvist": {"command": "old"},\n    "Other": {"command": "x"}\n  }\n}\n'
    )

    out = set_agent_server(text, "Kvist", ENTRY)

    assert '"command": "old"' not in out
    assert "// about kvist" in out
    assert '"Other": {"command": "x"}' in out
    assert inner_names(out) == ["Kvist", "Other"]


def test_writing_twice_changes_nothing():
    once = set_agent_server('{\n  "vim_mode": true\n}\n', "Kvist", ENTRY)

    assert set_agent_server(once, "Kvist", ENTRY) == once


def test_no_comment_is_lost_by_a_write():
    text = (
        '{\n  // top note\n  "agent_servers": {\n'
        '    "Other": {"command": "x"} // their agent\n  },\n  "vim_mode": true // on\n}\n'
    )

    out = set_agent_server(text, "Kvist", ENTRY)

    assert sorted(comments(out)) == sorted(comments(text))


def test_the_entry_is_indentable_alongside_its_siblings():
    text = '{\n  "agent_servers": {\n    "Other": {"command": "x"}\n  }\n}\n'

    out = set_agent_server(text, "Kvist", ENTRY)

    assert '    "Kvist": {\n      "type": "custom",' in out


# --- Removing ----------------------------------------------------------------


def test_removing_a_middle_entry_keeps_the_others_valid():
    text = (
        '{\n  "agent_servers": {\n    "A": 1,\n'
        '    "Kvist": {"command": "old"},\n    "B": 2\n  }\n}\n'
    )

    out = remove_agent_server(text, "Kvist")

    assert inner_names(out) == ["A", "B"]
    assert jsonc_settings._root_object(out)


def test_removing_the_last_entry_leaves_the_leading_comma_out():
    text = set_agent_server(
        '{\n  "agent_servers": {\n    "A": 1\n  }\n}\n', "Kvist", ENTRY
    )

    out = remove_agent_server(text, "Kvist")

    assert inner_names(out) == ["A"]
    assert ",\n  }" not in out


def test_removing_the_only_entry_drops_an_empty_agent_servers():
    text = set_agent_server('{\n  "vim_mode": true\n}\n', "Kvist", ENTRY)

    out = remove_agent_server(text, "Kvist")

    assert member_names(out) == ["vim_mode"]
    assert "agent_servers" not in out


def test_removing_an_absent_entry_changes_nothing():
    text = '{\n  "vim_mode": true // keep\n}\n'

    assert remove_agent_server(text, "Kvist") == text


def test_removing_keeps_a_neighbouring_comment():
    text = (
        '{\n  "agent_servers": {\n    "Other": {"command": "x"} // their agent\n  }\n}\n'
    )
    seeded = set_agent_server(text, "Kvist", ENTRY)

    out = remove_agent_server(seeded, "Kvist")

    assert "// their agent" in out
    assert '"Other": {"command": "x"}' in out


def test_remove_is_a_no_op_the_second_time():
    text = set_agent_server('{\n  "a": 1\n}\n', "Kvist", ENTRY)

    once = remove_agent_server(text, "Kvist")

    assert remove_agent_server(once, "Kvist") == once


# --- What must not be mistaken for structure ---------------------------------


def test_braces_and_colons_inside_strings_and_comments_are_not_structure():
    text = '{\n  "s": "a } b", // }\n  /* { */\n  "agent_servers": {"Kvist": {"c": 1}}\n}\n'

    assert has_agent_server(text, "Kvist") is True
    out = remove_agent_server(text, "Kvist")
    assert "agent_servers" not in out
    assert '"s": "a } b"' in out


# --- Rejected documents ------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   \n",
        "// nothing here\n",
        "[1, 2]",
        '{"a": ',
        '{"a": , "b": 1}',
        "{\n  \"a\": 1\n",  # unclosed
    ],
)
def test_a_document_that_cannot_be_read_is_refused(text):
    with pytest.raises(JsoncError):
        set_agent_server(text, "Kvist", ENTRY)


def test_agent_servers_that_is_not_an_object_is_refused():
    with pytest.raises(JsoncError, match="agent_servers"):
        set_agent_server('{"agent_servers": 5}', "Kvist", ENTRY)


def test_an_entry_that_is_not_an_object_is_refused():
    with pytest.raises(JsoncError, match="must be a JSON object"):
        set_agent_server('{"a": 1}', "Kvist", "[]")

    with pytest.raises(JsoncError, match="not valid JSON"):
        set_agent_server('{"a": 1}', "Kvist", "{")


def test_an_unreadable_document_is_refused_by_removal_too():
    with pytest.raises(JsoncError):
        remove_agent_server('{"a": ', "Kvist")
