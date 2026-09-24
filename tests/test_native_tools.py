from __future__ import annotations

import json
from types import SimpleNamespace
from typing import get_args

import pytest

import agent_model
from agent_state import NO_PLAN_STEP, PlanInfo
from native_tools import (
    ACTION_TOOLS,
    ELIDED_OUTPUT,
    NativeTurn,
    ToolCall,
    commands_from_turn,
    compact_history,
    final_answer_from_turn,
    native_role_prompt,
    tools_for_role,
)
from parser import CommandValidationError
from protocol import ActionType


def _call(call_id: str, name: str, **args) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=json.dumps(args)))


def _response(content: str = "", calls: list | None = None, reasoning: str | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=calls or None, reasoning_content=reasoning)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class ScriptedClient:
    """Answers the probe with a ping call, then plays scripted native turns."""

    def __init__(self, turns: list):
        self.turns = list(turns)
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(json.loads(json.dumps(kwargs, default=str)))
        if kwargs["tools"][0]["function"]["name"] == "ping":
            return _response(calls=[_call("probe", "ping", ok=True)])
        turn = self.turns.pop(0)
        if isinstance(turn, Exception):
            raise turn
        return turn


@pytest.fixture(autouse=True)
def _fresh_probe_cache(monkeypatch):
    monkeypatch.setattr(agent_model, "_NATIVE_TOOL_SUPPORT", {})
    monkeypatch.delenv("AGENT_NATIVE_TOOLS", raising=False)


def _native_agent(agent_factory, turns: list):
    agent = agent_factory(max_steps=10, stream=False)
    agent.client = ScriptedClient(turns)
    agent._state.plan_info = PlanInfo(
        goal="fix it", complexity="simple", plan=[NO_PLAN_STEP], success_criteria=[], risks=[]
    )
    agent._state.response_language = "en"
    return agent


def test_every_action_is_offered_as_a_tool():
    assert set(ACTION_TOOLS) == set(get_args(ActionType))


def test_tools_follow_the_role_prompt_allowed_actions():
    coder = {tool["function"]["name"] for tool in tools_for_role(agent_model._load_role_prompt("coder"))}
    reader = {tool["function"]["name"] for tool in tools_for_role(agent_model._load_role_prompt("reader"))}

    assert {"write_file", "replace_in_file", "run_shell", "final_answer"} <= coder
    assert not reader & {"write_file", "append_file", "replace_in_file"}
    assert "read_file" in reader


@pytest.mark.parametrize("role", ["coder", "devops", "reader"])
def test_native_role_prompt_drops_the_json_protocol(role):
    original = agent_model._load_role_prompt(role)
    native = native_role_prompt(original, agent_model._load_native_channel_section())

    assert "RESPONSE CHANNEL: TOOL CALLS" in native
    assert "Return only one valid JSON object" not in native
    assert "RESPONSE FORMAT ==" not in native
    assert "EXAMPLES ==" not in native
    assert "ARGS SCHEMAS ==" not in native
    assert "ALLOWED ACTIONS ==" in native
    assert "UNTRUSTED" in native  # the rest of the prompt survives


def test_calls_become_the_json_protocol_commands():
    turn = NativeTurn(
        content="Reading the installer",
        tool_calls=[
            ToolCall("c1", "read_file", json.dumps({"path": "installer/install.py"})),
            ToolCall("c2", "search_code", json.dumps({"query": "vsix"})),
        ],
    )

    commands = commands_from_turn(turn, files_changed=False)

    assert [command.action.type for command in commands] == ["read_file", "search_code"]
    assert commands[0].user_message == "Reading the installer"
    assert commands[0].plan_step == "inspect"
    assert commands[0].action.description == "install.py"


def test_bad_arguments_are_a_validation_error():
    with pytest.raises(CommandValidationError):
        commands_from_turn(NativeTurn("", tool_calls=[ToolCall("c1", "read_file", "{not json")]), files_changed=False)
    with pytest.raises(CommandValidationError):
        commands_from_turn(NativeTurn("", tool_calls=[ToolCall("c1", "read_file", "{}")]), files_changed=False)


def test_text_without_calls_is_a_final_answer():
    commands = final_answer_from_turn(NativeTurn("Nothing to change: the code already does this."))

    assert commands[0].action.type == "final_answer"
    assert commands[0].plan_step == "done"


