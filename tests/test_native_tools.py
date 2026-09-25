from __future__ import annotations

import json
import time
from types import SimpleNamespace
from typing import get_args

import pytest

import agent_model
from agent_state import NO_PLAN_STEP, PlanInfo
from native_tools import (
    ACTION_TOOLS,
    ELIDED_OUTPUT,
    OPENING_NOTE,
    NativeTurn,
    READONLY_TOOLS,
    ToolCall,
    calls_from_turn,
    compact_history,
    native_role_prompt,
    tools_for_role,
)
from parser import CommandValidationError, build_command
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


def test_silent_thinking_counts_only_when_it_is_long_enough():
    assert agent_model._thinking_seconds(20.0, reasoning_shown=False) == 20.0
    assert agent_model._thinking_seconds(1.2, reasoning_shown=False) == 0.0
    assert agent_model._thinking_seconds(1.2, reasoning_shown=True) == 1.2


def test_every_action_has_a_tool_definition():
    assert set(ACTION_TOOLS) == set(get_args(ActionType))


def test_tools_follow_the_role_prompt_allowed_actions():
    coder = {tool["function"]["name"] for tool in tools_for_role(agent_model._load_role_prompt("coder"))}
    reader = {tool["function"]["name"] for tool in tools_for_role(agent_model._load_role_prompt("reader"))}

    assert {"write_file", "replace_in_file", "run_shell", "refuse"} <= coder
    # A response without a call ends the turn, so no finishing tool is offered.
    assert not coder & {"final_answer", "report_to_user"}
    assert not reader & {"write_file", "append_file", "replace_in_file"}
    assert "read_file" in reader


@pytest.mark.parametrize("role", ["coder", "devops", "reader"])
def test_native_role_prompt_drops_the_json_protocol(role):
    original = agent_model._load_role_prompt(role)
    native = native_role_prompt(original, agent_model._load_native_channel_section())

    assert "RESPONSE CHANNEL: TOOL CALLS" in native
    assert "Return only one valid JSON object" not in native
    assert "No chat" not in native
    assert "RESPONSE FORMAT ==" not in native
    assert "EXAMPLES ==" not in native
    assert "ARGS SCHEMAS ==" not in native
    assert "ALLOWED ACTIONS ==" in native
    assert "UNTRUSTED" in native  # the rest of the prompt survives


def _commands(turn: NativeTurn) -> list:
    return [
        build_command(call, text=turn.content, batch_id="b", plan_step="inspect", language="en")
        for call in calls_from_turn(turn)
    ]


def test_calls_become_call_requests_in_order():
    turn = NativeTurn(
        content="Reading the installer",
        tool_calls=[
            ToolCall("c1", "read_file", json.dumps({"path": "installer/install.py"})),
            ToolCall("c2", "search_code", json.dumps({"query": "vsix"})),
        ],
    )

    commands = _commands(turn)

    assert [command.action.type for command in commands] == ["read_file", "search_code"]
    assert [command.call_id for command in commands] == ["c1", "c2"]
    assert commands[0].text == "Reading the installer"
    assert [command.action.description for command in commands] == [
        "reads installer/install.py",
        'searches for "vsix"',
    ]


def test_argumentless_tools_ask_for_a_purpose_and_drop_it_before_running():
    tools = {tool["function"]["name"]: tool["function"] for tool in tools_for_role(agent_model._load_role_prompt("coder"))}

    assert tools["git_status"]["parameters"]["required"] == ["purpose"]
    assert tools["git_diff"]["parameters"]["required"] == ["purpose"]
    assert "purpose" not in tools["read_file"]["parameters"]["properties"]

    turn = NativeTurn("", tool_calls=[ToolCall("c1", "git_status", json.dumps({"purpose": "see what changed"}))])

    assert _commands(turn)[0].action.args == {}


def test_shell_asks_for_a_purpose_that_becomes_its_label():
    tools = {tool["function"]["name"]: tool["function"] for tool in tools_for_role(agent_model._load_role_prompt("coder"))}

    assert tools["run_shell"]["parameters"]["required"] == ["command", "purpose"]
    turn = NativeTurn("", tool_calls=[ToolCall("c1", "run_shell", json.dumps({"command": ["pytest"], "purpose": "run the parser tests"}))])

    (command,) = _commands(turn)
    assert command.action.args == {"command": ["pytest"]}
    assert command.action.description == "run the parser tests"


def test_bad_arguments_cost_only_their_own_call():
    calls = calls_from_turn(
        NativeTurn(
            "",
            tool_calls=[
                ToolCall("c1", "read_file", "{not json"),
                ToolCall("c2", "read_file", json.dumps({"path": "a.py"})),
            ],
        )
    )

    assert "not valid JSON" in calls[0].error and calls[0].arguments is None
    assert (calls[1].arguments, calls[1].error) == ({"path": "a.py"}, "")
    with pytest.raises(CommandValidationError):
        build_command(calls[0], text="", batch_id="b", plan_step="inspect", language="en")


def test_registry_marks_only_side_effect_free_tools_readonly():
    assert READONLY_TOOLS == {
        "read_file", "read_multiple_files", "list_dir", "list_tree",
        "find_files", "search_code", "web_search", "fetch_url",
    }


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
    assert second_call[1]["content"].endswith(OPENING_NOTE)
    assert second_call[2]["tool_calls"][0]["id"] == "c1"
    assert second_call[2]["reasoning_content"] == "think one"
    assert second_call[3]["role"] == "tool" and second_call[3]["tool_call_id"] == "c1"
    assert "MARKER = 1" in second_call[3]["content"]
    assert "response_format" not in agent.client.requests[-1]


