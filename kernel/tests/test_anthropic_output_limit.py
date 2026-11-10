# -*- coding: utf-8 -*-
"""The Anthropic output ceiling and calls cut off by it, from the adapter to the call requests."""
from __future__ import annotations

import pytest

from kernel.agent import agent_model
from kernel.agent.native_tools import NativeTurn, ToolCall, calls_from_turn
from kernel.llm import pricing
from kernel.tests.test_anthropic_tools import (
    _ask,
    _client,
    _json,
    _message_delta,
    _response,
    _start,
    _stop,
    _text,
    _tool_use,
)

_TABLE = {
    "claude-big": {"provider": "anthropic", "input": 1.0, "output": 1.0, "cache_read": None, "cache_write": None,
                   "max_output_tokens": 64000},
}


@pytest.fixture(autouse=True)
def _fixture_table(monkeypatch):
    monkeypatch.setattr(pricing, "_table", lambda: _TABLE)


def _sent_max_tokens(*, model="claude-big", stream=False, **kwargs):
    client, sent = _client(response=_response(_text("ok")), events=[_message_delta("end_turn")])
    result = client.chat.completions.create(
        model=model, messages=[{"role": "user", "content": "hi"}], stream=stream, **kwargs
    )
    if stream:
        list(result)
    return sent


# ── ceiling ────────────────────────────────────────────────────────────────


def test_a_streamed_request_asks_for_the_models_whole_output_ceiling():
    assert _sent_max_tokens(stream=True)["max_tokens"] == 64000


def test_a_request_without_a_stream_stays_under_what_the_sdk_sends_whole():
    # 3600 s * n / 128000 must stay within the SDK's ten minutes.
    assert _sent_max_tokens(stream=False)["max_tokens"] == 21_333


@pytest.mark.parametrize("stream", [True, False])
def test_a_model_outside_the_table_gets_8192(stream):
    assert _sent_max_tokens(model="claude-unlisted", stream=stream)["max_tokens"] == 8192


def test_a_limit_the_caller_names_is_kept():
    assert _sent_max_tokens(stream=True, max_tokens=600)["max_tokens"] == 600


def test_the_sdk_really_accepts_the_non_streaming_ceiling_and_refuses_one_more():
    import anthropic

    client = anthropic.Anthropic(api_key="k")
    client._calculate_nonstreaming_timeout(21_333, None)
    with pytest.raises(ValueError):
        client._calculate_nonstreaming_timeout(21_334, None)


def test_legacy_thinking_budget_leaves_room_for_the_answer_without_a_stream():
    sent = _sent_max_tokens(stream=False, model="claude-sonnet-4-5", reasoning_effort="max")

    assert sent["thinking"]["type"] == "enabled"
    assert sent["max_tokens"] <= 21_333
    assert sent["thinking"]["budget_tokens"] < sent["max_tokens"]


# ── truncation seen by the adapter ─────────────────────────────────────────


def _cut_write(*, close: bool):
    events = [
        _start(0, "c1", "write_file"),
        _json(0, '{"path": "a.py", "content": "print('),
    ]
    if close:
        events += [_json(0, '1)"}'), _stop(0)]
    events.append(_message_delta("max_tokens"))
    return events


def _final_choice(chunks):
    return [chunk.choices[0] for chunk in chunks if chunk.choices][-1]


def test_a_stream_cut_inside_a_call_says_the_last_call_never_closed():
    client, _sent = _client(events=_cut_write(close=False))

    choice = _final_choice(_ask(client, stream=True))

    assert choice.finish_reason == "length"
    assert choice.last_call_open is True


def test_a_stream_whose_call_closed_before_the_limit_does_not():
    client, _sent = _client(events=_cut_write(close=True))

    choice = _final_choice(_ask(client, stream=True))

    assert choice.finish_reason == "length"
    assert choice.last_call_open is False


def test_a_whole_reply_ending_on_a_call_under_the_limit_is_treated_as_unfinished():
    partial = _tool_use("c1", "write_file", {"path": "a.py"})
    client, _sent = _client(response=_response(partial, stop_reason="max_tokens"))

    choice = _ask(client).choices[0]

    assert choice.finish_reason == "length"
    assert choice.last_call_open is True


def test_a_whole_reply_with_text_after_its_call_is_not():
    client, _sent = _client(
        response=_response(_tool_use("c1", "git_status", {}), _text("done"), stop_reason="max_tokens")
    )

    assert _ask(client).choices[0].last_call_open is False


