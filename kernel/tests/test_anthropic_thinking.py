# -*- coding: utf-8 -*-
"""Signed thinking blocks of an Anthropic reply: captured, kept in the history, replayed to the model that wrote them.

The streams are the SDK's own ``MessageStream`` over recorded events, so the
signature is collected by the code that collects it in production. Nothing here
reaches a network.
"""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx
import pytest
from anthropic._models import construct_type
from anthropic._types import not_given
from anthropic.lib.streaming import MessageStream
from anthropic.types import RawMessageStreamEvent, RedactedThinkingBlock, TextBlock, ThinkingBlock, ToolUseBlock

from kernel.agent.agent import LocalAgent
from kernel.agent.agent_model import call_native_action_model
from kernel.agent.agent_state import AgentConfig
from kernel.agent.native_tools import assistant_message, tool_message
from kernel.llm import llm_clients
from kernel.llm.anthropic_wire import THINKING_KEY, history_to_anthropic
from kernel.llm.llm_clients import AnthropicCompatibleClient
from kernel.providers import chatgpt_client
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import NullPerfLogger, NullUI

MODEL = "claude-sonnet-5-5"
OTHER_MODEL = "claude-opus-5-5"
SIGNATURE = "EqQBCkYIAxgCKkAx9q+Zr7LmT3vNw0y/PfQ1d5Hk2JcB8aVtUo4Xg6EeRz1sMnWl7yKiDhOa3b9cFpTvXjQuGqLw0S2rYdZmN4A=="
OPAQUE = "EuoBCkYIAxgCKkCx7Qm0vLzK3tHnW8dRyFpU2sBaOe5Jc1XgTi6VhYq9NlMwDrEz4k+Sb0AfPj7uGoCtQvIxZ3LnHy8dKe2WmR1a"
SECRETISH = "sk-abcdefghijklmnopqrstuvwxyz123456"
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "Read a file.",
            "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
        },
    }
]


# ── recorded replies ───────────────────────────────────────────────────────


