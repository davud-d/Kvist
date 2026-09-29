from __future__ import annotations

import re

import pytest

from kernel.agent_model import _load_role_prompt
from kernel.parser import ALLOWED_ACTIONS
from kernel.tests.conftest import commands_from_response

_TOOL_CALL_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)


def _example_calls(prompt_text: str) -> list[str]:
    """The <tool_call> blocks of the EXAMPLES section (RESPONSE FORMAT shows a template)."""
    examples = prompt_text[re.search(r"^== \d+\. EXAMPLES ==$", prompt_text, re.MULTILINE).end():]
    following = re.search(r"^== \d+\. ", examples, re.MULTILINE)
    return _TOOL_CALL_RE.findall(examples[: following.start()] if following else examples)
# Honoured when an older prompt calls them, never offered: a response without
# a tool call ends the turn.
_LEGACY_FINISHES = {"final_answer", "report_to_user"}


def _extract_allowed_actions_block(prompt_text: str) -> set[str]:
    """Extract the action list that follows the ALLOWED ACTIONS header."""
    match = re.search(
        r"==\s*\d+(?:\.\d+)?\.\s*ALLOWED ACTIONS\s*==\s*\n+(.+?)\n",
        prompt_text,
    )
    assert match is not None, "ALLOWED ACTIONS section not found in prompt"
    line = match.group(1).strip().rstrip(".")
    return {item.strip() for item in line.split(",") if item.strip()}


@pytest.mark.parametrize("role", ["coder", "devops", "universal"])
def test_prompt_action_list_matches_parser(role):
    """The allowed-actions list in role prompts must mirror parser.ALLOWED_ACTIONS."""
    prompt_text = _load_role_prompt(role)
    prompt_actions = _extract_allowed_actions_block(prompt_text)
    offered = ALLOWED_ACTIONS - _LEGACY_FINISHES
    assert prompt_actions == offered, (
        f"{role} prompt action list is out of sync with parser.ALLOWED_ACTIONS. "
        f"Missing in prompt: {sorted(offered - prompt_actions)}. "
        f"Extra in prompt: {sorted(prompt_actions - offered)}."
    )


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal"])
def test_prompt_says_a_response_without_calls_ends_the_turn(role):
    assert "A response with no tool call ends your turn" in _load_role_prompt(role)


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal"])
def test_prompt_no_longer_asks_for_thought_or_user_message(role):
    prompt_text = _load_role_prompt(role)
    for gone in ('"thought"', "user_message", "140 chars", "step_summary", "plan_step"):
        assert gone not in prompt_text, gone


@pytest.mark.parametrize("role", ["coder", "devops", "universal"])
def test_prompt_tool_call_examples_pass_the_parser(role):
    """Every <tool_call> example in a role prompt must pass the runtime's own validation."""
    examples = _example_calls(_load_role_prompt(role))
    assert examples, f"No tool call examples found in {role} prompt"

    for example in examples:
        (command,) = commands_from_response(example)
        assert command.action.type in ALLOWED_ACTIONS - _LEGACY_FINISHES


@pytest.mark.parametrize("role", ["coder", "devops", "universal"])
def test_prompt_marks_web_content_untrusted(role):
    """Role prompts must teach that web content is data, not instructions."""
    prompt_text = _load_role_prompt(role)
    assert "UNTRUSTED" in prompt_text


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal"])
def test_every_shell_example_names_its_purpose(role):
    """The person sees run_shell's purpose in place of the argv."""
    import json

    for example in _example_calls(_load_role_prompt(role)):
        call = json.loads(example[len("<tool_call>"):-len("</tool_call>")])
        if call["name"] == "run_shell":
            assert call["arguments"].get("purpose"), call