def test_a_fix_run_hears_how_many_calls_changed_nothing(agent_factory):
    from agent_runtime import _note_unchanged_run

    agent = _native_agent(agent_factory, [])
    state = agent._state
    state.modify_goal = True
    state.native_messages = [{"role": "tool", "tool_call_id": str(i), "content": "x"} for i in range(10)]

    _note_unchanged_run(state)
    _note_unchanged_run(state)

    notes = [m["content"] for m in state.native_messages if m["role"] == "user"]
    assert notes == ["Run so far: 10 tool calls, and none of them changed a file."]


def test_an_analysis_run_is_not_told_it_only_reads(agent_factory):
    from agent_runtime import _note_unchanged_run

    agent = _native_agent(agent_factory, [])
    state = agent._state
    state.native_messages = [{"role": "tool", "tool_call_id": str(i), "content": "x"} for i in range(30)]

    _note_unchanged_run(state)

    assert all(m["role"] == "tool" for m in state.native_messages)


def test_a_failed_read_does_not_stop_the_next_one(agent_factory):
    turns = [
        _response("", [_call("c1", "read_file", path="missing.py"), _call("c2", "read_file", path="b.py")]),
        _response("", [_call("c3", "final_answer", message="Stopping.", outcome="incomplete")]),
    ]
    agent = _native_agent(agent_factory, turns)
    (agent.executor.workspace / "b.py").write_text("B = 2\n", encoding="utf-8")

    agent._run_loop("read files", start_step=1)

    answers = [m for m in agent.client.requests[-1]["messages"] if m["role"] == "tool"]
    assert [m["tool_call_id"] for m in answers] == ["c1", "c2"]
    assert 'call_id="c1"' in answers[0]["content"] and '"ok": false' in answers[0]["content"]
    assert 'call_id="c2"' in answers[1]["content"] and "B = 2" in answers[1]["content"]


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

    shown: list[str] = []
    agent._ui.update_live = lambda **kwargs: shown.append(kwargs["user_message"])
    agent._state.response_language = "ru"

    started = time.monotonic() - 40

    turn = agent_model._native_turn_from_stream(agent, iter(stream), started)

    assert shown and shown[0].startswith("Пишет read_file")
    # Thinking is timed from the request, not from the first reasoning fragment.
    assert agent._last_reasoning_seconds >= 40
    agent._state.response_language = "en"
    shown.clear()
    agent_model._native_turn_from_stream(agent, iter(stream), time.monotonic())
    assert shown and shown[0].startswith("Writing read_file")
    assert turn.content == "Looking"
    assert turn.echo_reasoning and turn.reasoning == "let me look"
    assert [(c.call_id, c.name, c.arguments) for c in turn.tool_calls] == [
        ("c1", "read_file", '{"path": "a.py"}'),
        ("c2", "git_status", "{}"),
    ]


class _FlakyProbeClient:
    """Fails the first probe with ``error``, then answers it with a ping call."""

    def __init__(self, error: Exception):
        self.error = error
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if len(self.requests) == 1:
            raise self.error
        return _response(calls=[_call("probe", "ping", ok=True)])


@pytest.mark.parametrize(
    "error",
    [TimeoutError("Request timed out."), ConnectionError("Connection reset by peer"), RuntimeError("Error code: 503 - upstream unavailable")],
)
def test_a_probe_lost_to_the_network_is_asked_again_next_run(agent_factory, error):
    agent = agent_factory(max_steps=1)
    agent.client = _FlakyProbeClient(error)

    assert agent_model.native_tools_supported(agent, "m") is False
    assert not agent_model._NATIVE_TOOL_SUPPORT
    assert agent_model.native_tools_supported(agent, "m") is True
    assert len(agent.client.requests) == 2


def test_an_explicit_refusal_of_tools_is_remembered(agent_factory):
    agent = agent_factory(max_steps=1)
    agent.client = _FlakyProbeClient(RuntimeError("Error code: 400 - tools is not supported for this model"))

    assert agent_model.native_tools_supported(agent, "m") is False
    assert agent_model.native_tools_supported(agent, "m") is False
    assert len(agent.client.requests) == 1


@pytest.mark.parametrize(
    ("model", "sends"),
    [("gpt-6-astra", False), ("openai/gpt-6-astra", False), ("gpt-5.6-luna", False), ("o3-mini", False), ("mock-model", True)],
)
def test_native_turns_send_no_temperature_to_reasoning_models(agent_factory, model, sends):
    agent = _native_agent(agent_factory, [_response("Done.")])
    agent._state.selected_action_model = model
    agent._state.selected_action_role = "smart"

    agent._run_loop("say done", start_step=1)

    turn_request = agent.client.requests[-1]
    assert ("temperature" in turn_request) is sends


def test_a_temperature_refusal_is_learned_and_the_turn_asked_again(agent_factory, monkeypatch):
    monkeypatch.setattr(agent_model, "_TEMPERATURE_UNSUPPORTED", set())
    refusal = RuntimeError("Unsupported value: 'temperature' does not support 0.1 with this model.")
    agent = _native_agent(agent_factory, [refusal, _response("Done."), _response("Done.")])
    agent._state.selected_action_model = "custom-thinker"
    agent._state.selected_action_role = "smart"

    agent._run_loop("say done", start_step=1)

    turns = [request for request in agent.client.requests if request["tools"][0]["function"]["name"] != "ping"]
    assert "temperature" in turns[0]
    assert all("temperature" not in turn for turn in turns[1:])
    # The refusal cost a retry of the same turn, not a failed step.
    assert not any("temperature" in error for error in agent._state.error_history)
    assert not any(log.get("status") == "model_error" for log in agent._state.step_logs)
