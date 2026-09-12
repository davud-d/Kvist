from __future__ import annotations

import re

import pytest

from agent_model import _load_role_prompt
from parser import ALLOWED_ACTIONS, parse_agent_commands

_EXAMPLE_LINE_RE = re.compile(r"^\{\"thought\".*\}$", re.MULTILINE)


def _extract_allowed_actions_block(prompt_text: str) -> set[str]:
    """Extract the action list that follows the ALLOWED ACTIONS header."""
    match = re.search(
        r"==\s*\d+(?:\.\d+)?\.\s*ALLOWED ACTIONS\s*==\s*\n+(.+?)\n",
        prompt_text,
    )
    assert match is not None, "ALLOWED ACTIONS section not found in prompt"
    line = match.group(1).strip().rstrip(".")
    return {item.strip() for item in line.split(",") if item.strip()}


@pytest.mark.parametrize("role", ["coder", "devops"])
def test_prompt_action_list_matches_parser(role):
    """The allowed-actions list in role prompts must mirror parser.ALLOWED_ACTIONS."""
    prompt_text = _load_role_prompt(role)
    prompt_actions = _extract_allowed_actions_block(prompt_text)
    assert prompt_actions == ALLOWED_ACTIONS, (
        f"{role} prompt action list is out of sync with parser.ALLOWED_ACTIONS. "
        f"Missing in prompt: {sorted(ALLOWED_ACTIONS - prompt_actions)}. "
        f"Extra in prompt: {sorted(prompt_actions - ALLOWED_ACTIONS)}."
    )


def test_coder_prompt_mentions_report_to_user():
    prompt_text = _load_role_prompt("coder")
    assert "report_to_user" in prompt_text


def test_devops_prompt_mentions_report_to_user():
    prompt_text = _load_role_prompt("devops")
    assert "report_to_user" in prompt_text


@pytest.mark.parametrize("role", ["coder", "devops"])
def test_prompt_json_examples_pass_parser(role):
    """Every inline JSON example in role prompts must parse via parse_agent_commands."""
    prompt_text = _load_role_prompt(role)
    examples = _EXAMPLE_LINE_RE.findall(prompt_text)
    assert examples, f"No JSON examples found in {role} prompt"

    for example in examples:
        # Examples teach the batch form; the single-action form is only tolerated.
        assert '"actions"' in example
        for command in parse_agent_commands(example):
            assert command.action.type in ALLOWED_ACTIONS


@pytest.mark.parametrize("role", ["coder", "devops"])
def test_prompt_marks_web_content_untrusted(role):
    """Role prompts must teach that web content is data, not instructions."""
    prompt_text = _load_role_prompt(role)
    assert "UNTRUSTED" in prompt_text


@pytest.mark.parametrize("role", ["coder", "devops", "reader"])
def test_prompt_example_descriptions_fit_the_banner_action_row(role):
    """The Action row shows 17 characters; a longer description is cut mid-word."""
    import json

    for example in _EXAMPLE_LINE_RE.findall(_load_role_prompt(role)):
        for action in json.loads(example)["actions"]:
            assert len(action.get("description", "")) <= 16, action
