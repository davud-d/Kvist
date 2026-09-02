# -*- coding: utf-8 -*-
from __future__ import annotations

import re

import pytest

from agent_model import _load_role_prompt
from parser import ALLOWED_ACTIONS, parse_agent_command

_WRITE_ACTIONS = {"write_file", "append_file", "replace_in_file"}
_EXAMPLE_LINE_RE = re.compile(r'^\{"thought".*\}$', re.MULTILINE)


def _reader_allowed_actions(prompt_text: str) -> set[str]:
    match = re.search(
        r"==\s*\d+(?:\.\d+)?\.\s*ALLOWED ACTIONS\s*==\s*\n+(.+?)\n",
        prompt_text,
    )
    assert match is not None, "ALLOWED ACTIONS section not found in reader prompt"
    line = match.group(1).strip().rstrip(".")
    return {item.strip() for item in line.split(",") if item.strip()}


def test_reader_prompt_loads_without_falling_back_to_coder():
    reader = _load_role_prompt("reader")
    coder = _load_role_prompt("coder")
    assert reader != coder
    assert "read-only" in reader.lower()


def test_reader_prompt_lists_only_read_only_actions():
    actions = _reader_allowed_actions(_load_role_prompt("reader"))
    # A real subset of the parser contract, with every write action removed.
    assert actions <= ALLOWED_ACTIONS
    assert actions.isdisjoint(_WRITE_ACTIONS)
    # Still offers the essential read/analyze/report surface.
    assert {"read_file", "search_code", "report_to_user", "final_answer"} <= actions


def test_reader_prompt_has_no_write_or_patch_guidance():
    reader = _load_role_prompt("reader")
    for token in ("write_file", "append_file", "replace_in_file"):
        assert token not in reader


def test_reader_prompt_keeps_report_and_untrusted_and_examples():
    reader = _load_role_prompt("reader")
    assert "report_to_user" in reader
    assert "UNTRUSTED" in reader
    examples = _EXAMPLE_LINE_RE.findall(reader)
    assert examples
    for example in examples:
        command = parse_agent_command(example)
        assert command.action.type in ALLOWED_ACTIONS


def test_reader_prompt_stays_within_token_budget():
    try:
        import tiktoken
    except ImportError:
        pytest.skip("tiktoken unavailable")
    enc = tiktoken.get_encoding("cl100k_base")
    tokens = len(enc.encode(_load_role_prompt("reader")))
    # 1500 → 1600 for section 11 (KVIST.md); → 1900 for the HANDOFF path
    # (delegate_task to coder/devops). Nothing else may grow into it.
    assert tokens <= 1900, f"reader prompt is {tokens} tokens (budget 1900)"
