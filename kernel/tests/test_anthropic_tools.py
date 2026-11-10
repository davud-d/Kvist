# -*- coding: utf-8 -*-
"""Native tool calling through the Anthropic adapter: request shape, history, responses, streams."""
from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from anthropic.types import (
    InputJSONDelta,
    RawContentBlockDeltaEvent,
    RawContentBlockStartEvent,
    RawContentBlockStopEvent,
    ToolUseBlock,
)

from kernel.agent.agent_model import _merge_tool_call_delta
from kernel.llm.llm_clients import AnthropicCompatibleClient

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
MARK = {"type": "ephemeral"}


def _client(*, response=None, events=(), base_url=None):
    client = AnthropicCompatibleClient(api_key="test-key", base_url=base_url)
    sent: dict = {}

    def create(**kwargs):
        sent.update(kwargs)
        return response

    @contextmanager
    def stream(**kwargs):
        sent.update(kwargs)
        yield iter(events)

    client._client = SimpleNamespace(messages=SimpleNamespace(create=create, stream=stream))
    return client, sent


def _ask(client, messages=None, **kwargs):
    return client.chat.completions.create(
        model="claude-sonnet-5-5",
        messages=messages or [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}],
        tools=TOOLS,
        **kwargs,
    )


def _response(*blocks, stop_reason="end_turn"):
    return SimpleNamespace(content=list(blocks), stop_reason=stop_reason, usage=None)


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _tool_use(call_id, name, arguments):
    return SimpleNamespace(type="tool_use", id=call_id, name=name, input=arguments)


# ── request ────────────────────────────────────────────────────────────────


def test_tools_go_out_as_name_description_input_schema():
    client, sent = _client(response=_response(_text("ok")))
    _ask(client)

    assert sent["tools"][0]["name"] == "read_file"
    assert sent["tools"][0]["description"] == "Read a file."
    assert sent["tools"][0]["input_schema"] == TOOLS[0]["function"]["parameters"]
    assert "parameters" not in sent["tools"][0]


def test_direct_request_marks_last_tool_and_stays_within_four_marks():
    client, sent = _client(response=_response(_text("ok")))
    messages = [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
        {"role": "user", "content": "c"},
    ]
    _ask(client, messages)

    assert "cache_control" not in sent["tools"][0]
    assert sent["tools"][-1]["cache_control"] == MARK
    assert str(sent).count("'cache_control'") == 3


def test_gateway_request_gets_tools_without_any_mark():
    client, sent = _client(response=_response(_text("ok")), base_url="https://gateway.example.com/")
    _ask(client)

    assert "cache_control" not in str(sent)
    assert len(sent["tools"]) == 2


def test_forced_tool_choice_is_translated():
    client, sent = _client(response=_response(_text("ok")))
    _ask(client, tool_choice={"type": "function", "function": {"name": "read_file"}})

    assert sent["tool_choice"] == {"type": "tool", "name": "read_file"}


def test_forced_tool_choice_degrades_to_auto_while_thinking_is_on():
    client, sent = _client(response=_response(_text("ok")))
    _ask(client, tool_choice={"type": "function", "function": {"name": "read_file"}}, reasoning_effort="high")

    assert "thinking" in sent or "output_config" in sent
    assert sent["tool_choice"] == {"type": "auto"}


# ── history ────────────────────────────────────────────────────────────────


def _history_with_two_results():
    return [
        {"role": "system", "content": "sys"},
        {"role": "user", "content": "look around"},
        {
            "role": "assistant",
            "content": "Looking.",
            "tool_calls": [
                {"id": "c1", "type": "function", "function": {"name": "read_file", "arguments": '{"path": "a.py"}'}},
                {"id": "c2", "type": "function", "function": {"name": "git_status", "arguments": "{}"}},
            ],
        },
        {"role": "tool", "tool_call_id": "c1", "content": "print(1)"},
        {"role": "tool", "tool_call_id": "c2", "content": "clean"},
    ]


def test_history_turns_calls_into_tool_use_and_consecutive_results_into_one_user_message():
    client, sent = _client(response=_response(_text("ok")), base_url="https://gateway.example.com")
    _ask(client, _history_with_two_results())

    assert [m["role"] for m in sent["messages"]] == ["user", "assistant", "user"]
    assert sent["messages"][1]["content"] == [
        {"type": "text", "text": "Looking."},
        {"type": "tool_use", "id": "c1", "name": "read_file", "input": {"path": "a.py"}},
        {"type": "tool_use", "id": "c2", "name": "git_status", "input": {}},
    ]
    assert sent["messages"][2]["content"] == [
        {"type": "tool_result", "tool_use_id": "c1", "content": "print(1)"},
        {"type": "tool_result", "tool_use_id": "c2", "content": "clean"},
    ]


def test_history_replays_unparseable_arguments_as_an_empty_object():
    history = _history_with_two_results()
    history[2]["tool_calls"][0]["function"]["arguments"] = '{"path": "a.py'
    client, sent = _client(response=_response(_text("ok")))
    _ask(client, history)

    assert sent["messages"][1]["content"][1]["input"] == {}


def test_history_cache_mark_lands_on_the_last_tool_result_without_touching_the_rest():
    client, sent = _client(response=_response(_text("ok")))
    _ask(client, _history_with_two_results())

    results = sent["messages"][-1]["content"]
    assert "cache_control" not in results[0]
    assert results[-1]["cache_control"] == MARK


def test_thinking_is_left_off_while_replaying_tool_use_it_has_no_signed_block_for():
    client, sent = _client(response=_response(_text("ok")))
    _ask(client, _history_with_two_results(), reasoning_effort="high")

    assert "thinking" not in sent


# ── whole responses ────────────────────────────────────────────────────────


