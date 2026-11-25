from __future__ import annotations

import re

import pytest

from kernel.agent.agent_model import _load_role_prompt
from kernel.protocol.parser import ALLOWED_ACTIONS
from kernel.agent.native_tools import native_role_prompt
from kernel.tests.conftest import commands_from_response

_TOOL_CALL_RE = re.compile(r"<tool_call>.*?</tool_call>", re.DOTALL)


def _example_calls(prompt_text: str) -> list[str]:
    """The <tool_call> blocks of the EXAMPLES section (RESPONSE FORMAT shows a template)."""
    examples = prompt_text[re.search(r"^== \d+\. examples ==$", prompt_text, re.MULTILINE).end():]
    following = re.search(r"^== \d+\. ", examples, re.MULTILINE)
    return _TOOL_CALL_RE.findall(examples[: following.start()] if following else examples)
# Only the reporter handoff is legacy. final_answer is optional for explicit
# outcomes; ordinary text without calls still ends the turn.
_LEGACY_FINISHES = {"report_to_user"}
# Added to the prompt at run time, and only by an interactive frontend
# (end_session.with_tool_in_prompt); the static prompt files never list it.
_CONDITIONAL = {"end_session", "generate_image"}
# Offered by the reviewer's role prompt of a best-of run only (judge_prompt.txt), never by the roles above.
_REVIEW_ACTIONS = {"executor", "verdict"}
_REVIEWER_ACTIONS = {"final_answer", "read_file", "read_multiple_files", "list_dir", "list_tree", "find_files",
                     "search_code", "git_status", "git_diff", "executor", "verdict"}


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal"])
def test_shell_permission_rules_survive_native_prompt_conversion(role):
    prompt = native_role_prompt(_load_role_prompt(role), "Native calls")
    assert '"sandbox": "default" | "host"' in prompt
    assert "never retry a user-denied permission" in prompt
    assert "do not silently add network access" in prompt


def _extract_allowed_actions_block(prompt_text: str) -> set[str]:
    """Extract the action list that follows the ALLOWED ACTIONS header."""
    match = re.search(
        r"==\s*\d+(?:\.\d+)?\.\s*allowed actions\s*==\s*\n+(.+?)\n",
        prompt_text,
    )
    assert match is not None, "allowed actions section not found in prompt"
    line = match.group(1).strip().rstrip(".")
    return {item.strip() for item in line.split(",") if item.strip()}


@pytest.mark.parametrize("role", ["coder", "devops", "universal"])
def test_prompt_action_list_matches_parser(role):
    """The allowed-actions list in role prompts must mirror parser.ALLOWED_ACTIONS."""
    prompt_text = _load_role_prompt(role)
    prompt_actions = _extract_allowed_actions_block(prompt_text)
    offered = ALLOWED_ACTIONS - _LEGACY_FINISHES - _CONDITIONAL - _REVIEW_ACTIONS
    assert prompt_actions == offered, (
        f"{role} prompt action list is out of sync with parser.ALLOWED_ACTIONS. "
        f"Missing in prompt: {sorted(offered - prompt_actions)}. "
        f"Extra in prompt: {sorted(prompt_actions - offered)}."
    )


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal", "judge"])
def test_prompt_says_a_response_without_calls_ends_the_turn(role):
    assert "a response with no tool call ends your turn" in _load_role_prompt(role)


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal", "judge"])
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
        assert command.action.type in ALLOWED_ACTIONS - _LEGACY_FINISHES - _CONDITIONAL


@pytest.mark.parametrize("role", ["coder", "devops", "universal", "judge"])
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


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal"])
def test_end_session_is_offered_with_its_block_and_schema(role):
    from kernel.plan.end_session import _PROMPT_FILE, with_tool_in_prompt

    prompt_text = with_tool_in_prompt(_load_role_prompt(role))
    listed = _extract_allowed_actions_block(prompt_text)
    assert "end_session" in listed
    assert listed - {"end_session"} == _extract_allowed_actions_block(_load_role_prompt(role))
    assert _PROMPT_FILE.read_text(encoding="utf-8").strip() in prompt_text
    assert 'end_session: {"message_id": <int>, "request": true' in prompt_text


def test_end_session_block_is_the_agreed_text():
    from kernel.plan.end_session import _PROMPT_FILE

    text = _PROMPT_FILE.read_text(encoding="utf-8")
    assert text.startswith("end_session: closes this conversation for good, in two cases:")
    assert "the tool speaks." in text
    assert "the first call shows a warning and ends your turn; send nothing after it." in text
    assert "call end_session again with that new message's id" in text
    assert "if it returns a warning, the same rule applies to subsequent messages." in text
    assert "request=true" in text and "may harm themselves or someone else" in text


@pytest.mark.parametrize("role", ["coder", "reader", "devops", "universal"])
def test_both_transports_describe_model_owned_completion(role):
    from kernel.agent.agent_model import _load_native_channel_section

    original = _load_role_prompt(role)
    native = native_role_prompt(original, _load_native_channel_section())
    for prompt in (original, native):
        assert "refused once" not in prompt
        assert "runtime never records a fix task with no edits as done" not in prompt
        assert "final_answer" in _extract_allowed_actions_block(prompt)
        assert "no_change_needed" in prompt


def test_the_reviewer_prompt_offers_the_reads_and_the_two_review_actions_and_nothing_that_writes():
    prompt = _load_role_prompt("judge")

    assert _extract_allowed_actions_block(prompt) == _REVIEWER_ACTIONS
    assert _REVIEWER_ACTIONS <= ALLOWED_ACTIONS
    assert not _REVIEWER_ACTIONS & {"write_file", "append_file", "replace_in_file", "run_shell", "delegate_task"}


@pytest.mark.parametrize("role", ["coder", "devops", "reader", "universal"])
def test_no_other_role_prompt_offers_the_review_actions(role):
    prompt = _load_role_prompt(role)

    assert not _REVIEW_ACTIONS & _extract_allowed_actions_block(prompt)
    assert not any(f"{action}:" in prompt for action in _REVIEW_ACTIONS)


def test_every_example_of_the_reviewer_prompt_passes_the_parser_with_the_actions_it_offers():
    examples = _example_calls(_load_role_prompt("judge"))
    assert examples

    seen = set()
    for example in examples:
        (command,) = commands_from_response(example)
        assert command.action.type in _REVIEWER_ACTIONS
        seen.add(command.action.type)
    assert {"executor", "verdict", "read_file"} <= seen


def test_the_native_reviewer_prompt_keeps_its_action_list_and_drops_the_text_protocol():
    from kernel.agent.agent_model import _load_native_channel_section

    native = native_role_prompt(_load_role_prompt("judge"), _load_native_channel_section())

    assert _extract_allowed_actions_block(native) == _REVIEWER_ACTIONS
    assert "<tool_call>" not in native and "== 4. ARGS SCHEMAS ==" not in native