def test_compaction_elides_oldest_tool_output_first():
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "task"},
        {"role": "tool", "tool_call_id": "a", "content": "x " * 500},
        {"role": "tool", "tool_call_id": "b", "content": "y " * 500},
    ]

    elided = compact_history(messages, lambda text: len(text.split()), budget=600)

    assert elided == 1
    assert messages[2]["content"] == ELIDED_OUTPUT
    assert messages[3]["content"].startswith("y")


def test_probe_decides_support_once_per_process(agent_factory):
    agent = _native_agent(agent_factory, [])

    assert agent_model.native_tools_supported(agent, "m") is True
    assert agent_model.native_tools_supported(agent, "m") is True
    assert len(agent.client.requests) == 1


def test_probe_answered_in_prose_means_no_support(agent_factory):
    agent = agent_factory(max_steps=1)  # FakeLLMClient answers with text

    assert agent_model.native_tools_supported(agent, "m") is False


def test_kill_switch_skips_the_probe(agent_factory, monkeypatch):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")
    agent = _native_agent(agent_factory, [])

    assert agent_model.native_tools_supported(agent, "m") is False
    assert agent.client.requests == []


def test_native_run_keeps_one_conversation_and_answers_every_call(agent_factory):
    turns = [
        _response("Reading a.py", [_call("c1", "read_file", path="a.py")], reasoning="think one"),
        _response("", [_call("c2", "final_answer", message="Done reading.", outcome="incomplete")]),
    ]
    agent = _native_agent(agent_factory, turns)
    (agent.executor.workspace / "a.py").write_text("MARKER = 1\n", encoding="utf-8")

    result = agent._run_loop("read a.py", start_step=1)

    assert result == "Done reading."
    second_call = agent.client.requests[-1]["messages"]
    assert second_call[0]["role"] == "system" and "RESPONSE CHANNEL: TOOL CALLS" in second_call[0]["content"]
    assert second_call[2]["tool_calls"][0]["id"] == "c1"
    assert second_call[2]["reasoning_content"] == "think one"
    assert second_call[3]["role"] == "tool" and second_call[3]["tool_call_id"] == "c1"
    assert "MARKER = 1" in second_call[3]["content"]
    assert "response_format" not in agent.client.requests[-1]


def test_calls_after_a_refused_one_are_answered_not_run(agent_factory):
    turns = [
        _response("", [_call("c1", "read_file", path="missing.py"), _call("c2", "read_file", path="b.py")]),
        _response("", [_call("c3", "final_answer", message="Stopping.", outcome="incomplete")]),
    ]
    agent = _native_agent(agent_factory, turns)

    agent._run_loop("read files", start_step=1)

    answers = [m for m in agent.client.requests[-1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in answers] == ["c1", "c2"]
    assert answers[1]["content"].startswith("Not executed")


def test_rejected_tools_fall_back_to_the_json_protocol(agent_factory):
    agent = _native_agent(agent_factory, [RuntimeError("Error code: 400 - tools is not supported for this model")])
    json_answer = {
        "thought": "t", "plan_step": "done", "user_message": "m",
        "actions": [{"type": "final_answer", "args": {"message": "Fell back.", "outcome": "incomplete"}}],
    }
    agent._call_action_model = lambda prompt: json.dumps(json_answer)

    result = agent._run_loop("anything", start_step=1)

    assert result == "Fell back."
    assert agent._state.native_tools is False
    assert agent_model.native_tools_supported(agent, agent._state.selected_action_model) is False


def test_streamed_tool_call_fragments_are_reassembled(agent_factory):
    agent = agent_factory(max_steps=1)

    def chunk(**delta):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**delta))])

    def fragment(index, call_id=None, name=None, arguments=None):
        return SimpleNamespace(index=index, id=call_id, function=SimpleNamespace(name=name, arguments=arguments))

    stream = [
        chunk(reasoning_content="let me look", content=None, tool_calls=None),
        chunk(content="Looking", tool_calls=None),
        chunk(content=None, tool_calls=[fragment(0, "c1", "read_file", '{"pa')]),
        chunk(content=None, tool_calls=[fragment(0, None, "read_file", 'th": "a.py"}')]),
        chunk(content=None, tool_calls=[fragment(1, "c2", "git_status", "{}")]),
    ]

    turn = agent_model._native_turn_from_stream(agent, iter(stream))

    assert turn.content == "Looking"
    assert turn.echo_reasoning and turn.reasoning == "let me look"
    assert [(c.call_id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("c1", "read_file", '{"path": "a.py"}'),
        ("c2", "git_status", "{}"),
    ]