def test_single_tool_use_becomes_a_tool_call_and_finish_reason_tool_calls():
    reply = _ask(_client(response=_response(_tool_use("c1", "read_file", {"path": "a.py"}), stop_reason="tool_use"))[0])

    choice = reply.choices[0]
    assert choice.finish_reason == "tool_calls"
    [call] = choice.message.tool_calls
    assert (call.id, call.type, call.function.name) == ("c1", "function", "read_file")
    assert json.loads(call.function.arguments) == {"path": "a.py"}


def test_parallel_tool_use_blocks_keep_their_order_and_text():
    response = _response(
        _text("Two reads."),
        _tool_use("c1", "read_file", {"path": "a.py"}),
        _tool_use("c2", "read_file", {"path": "b.py"}),
        stop_reason="tool_use",
    )
    message = _ask(_client(response=response)[0]).choices[0].message

    assert message.content == "Two reads."
    assert [c.id for c in message.tool_calls] == ["c1", "c2"]
    assert [json.loads(c.function.arguments)["path"] for c in message.tool_calls] == ["a.py", "b.py"]


def test_answer_without_calls_has_no_tool_calls():
    choice = _ask(_client(response=_response(_text("done")))[0]).choices[0]

    assert choice.message.content == "done"
    assert not choice.message.tool_calls
    assert choice.finish_reason == "end_turn"


# ── streams ────────────────────────────────────────────────────────────────


def _start(index, call_id, name):
    return RawContentBlockStartEvent(
        type="content_block_start",
        index=index,
        content_block=ToolUseBlock(type="tool_use", id=call_id, name=name, input={}),
    )


def _json(index, piece):
    return RawContentBlockDeltaEvent(
        type="content_block_delta", index=index, delta=InputJSONDelta(type="input_json_delta", partial_json=piece)
    )


def _stop(index):
    return RawContentBlockStopEvent(type="content_block_stop", index=index)


def _message_delta(stop_reason, output_tokens=7):
    return SimpleNamespace(
        type="message_delta",
        delta=SimpleNamespace(stop_reason=stop_reason),
        usage=SimpleNamespace(output_tokens=output_tokens),
    )


def _start_usage():
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(
            usage=SimpleNamespace(
                input_tokens=10, output_tokens=1, cache_read_input_tokens=300, cache_creation_input_tokens=50
            )
        ),
    )


def _collect(chunks):
    """What the native stream consumer builds from the chunks: calls by index, plus the usage chunk."""
    calls: dict[int, dict[str, str]] = {}
    finish = None
    usage = None
    for chunk in chunks:
        if getattr(chunk, "usage", None) is not None:
            usage = chunk.usage
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        for piece in getattr(delta, "tool_calls", None) or []:
            _merge_tool_call_delta(calls, piece)
        finish = getattr(chunk.choices[0], "finish_reason", None) or finish
    return calls, finish, usage


def test_stream_assembles_arguments_sent_in_three_pieces():
    events = [
        _start_usage(),
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(text="Reading.", thinking=None)),
        _start(1, "c1", "read_file"),
        _json(1, ""),
        _json(1, '{"pa'),
        _json(1, 'th": "src/'),
        _json(1, 'a.py"}'),
        _stop(1),
        _message_delta("tool_use", 20),
    ]
    chunks = list(_ask(_client(events=events)[0], stream=True))
    calls, finish, usage = _collect(chunks)

    assert calls == {1: {"id": "c1", "name": "read_file", "arguments": '{"path": "src/a.py"}'}}
    assert finish == "tool_calls"
    # usage from earlier commits is untouched by tool events
    assert (usage.prompt_tokens, usage.completion_tokens, usage.cache_write_tokens) == (360, 20, 50)
    assert usage.prompt_tokens_details.cached_tokens == 300
    assert "Reading." in "".join(
        c.choices[0].delta.content or "" for c in chunks if c.choices and hasattr(c.choices[0], "delta")
    )


def test_stream_keeps_parallel_calls_apart():
    events = [
        _start(0, "c1", "read_file"),
        _start(1, "c2", "read_file"),
        _json(0, '{"path": "a.py"}'),
        _json(1, '{"path": '),
        _json(1, '"b.py"}'),
        _stop(0),
        _stop(1),
        _message_delta("tool_use"),
    ]
    calls, _finish, _usage = _collect(_ask(_client(events=events)[0], stream=True))

    assert [(e["id"], e["arguments"]) for _i, e in sorted(calls.items())] == [
        ("c1", '{"path": "a.py"}'),
        ("c2", '{"path": "b.py"}'),
    ]


def test_stream_gives_a_call_without_parameters_the_empty_object():
    events = [_start(0, "c1", "git_status"), _json(0, ""), _stop(0), _message_delta("tool_use")]
    calls, _finish, _usage = _collect(_ask(_client(events=events)[0], stream=True))

    assert calls[0]["arguments"] == "{}"


def test_stream_without_calls_yields_only_text_and_the_stop_reason():
    events = [
        SimpleNamespace(type="content_block_delta", index=0, delta=SimpleNamespace(text="done", thinking=None)),
        _message_delta("end_turn"),
    ]
    calls, finish, _usage = _collect(_ask(_client(events=events)[0], stream=True))

    assert calls == {}
    assert finish == "end_turn"


@pytest.mark.parametrize(
    "stop_reason,expected",
    [("tool_use", "tool_calls"), ("max_tokens", "length"), ("end_turn", "end_turn")],
)
def test_finish_reason_speaks_openai_for_tool_use_and_the_output_limit(stop_reason, expected):
    events = [_message_delta(stop_reason)]

    assert _collect(_ask(_client(events=events)[0], stream=True))[1] == expected
