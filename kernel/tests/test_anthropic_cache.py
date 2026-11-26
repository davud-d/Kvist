# -*- coding: utf-8 -*-
"""Prompt-cache breakpoints on Anthropic's own endpoint: at most four, where the stable prefixes end.

Every path that reaches the Anthropic client is driven through its real code
and read back from the request the (fake) SDK received. The text protocol's
system prompt must be the same bytes on every step of a run.
"""
from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any, Callable

import pytest

from kernel.agent import agent_model
from kernel.agent.agent import LocalAgent
from kernel.agent.agent_model import build_role_system_prompt, call_action_model, call_native_action_model
from kernel.agent.agent_state import AgentConfig
from kernel.context.system_context import instructions_filename
from kernel.llm.llm_clients import AnthropicCompatibleClient, _cached_system_blocks, _with_prompt_cache
from kernel.llm.prompt_prefix import SELECTED_STYLE_MARKER, split_style_tail
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import NullPerfLogger, NullUI

MARK = {"type": "ephemeral"}
MODEL = "claude-sonnet-5-5"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    },
    {"type": "function", "function": {"name": "git_status", "description": "Status.", "parameters": {"type": "object", "properties": {}}}},
]


class _Sdk:
    """The Anthropic SDK's ``messages`` namespace: records each request, answers with one text block."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="ok")], stop_reason="end_turn", usage=None)


def _agent(tmp_path, *, style: str = "Default", base_url: str | None = None, **config: Any) -> tuple[LocalAgent, _Sdk]:
    client = AnthropicCompatibleClient(api_key="test-key", base_url=base_url)
    sdk = _Sdk()
    client._client = sdk
    agent = LocalAgent(
        client=client,
        executor=SafeExecutor(tmp_path),
        config=AgentConfig(
            persist_logs=False, api_request_interval=0, retry_base_delay=0, provider="anthropic",
            fast_model=MODEL, smart_model=MODEL, stream=False, cache_enabled=False, **config,
        ),
        ui=NullUI(),
    )
    agent._perf_logger = NullPerfLogger()
    agent._session_styles = {"agents": style, "chat": "Default"}
    agent._state.current_agent_role = "coder"
    return agent, sdk


def _marks(request: dict[str, Any]) -> tuple[int, int, int]:
    """(tools, system blocks, message blocks) carrying a breakpoint in one request."""
    tools = sum("cache_control" in tool for tool in request.get("tools", []))
    system = request.get("system", "")
    system_marks = 0 if isinstance(system, str) else sum("cache_control" in block for block in system)
    message_marks = sum(
        "cache_control" in block
        for message in request["messages"]
        if isinstance(message["content"], list)
        for block in message["content"]
    )
    return tools, system_marks, message_marks


def _history(*, results: int = 1) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": "task"}]
    for index in range(results):
        call = {"id": f"c{index}", "type": "function", "function": {"name": "read_file", "arguments": json.dumps({"path": "a"})}}
        messages.append({"role": "assistant", "content": "", "tool_calls": [call]})
        messages.append({"role": "tool", "tool_call_id": f"c{index}", "content": "file text"})
    return messages


# ── The adapter: the budget of four ──────────────────────────────────────────

_STYLED = f"shared rules\n\n{SELECTED_STYLE_MARKER}\n\nbe terse"


@pytest.mark.parametrize("with_tools", [False, True])
@pytest.mark.parametrize("many_messages", [False, True])
@pytest.mark.parametrize("system", [None, "plain system", _STYLED, f"{SELECTED_STYLE_MARKER}\n\nonly a style"])
def test_the_marks_of_a_direct_request_never_exceed_four_in_any_combination(with_tools, many_messages, system):
    client = AnthropicCompatibleClient(api_key="test-key")
    sdk = _Sdk()
    client._client = sdk
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}] if system is not None else []
    messages += _history() if many_messages else [{"role": "user", "content": "task"}]
    kwargs: dict[str, Any] = {"tools": TOOLS} if with_tools else {}

    client.chat.completions.create(model=MODEL, messages=messages, **kwargs)

    tools, system_marks, message_marks = _marks(sdk.requests[0])
    expected_system = 0 if system is None else 2 if system == _STYLED else 1
    assert (tools, system_marks, message_marks) == (int(with_tools), expected_system, int(many_messages))
    assert tools + system_marks + message_marks <= 4


def test_the_fullest_request_carries_exactly_four_marks_in_the_order_of_the_prefix():
    client = AnthropicCompatibleClient(api_key="test-key")
    sdk = _Sdk()
    client._client = sdk

    client.chat.completions.create(
        model=MODEL, messages=[{"role": "system", "content": _STYLED}, *_history(results=2)], tools=TOOLS,
    )

    request = sdk.requests[0]
    assert "cache_control" not in request["tools"][0]
    assert request["tools"][-1]["cache_control"] == MARK
    assert [block["cache_control"] for block in request["system"]] == [MARK, MARK]
    assert request["messages"][-1]["content"][-1]["cache_control"] == MARK
    assert _marks(request) == (1, 2, 1)


def test_a_system_with_a_style_tail_becomes_a_shared_block_and_a_tail_block():
    shared, tail = split_style_tail(_STYLED)

    blocks = _cached_system_blocks(_STYLED)

    assert [block["text"] for block in blocks] == [shared, tail]
    assert "".join(block["text"] for block in blocks) == _STYLED
    assert all(block["cache_control"] == MARK for block in blocks)


def test_a_system_without_the_marker_stays_one_marked_block():
    assert _cached_system_blocks("plain") == [{"type": "text", "text": "plain", "cache_control": MARK}]


def test_a_system_that_is_all_style_is_not_split_into_an_empty_block():
    system = f"{SELECTED_STYLE_MARKER}\n\nstyle only"

    assert _cached_system_blocks(system) == [{"type": "text", "text": system, "cache_control": MARK}]


def test_the_history_the_caller_passed_is_not_mutated_by_the_marks():
    history_block = {"type": "text", "text": "second"}
    messages = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": [history_block]},
    ]

    _system, marked = _with_prompt_cache(_STYLED, messages)

    assert "cache_control" not in history_block
    assert marked[-1]["content"][-1]["cache_control"] == MARK


@pytest.mark.parametrize("base_url", ["https://gateway.example.com", "https://proxy.example.com/anthropic"])
def test_a_gateway_gets_no_marks_on_a_styled_system_with_tools_and_history(base_url):
    client = AnthropicCompatibleClient(api_key="test-key", base_url=base_url)
    sdk = _Sdk()
    client._client = sdk

    client.chat.completions.create(
        model=MODEL, messages=[{"role": "system", "content": _STYLED}, *_history(results=2)], tools=TOOLS,
    )

    request = sdk.requests[0]
    assert "cache_control" not in str(request)
    assert request["system"] == _STYLED


# ── Executors that differ only in style ──────────────────────────────────────


def test_two_executors_with_different_styles_send_the_same_marked_first_system_block(tmp_path):
    first, first_sdk = _agent(tmp_path, style="Senior Dev")
    second, second_sdk = _agent(tmp_path, style="Minimalist")

    for agent in (first, second):
        agent._state.native_tool_defs = TOOLS
        system = build_role_system_prompt(agent, "coder", native_tools=True)
        call_native_action_model(agent, [{"role": "system", "content": system}, {"role": "user", "content": "task"}], TOOLS)

    one, two = first_sdk.requests[0], second_sdk.requests[0]
    assert one["system"][0] == two["system"][0]
    assert one["system"][0]["cache_control"] == MARK
    assert one["system"][1]["text"].startswith(SELECTED_STYLE_MARKER)
    assert one["system"][1]["text"] != two["system"][1]["text"]
    assert one["tools"] == two["tools"]


# ── Every path to the client ─────────────────────────────────────────────────


def _native_first(agent, tmp_path):
    system = build_role_system_prompt(agent, "coder", native_tools=True)
    call_native_action_model(agent, [{"role": "system", "content": system}, {"role": "user", "content": "task"}], TOOLS)


def _native_later(agent, tmp_path):
    system = build_role_system_prompt(agent, "coder", native_tools=True)
    call_native_action_model(agent, [{"role": "system", "content": system}, *_history(results=2)], TOOLS)


def _text_step(agent, tmp_path):
    call_action_model(agent, "the runtime prompt")


def _chat_turn(agent, tmp_path):
    agent._state.current_agent_role = "chat"
    agent._state.user_goal = "hello"
    agent._run_chat("hello")


def _chat_later_turn(agent, tmp_path):
    _chat_turn(agent, tmp_path)
    agent._run_chat("and one more thing")


def _router(agent, tmp_path):
    from kernel.agent.role_router import route_role_decision_with_llm

    route_role_decision_with_llm("hmm, how do things look around here?", agent.client, MODEL)


def _title(agent, tmp_path):
    from kernel.session.session_titler import generate_session_title

    generate_session_title(agent.client, agent.config, "fix the parser", "done, the parser is fixed")


def _narrator(agent, tmp_path):
    from kernel.agent.narrator import Narrator

    Narrator(agent)._ask("the model is thinking about the parser")


def _memory(agent, tmp_path):
    from kernel.agent.agent_tools import memory_model

    memory_model(agent).ask([{"role": "system", "content": "extract"}, {"role": "user", "content": "turns"}])


def _classifier(agent, tmp_path):
    from kernel.agent.agent_tools import auto_classifier
    from kernel.sandbox.auto_approval import Question

    auto_classifier(agent).classify([Question("command", "ls -la")])


def _skill_service(agent, tmp_path):
    from kernel.plugins.services import _complete

    _complete(agent, MODEL, [{"role": "system", "content": "skill rules"}, {"role": "user", "content": "ask"}], 256, owner="t")


def _vision(agent, tmp_path):
    from kernel.vision.vision import describe_images

    image = {"name": "a.png", "data_url": "data:image/png;base64,iVBORw0KGgo="}
    describe_images(agent, [image], "what is this")


def _commit_message(agent, tmp_path):
    from kernel.agent.agent_model import _create_json_completion, _load_role_prompt

    messages = [{"role": "system", "content": _load_role_prompt("commit")}, {"role": "user", "content": "diff"}]
    _create_json_completion(agent, MODEL, messages, stream=False, json_mode=False)


def _doctor_probe(agent, tmp_path):
    from kernel.agent.agent_model import _create_json_completion

    _create_json_completion(agent, MODEL, [{"role": "user", "content": "Reply with pong."}], stream=False, json_mode=False)


def _inline_completion(agent, tmp_path):
    from kernel.ide.completion import _request_completion

    messages = [{"role": "system", "content": "complete the code"}, {"role": "user", "content": "def f("}]
    _request_completion(SimpleNamespace(client=agent.client, config=agent.config), MODEL, messages, False)


# (driver, request index, expected marks on (tools, system blocks, message blocks)).
_PATHS = {
    "native step, first": (_native_first, 0, (1, 2, 0)),
    "native step, with results": (_native_later, 0, (1, 2, 1)),
    "text step": (_text_step, 0, (0, 2, 0)),
    "chat, first message": (_chat_turn, 0, (0, 2, 0)),
    "chat, later message": (_chat_later_turn, 1, (0, 2, 1)),
    "role router": (_router, 0, (0, 1, 0)),
    "session title": (_title, 0, (0, 1, 0)),
    "narrator": (_narrator, 0, (0, 1, 0)),
    "memory extractor": (_memory, 0, (0, 1, 0)),
    "permission classifier": (_classifier, 0, (0, 1, 0)),
    "skill model service": (_skill_service, 0, (0, 1, 0)),
    "vision": (_vision, 0, (0, 0, 0)),
    "commit message": (_commit_message, 0, (0, 1, 0)),
    "connection probe": (_doctor_probe, 0, (0, 0, 0)),
    "inline completion": (_inline_completion, 0, (0, 1, 0)),
}


@pytest.mark.parametrize("path", list(_PATHS))
def test_each_path_to_the_client_marks_what_it_should_and_stays_within_four(tmp_path, path):
    driver, index, expected = _PATHS[path]
    agent, sdk = _agent(tmp_path)

    driver(agent, tmp_path)

    assert _marks(sdk.requests[index]) == expected
    assert sum(expected) <= 4


@pytest.mark.parametrize("path", list(_PATHS))
def test_no_path_marks_anything_on_a_gateway(tmp_path, path):
    driver, _index, _expected = _PATHS[path]
    agent, sdk = _agent(tmp_path, base_url="https://gateway.example.com")

    driver(agent, tmp_path)

    assert sdk.requests
    assert all("cache_control" not in str(request) for request in sdk.requests)


def test_the_classifiers_second_call_extends_the_first_so_its_prefix_can_be_read_from_the_cache(tmp_path):
    from kernel.agent.agent_tools import auto_classifier
    from kernel.sandbox.auto_approval import Question

    agent, sdk = _agent(tmp_path)
    classifier = auto_classifier(agent)

    classifier.classify([Question("command", "ls -la")])
    classifier.classify([Question("command", "pwd")])

    first, second = sdk.requests
    assert _marks(second) == (0, 1, 1)
    assert second["system"] == first["system"]
    assert second["messages"][0]["content"] == first["messages"][0]["content"]
    assert len(second["messages"]) == 3


def test_the_chat_system_prompt_is_split_at_its_style(tmp_path):
    agent, sdk = _agent(tmp_path)

    _chat_turn(agent, tmp_path)

    shared, tail = split_style_tail(build_role_system_prompt(agent, "chat"))
    assert [block["text"] for block in sdk.requests[0]["system"]] == [shared, tail]


# ── The text protocol's system prompt, byte for byte between steps ───────────


def _text_systems(sdk: _Sdk) -> list[str]:
    return ["".join(block["text"] for block in request["system"]) for request in sdk.requests]


def test_three_text_steps_send_the_same_system_even_after_the_instructions_and_the_plan_phase_changed(tmp_path):
    agent, sdk = _agent(tmp_path)
    instructions = tmp_path / instructions_filename("coder")
    instructions.write_text("# Project\n\nUse tabs.\n", encoding="utf-8")

    call_action_model(agent, "step one")
    instructions.write_text("# Project\n\nUse spaces, and run the linter.\n", encoding="utf-8")
    agent._state.plan_phase = "planning"
    call_action_model(agent, "step two")
    agent._state.plan_phase = ""
    call_action_model(agent, "step three")

    first, second, third = _text_systems(sdk)
    assert first == second == third
    assert "Use tabs." in first
    assert "Use spaces" not in first


def test_the_plan_section_is_the_one_the_run_started_with(tmp_path):
    agent, sdk = _agent(tmp_path)
    agent._state.plan_phase = "planning"

    call_action_model(agent, "step one")
    agent._state.plan_phase = ""
    call_action_model(agent, "step two")

    first, second = _text_systems(sdk)
    assert first == second
    assert "plan mode" in first


def test_the_next_run_picks_up_what_changed_in_the_instructions(tmp_path):
    from kernel.agent.agent_runtime import reset_run_state

    agent, sdk = _agent(tmp_path)
    instructions = tmp_path / instructions_filename("coder")
    instructions.write_text("# Project\n\nUse tabs.\n", encoding="utf-8")
    call_action_model(agent, "step one")
    instructions.write_text("# Project\n\nUse spaces.\n", encoding="utf-8")

    reset_run_state(agent)
    agent._state.current_agent_role = "coder"
    call_action_model(agent, "first step of the next run")

    first, second = _text_systems(sdk)
    assert "Use tabs." in first
    assert "Use spaces." in second


def test_a_change_of_role_or_of_model_builds_the_system_again(tmp_path, monkeypatch):
    agent, sdk = _agent(tmp_path)
    built: list[str] = []
    real = agent_model.build_role_system_prompt
    monkeypatch.setattr(agent_model, "build_role_system_prompt", lambda self, role, **kw: built.append(role) or real(self, role, **kw))

    call_action_model(agent, "one")
    call_action_model(agent, "two")
    agent._state.current_agent_role = "reader"
    call_action_model(agent, "three")
    call_action_model(agent, "four")
    agent._state.selected_action_model = "claude-opus-5-5"
    call_action_model(agent, "five")

    assert built == ["coder", "reader", "reader"]


def test_leaving_the_native_channel_builds_the_text_system_once_and_it_stays_the_same(tmp_path, monkeypatch):
    from kernel.agent.agent_runtime import _leave_native_tools

    agent, sdk = _agent(tmp_path)
    agent._state.selected_action_model = MODEL
    native_system = build_role_system_prompt(agent, "coder", native_tools=True)
    agent._state.native_tools = True
    agent._state.native_messages = [{"role": "system", "content": native_system}]
    built: list[bool] = []
    real = agent_model.build_role_system_prompt
    monkeypatch.setattr(
        agent_model, "build_role_system_prompt",
        lambda self, role, **kw: built.append(kw.get("native_tools", False)) or real(self, role, **kw),
    )

    _leave_native_tools(agent, RuntimeError("tools are not supported"))
    call_action_model(agent, "text one")
    call_action_model(agent, "text two")
    call_action_model(agent, "text three")

    first, second, third = _text_systems(sdk)
    assert first == second == third
    assert first != native_system
    assert built == [False]


def test_the_static_part_of_the_runtime_prompt_is_the_same_on_every_step_of_a_run(tmp_path):
    agent, _sdk = _agent(tmp_path)
    agent._state.response_language = "en"
    agent._set_environment_facts()

    first = agent._build_runtime_prompt("fix the failing test", 1)
    second = agent._build_runtime_prompt("fix the failing test", 2)

    common = len(first) - len(first.lstrip())
    while common < min(len(first), len(second)) and first[common] == second[common]:
        common += 1
    assert first[:common].endswith("fix the failing test\n\nstep: ")