def _events(*, text: str = "Looking.", call: bool = True) -> list[Any]:
    """A reply as the API streams it: thinking in pieces, its signature, a redacted block, text, a call."""
    raw: list[dict[str, Any]] = [
        {"type": "message_start", "message": {
            "id": "msg_1", "type": "message", "role": "assistant", "model": MODEL, "content": [],
            "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 12, "output_tokens": 1}}},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "First, "}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": f"read it {SECRETISH}."}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": SIGNATURE}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "redacted_thinking", "data": OPAQUE}},
        {"type": "content_block_stop", "index": 1},
        {"type": "content_block_start", "index": 2, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 2, "delta": {"type": "text_delta", "text": text}},
        {"type": "content_block_stop", "index": 2},
    ]
    if call:
        raw += [
            {"type": "content_block_start", "index": 3, "content_block": {
                "type": "tool_use", "id": "toolu_1", "name": "read_file", "input": {}}},
            {"type": "content_block_delta", "index": 3, "delta": {"type": "input_json_delta", "partial_json": '{"path": "a.py"}'}},
            {"type": "content_block_stop", "index": 3},
        ]
    raw += [
        {"type": "message_delta", "delta": {"stop_reason": "tool_use" if call else "end_turn", "stop_sequence": None},
         "usage": {"output_tokens": 40}},
        {"type": "message_stop"},
    ]
    return [construct_type(type_=RawMessageStreamEvent, value=event) for event in raw]


EXPECTED_BLOCKS = [
    {"type": "thinking", "thinking": f"First, read it {SECRETISH}.", "signature": SIGNATURE},
    {"type": "redacted_thinking", "data": OPAQUE},
]


def _whole_reply(*, call: bool = True) -> Any:
    content: list[Any] = [
        ThinkingBlock(type="thinking", thinking=f"First, read it {SECRETISH}.", signature=SIGNATURE),
        RedactedThinkingBlock(type="redacted_thinking", data=OPAQUE),
        TextBlock(type="text", text="Looking."),
    ]
    if call:
        content.append(ToolUseBlock(type="tool_use", id="toolu_1", name="read_file", input={"path": "a.py"}))
    return SimpleNamespace(content=content, stop_reason="tool_use" if call else "end_turn", usage=None)


class _RawStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = iter(events)

    def __iter__(self) -> "_RawStream":
        return self

    def __next__(self) -> Any:
        return next(self._events)

    def close(self) -> None:
        return None


class _Sdk:
    """The SDK's ``messages`` namespace: records each request and answers from a queue."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.replies: list[Any] = []
        self.messages = SimpleNamespace(create=self._create, stream=self._stream)

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(json.loads(json.dumps(kwargs)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply

    @contextmanager
    def _stream(self, **kwargs: Any):
        self.requests.append(json.loads(json.dumps(kwargs)))
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        yield MessageStream(_RawStream(reply), not_given)


def _client() -> tuple[AnthropicCompatibleClient, _Sdk]:
    client = AnthropicCompatibleClient(api_key="test-key")
    sdk = _Sdk()
    client._client = sdk
    return client, sdk


def _agent(tmp_path, *, stream: bool, provider: str = "anthropic") -> tuple[LocalAgent, _Sdk]:
    client, sdk = _client()
    agent = LocalAgent(
        client=client,
        executor=SafeExecutor(tmp_path),
        config=AgentConfig(
            persist_logs=False, api_request_interval=0, retry_base_delay=0, provider=provider,
            fast_model=MODEL, smart_model=MODEL, stream=stream, cache_enabled=False, reasoning_effort="high",
        ),
        ui=NullUI(),
    )
    agent._perf_logger = NullPerfLogger()
    agent._session_styles = {"agents": "Default", "chat": "Default"}
    agent._state.current_agent_role = "coder"
    agent._state.selected_action_model = MODEL
    return agent, sdk


def _ask(client: AnthropicCompatibleClient, *, stream: bool = False, **kwargs: Any) -> Any:
    return client.chat.completions.create(
        model=MODEL, messages=[{"role": "user", "content": "hi"}], tools=TOOLS, stream=stream, **kwargs
    )


# ── capture ────────────────────────────────────────────────────────────────


def test_a_stream_hands_over_the_blocks_in_order_with_the_signature_whole():
    client, sdk = _client()
    sdk.replies.append(_events())

    chunks = list(_ask(client, stream=True))

    assert chunks[-1].thinking_blocks == EXPECTED_BLOCKS
    shown = "".join(
        chunk.choices[0].delta.reasoning_content
        for chunk in chunks
        if chunk.choices and getattr(chunk.choices[0].delta, "reasoning_content", None)
    )
    assert shown == f"First, read it {SECRETISH}."


def test_a_whole_response_hands_over_the_same_blocks():
    client, sdk = _client()
    sdk.replies.append(_whole_reply())

    response = _ask(client)

    assert response.choices[0].message.thinking_blocks == EXPECTED_BLOCKS


def test_a_thinking_block_a_gateway_left_unsigned_is_not_kept():
    client, sdk = _client()
    unsigned = ThinkingBlock(type="thinking", thinking="no signature", signature="")
    sdk.replies.append(SimpleNamespace(content=[unsigned, TextBlock(type="text", text="ok")], stop_reason="end_turn", usage=None))

    assert _ask(client).choices[0].message.thinking_blocks == []


def test_a_reply_without_thinking_carries_no_blocks():
    client, sdk = _client()
    sdk.replies.append(SimpleNamespace(content=[TextBlock(type="text", text="ok")], stop_reason="end_turn", usage=None))

    assert _ask(client).choices[0].message.thinking_blocks == []


# ── the wire ───────────────────────────────────────────────────────────────


def _assistant_with_blocks(model: str = MODEL, provider: str = "anthropic") -> dict[str, Any]:
    return {
        "role": "assistant",
        "content": "Looking.",
        "tool_calls": [{"id": "toolu_1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}}],
        THINKING_KEY: {"provider": provider, "model": model, "blocks": EXPECTED_BLOCKS},
    }


def test_the_blocks_go_first_and_unchanged_to_the_model_that_wrote_them():
    sent = history_to_anthropic([{"role": "user", "content": "go"}, _assistant_with_blocks()], model=MODEL)

    content = sent[1]["content"]
    assert content[:2] == EXPECTED_BLOCKS
    assert json.dumps(content[:2]) == json.dumps(EXPECTED_BLOCKS)
    assert [block["type"] for block in content] == ["thinking", "redacted_thinking", "text", "tool_use"]


def test_another_model_gets_no_blocks():
    sent = history_to_anthropic([_assistant_with_blocks()], model=OTHER_MODEL)

    assert [block["type"] for block in sent[0]["content"]] == ["text", "tool_use"]


def test_an_answer_without_calls_keeps_its_blocks_in_front_of_the_text():
    answer = {**_assistant_with_blocks(), "content": "Done."}
    del answer["tool_calls"]

    sent = history_to_anthropic([answer], model=MODEL)

    assert [block["type"] for block in sent[0]["content"]] == ["thinking", "redacted_thinking", "text"]


def _history(*, blocks_on: tuple[bool, ...]) -> list[dict[str, Any]]:
    messages: list[dict[str, Any]] = [{"role": "user", "content": "go"}]
    for index, held in enumerate(blocks_on):
        call = {"id": f"c{index}", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a"}'}}
        message: dict[str, Any] = {"role": "assistant", "content": "", "tool_calls": [call]}
        if held:
            message[THINKING_KEY] = {"provider": "anthropic", "model": MODEL, "blocks": EXPECTED_BLOCKS}
        messages += [message, {"role": "tool", "tool_call_id": f"c{index}", "content": "text"}]
    return messages


def test_thinking_stays_on_when_every_replayed_tool_turn_has_its_blocks():
    client, sdk = _client()
    sdk.replies.append(_whole_reply(call=False))

    client.chat.completions.create(
        model=MODEL, messages=_history(blocks_on=(True, True)), tools=TOOLS, reasoning_effort="high"
    )

    request = sdk.requests[0]
    assert request["thinking"] == {"type": "adaptive"}
    assert [message["content"][0]["type"] for message in request["messages"] if message["role"] == "assistant"] == [
        "thinking",
        "thinking",
    ]


def test_thinking_is_turned_off_only_for_a_history_where_a_tool_turn_lost_its_blocks():
    client, sdk = _client()
    sdk.replies.append(_whole_reply(call=False))

    client.chat.completions.create(
        model=MODEL, messages=_history(blocks_on=(True, False)), tools=TOOLS, reasoning_effort="high"
    )

    assert "thinking" not in sdk.requests[0]


# ── the chain through the agent ────────────────────────────────────────────


def _first_turn(agent: LocalAgent) -> tuple[list[dict[str, Any]], Any]:
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "read a.py"},
    ]
    turn = call_native_action_model(agent, messages, TOOLS)
    messages.append(assistant_message(turn))
    messages.append(tool_message("toolu_1", "print(1)"))
    return messages, turn


@pytest.mark.parametrize("stream", [True, False])
def test_the_blocks_of_a_turn_come_back_word_for_word_on_the_next_request(tmp_path, stream):
    agent, sdk = _agent(tmp_path, stream=stream)
    sdk.replies.append(_events() if stream else _whole_reply())
    messages, turn = _first_turn(agent)
    sdk.replies.append(_events(call=False) if stream else _whole_reply(call=False))

    call_native_action_model(agent, messages, TOOLS)

    assert turn.thinking_blocks == EXPECTED_BLOCKS and turn.thinking_origin == ("anthropic", MODEL)
    assert messages[2][THINKING_KEY] == {"provider": "anthropic", "model": MODEL, "blocks": EXPECTED_BLOCKS}
    second = sdk.requests[1]
    assert "thinking" in second
    assistant = next(message for message in second["messages"] if message["role"] == "assistant")
    assert json.dumps(assistant["content"][:2]) == json.dumps(EXPECTED_BLOCKS)


def test_blocks_are_not_sent_to_another_model_and_that_step_runs_without_thinking(tmp_path):
    agent, sdk = _agent(tmp_path, stream=False)
    sdk.replies.append(_whole_reply())
    messages, _turn = _first_turn(agent)
    sdk.replies.append(_whole_reply(call=False))
    agent._state.selected_action_model = OTHER_MODEL
    agent.config.fast_model = agent.config.smart_model = OTHER_MODEL

    call_native_action_model(agent, messages, TOOLS)

    second = sdk.requests[1]
    assert second["model"] == OTHER_MODEL
    assert "thinking_blocks" not in json.dumps(second)
    assert SIGNATURE not in json.dumps(second)
    assert "thinking" not in second


def test_blocks_are_not_sent_under_another_provider_name(tmp_path):
    agent, sdk = _agent(tmp_path, stream=False)
    sdk.replies.append(_whole_reply())
    messages, _turn = _first_turn(agent)
    sdk.replies.append(_whole_reply(call=False))
    agent.config.provider = "anymodel"

    call_native_action_model(agent, messages, TOOLS)

    assert SIGNATURE not in json.dumps(sdk.requests[1])


def test_a_block_is_not_redacted_but_the_tool_result_beside_it_is(tmp_path):
    agent, sdk = _agent(tmp_path, stream=False)
    sdk.replies.append(_whole_reply())
    messages, _turn = _first_turn(agent)
    messages[3]["content"] = f"API_KEY={SECRETISH}"
    sdk.replies.append(_whole_reply(call=False))

    call_native_action_model(agent, messages, TOOLS)

    second = sdk.requests[1]
    assistant = next(message for message in second["messages"] if message["role"] == "assistant")
    assert assistant["content"][0]["thinking"] == f"First, read it {SECRETISH}."
    result = next(message for message in second["messages"] if message["role"] == "user" and message is not second["messages"][0])
    assert SECRETISH not in json.dumps(result)


class _OpenAiLike:
    """What every client but the Anthropic adapter is: it takes chat-completions kwargs."""

    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self.requests.append(kwargs)
        message = SimpleNamespace(content="done", tool_calls=None, reasoning_content=None)
        return SimpleNamespace(choices=[SimpleNamespace(message=message, finish_reason="stop")], usage=None)


@pytest.mark.parametrize("provider", ["openai", "chatgpt"])
def test_a_client_that_is_not_anthropics_never_sees_the_key(tmp_path, provider):
    agent, _sdk = _agent(tmp_path, stream=False, provider=provider)
    agent.client = _OpenAiLike()
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "go"}, _assistant_with_blocks(provider=provider)]
    messages.append(tool_message("toolu_1", "text"))

    call_native_action_model(agent, messages, TOOLS)

    sent = agent.client.requests[0]
    assert all(THINKING_KEY not in message for message in sent["messages"])
    assert SIGNATURE not in json.dumps(sent)
    assert messages[2][THINKING_KEY]["blocks"] == EXPECTED_BLOCKS
    if provider == "chatgpt":
        assert SIGNATURE not in json.dumps(chatgpt_client._request(sent))


# ── a forced tool choice the model refuses ─────────────────────────────────


def _refusal() -> anthropic.BadRequestError:
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        "tool_choice: forced tool use is not supported with thinking",
        response=httpx.Response(400, request=request),
        body=None,
    )


@pytest.fixture(autouse=True)
def _forget_refusals():
    llm_clients._FORCED_CHOICE_REFUSED.clear()
    yield
    llm_clients._FORCED_CHOICE_REFUSED.clear()


FORCED = {"type": "function", "function": {"name": "read_file"}}


def test_a_refused_forced_choice_is_asked_again_with_auto_and_remembered():
    client, sdk = _client()
    sdk.replies += [_refusal(), _whole_reply(call=False), _whole_reply(call=False)]

    _ask(client, tool_choice=FORCED)
    _ask(client, tool_choice=FORCED)

    assert [request.get("tool_choice") for request in sdk.requests] == [
        {"type": "tool", "name": "read_file"},
        {"type": "auto"},
        {"type": "auto"},
    ]


def test_a_refused_forced_choice_is_asked_again_in_a_stream_too():
    client, sdk = _client()
    sdk.replies += [_refusal(), _events(call=False)]

    chunks = list(_ask(client, stream=True, tool_choice=FORCED))

    assert [request.get("tool_choice") for request in sdk.requests] == [{"type": "tool", "name": "read_file"}, {"type": "auto"}]
    assert chunks[-1].thinking_blocks == EXPECTED_BLOCKS


def test_a_bad_request_about_something_else_is_not_retried():
    client, sdk = _client()
    other = anthropic.BadRequestError(
        "messages: roles must alternate",
        response=httpx.Response(400, request=httpx.Request("POST", "https://api.anthropic.com/v1/messages")),
        body=None,
    )
    sdk.replies += [other]

    with pytest.raises(RuntimeError, match="roles must alternate"):
        _ask(client, tool_choice=FORCED)

    assert len(sdk.requests) == 1
    assert not llm_clients._FORCED_CHOICE_REFUSED
