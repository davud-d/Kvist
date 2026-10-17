# -*- coding: utf-8 -*-
from __future__ import annotations

import re

import pytest

from kernel.agent_model import _load_role_prompt
from kernel.parser import ALLOWED_ACTIONS
from kernel.tests.conftest import commands_from_response

_WRITE_ACTIONS = {"write_file", "append_file", "replace_in_file"}
_TOOL_CALL_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)


def _example_calls(prompt_text: str) -> list[str]:
    """The <tool_call> blocks of the EXAMPLES section (RESPONSE FORMAT shows a template)."""
    examples = prompt_text[re.search(r"^== \d+\. EXAMPLES ==$", prompt_text, re.MULTILINE).end():]
    following = re.search(r"^== \d+\. ", examples, re.MULTILINE)
    return _TOOL_CALL_RE.findall(examples[: following.start()] if following else examples)


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
    # Still offers the essential read/analyze surface; a response without a
    # tool call is the report; final_answer optionally carries an explicit outcome.
    assert {"read_file", "search_code"} <= actions
    assert "final_answer" in actions
    assert "report_to_user" not in actions


def test_reader_prompt_has_no_write_or_patch_guidance():
    reader = _load_role_prompt("reader")
    for token in ("write_file", "append_file", "replace_in_file"):
        assert token not in reader


def test_reader_prompt_keeps_the_turn_end_untrusted_and_examples():
    reader = _load_role_prompt("reader")
    assert "A response with no tool call ends your turn" in reader
    assert "UNTRUSTED" in reader
    examples = _example_calls(reader)
    assert examples
    for example in examples:
        for command in commands_from_response(example):
            assert command.action.type in ALLOWED_ACTIONS


def test_reader_prompt_stays_within_token_budget():
    try:
        import tiktoken
        import requests
    except ImportError:
        pytest.skip("tiktoken unavailable")
    try:
        enc = tiktoken.get_encoding("cl100k_base")
    except requests.exceptions.RequestException:
        # cl100k_base's vocab is fetched from a remote blob on first use.
        # No network and no local cache means this budget genuinely cannot
        # be checked here, rather than a real regression, so skip instead
        # of failing.
        pytest.skip(
            "tiktoken cl100k_base is not cached locally and no network is "
            "available to download it"
        )
    tokens = len(enc.encode(_load_role_prompt("reader")))
    # 1500 → 1600 for section 11 (KVIST.md); → 1900 for the HANDOFF path
    # (delegate_task to coder/devops); → 2100 for action batches, which changed
    # the response schema itself; → 2150 for the finish "outcome" arg, another
    # schema change; → 2200 for the anti-sycophancy line in section 8 (a
    # user's claim about existing behavior is not evidence); → 2280 for
    # naming the grounding principle explicitly in section 8 plus two more
    # examples of it (git_status/git_diff cover tracked changes only; a
    # truncated log/dump can make a naive count wrong); → 2520 for naming the
    # investigation principle in section 5 (an action earns its step only when
    # you can name the fact it settles), which replaced the "max 2 consecutive
    # inspects" counter that a run of 50 search_code calls sailed straight
    # past. It briefly went to 3200 for DELIVERING WORK and CORRECTIONS, which
    # then moved into prompts/system/constitution.txt and came back out of
    # here. This budget covers the role file alone; build_role_system_prompt
    # prepends the constitution on every call, so the real per-call cost is
    # this plus that file. Nothing else may grow into either.
    # → 2700 for the emission paragraphs in section 2: the JSON object is the
    # only channel out, so the format is nothing to recall at the end of a long
    # reasoning pass, and an answer worked out in thinking was written nowhere.
    # The v2 contract (the <tool_call> block, when to speak, which calls run
    # together, how an end of the turn is checked) fit into the same budget by
    # saying each rule once.
    # → 3000 for the cost of a response in BATCHING: runs took 2-3 reads per
    # response while 4-8 were independent, and every extra response resends
    # the whole conversation. The extra headroom is deliberate: wording that
    # states the reason is kept whole rather than squeezed to fit a ceiling.
    # → 3100 for the wait and schedule tools: a read-only task ("collect the
    # AI news every morning") is routed to the reader, so it needs them too.
    assert tokens <= 3100, f"reader prompt is {tokens} tokens (budget 3100)"