# ── truncation seen by the core ────────────────────────────────────────────

WHOLE = '{"path": "a.py", "content": "print(1)"}'
CUT = '{"path": "a.py", "content": "print('


def _turn(calls, **flags):
    return NativeTurn(content="", tool_calls=[ToolCall(*call) for call in calls], **flags)


def test_a_last_call_with_broken_arguments_under_the_limit_is_not_run_and_says_why():
    requests = calls_from_turn(_turn([("c1", "write_file", CUT)], cut_off=True))

    assert requests[0].arguments is None
    assert "cut off by the output limit" in requests[0].error
    assert "smaller parts" in requests[0].error


def test_a_call_whole_at_the_limit_runs():
    requests = calls_from_turn(_turn([("c1", "write_file", WHOLE)], cut_off=True))

    assert requests[0].error == ""
    assert requests[0].arguments == {"path": "a.py", "content": "print(1)"}


def test_a_call_the_provider_says_never_closed_is_not_run_even_when_its_arguments_parse():
    requests = calls_from_turn(_turn([("c1", "write_file", '{"path": "a.py"}')], cut_off=True, last_call_open=True))

    assert requests[0].arguments is None
    assert "cut off by the output limit" in requests[0].error


def test_only_the_last_call_is_cut_the_earlier_ones_run():
    requests = calls_from_turn(
        _turn([("c1", "read_file", '{"path": "a.py"}'), ("c2", "write_file", CUT)], cut_off=True)
    )

    assert requests[0].arguments == {"path": "a.py"}
    assert requests[1].arguments is None and "cut off" in requests[1].error


def test_broken_arguments_without_the_limit_keep_their_own_message():
    requests = calls_from_turn(_turn([("c1", "write_file", CUT)]))

    assert "not valid JSON" in requests[0].error


# ── the whole path, adapter to call requests ───────────────────────────────


def _agent_over(agent_factory, monkeypatch, *, stream: bool, events=None, response=None):
    agent = agent_factory(max_steps=1)
    agent.config.stream = stream
    client, _sent = _client(events=events or (), response=response)
    adapter = client.chat.completions

    def create(_agent, request):
        return adapter.create(**{key: value for key, value in request.items() if key != "timeout"})

    monkeypatch.setattr(agent_model, "_create_completion_with_reasoning_fallback", create)
    monkeypatch.setattr(agent_model, "_STREAM_LOSES_ARGUMENTS", set())
    return agent


def _request(agent):
    return agent_model._request_native_turn(
        agent, "claude-big", [{"role": "user", "content": "x"}], [_write_tool()]
    )


def _write_tool():
    return {"type": "function", "function": {"name": "write_file", "parameters": {"type": "object", "properties": {}}}}


def test_a_streamed_write_file_cut_by_the_limit_is_not_run(agent_factory, monkeypatch):
    agent = _agent_over(agent_factory, monkeypatch, stream=True, events=_cut_write(close=False))

    requests = calls_from_turn(_request(agent))

    assert len(requests) == 1 and requests[0].arguments is None
    assert "cut off by the output limit" in requests[0].error


def test_a_whole_write_file_cut_by_the_limit_is_not_run(agent_factory, monkeypatch):
    partial = _tool_use("c1", "write_file", {"path": "a.py", "content": "print("})
    agent = _agent_over(
        agent_factory, monkeypatch, stream=False, response=_response(partial, stop_reason="max_tokens")
    )

    requests = calls_from_turn(_request(agent))

    assert requests[0].arguments is None
    assert "cut off by the output limit" in requests[0].error


def test_a_streamed_whole_call_at_the_limit_runs(agent_factory, monkeypatch):
    agent = _agent_over(agent_factory, monkeypatch, stream=True, events=_cut_write(close=True))

    requests = calls_from_turn(_request(agent))

    assert requests[0].error == ""
    assert requests[0].arguments == {"path": "a.py", "content": "print(1)"}


# ── the debug warning ──────────────────────────────────────────────────────


def test_unparsed_arguments_in_the_history_are_announced_in_debug(agent_factory):
    from kernel.agent.agent_runtime import _warn_unparsed_history_arguments

    agent = agent_factory(max_steps=1)
    lines: list[str] = []
    agent._ui.debug_print = lines.append

    _warn_unparsed_history_arguments(agent, _turn([("c1", "write_file", CUT), ("c2", "git_status", "{}")]))

    assert len(lines) == 1
    assert "write_file" in lines[0] and "{}" in lines[0]
