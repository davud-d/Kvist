# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import time
from types import SimpleNamespace

from kernel.agent import agent_model
from kernel.agent.call_stream import CallStream
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import CallStreamEvent, FrontendChannel


def _feed(stream: CallStream, arguments: str, *, final: bool = False, tool: str = "write_file"):
    return [(p.field, p.first_line, p.text, p.done) for p in stream.pieces("c1", tool, arguments, final=final)]


def test_whole_words_go_out_and_the_unfinished_one_waits():
    stream = CallStream()
    assert _feed(stream, '{"path": "a.py", "content": "one\\ntw') == [("content", 0, "one\n", False)]
    assert _feed(stream, '{"path": "a.py", "content": "one\\ntw') == []
    assert _feed(stream, '{"path": "a.py", "content": "one\\ntwo thr') == [("content", 1, "two ", False)]
    assert _feed(stream, '{"path": "a.py", "content": "one\\ntwo three\\n') == [("content", 1, "three\n", False)]


def test_a_secret_is_never_split_between_two_deliveries():
    stream = CallStream()
    key = "sk-abcdefghijklmnopqrstuvwxyz0123456789"
    assert _feed(stream, '{"content": "k = \'' + key[:12]) == [("content", 0, "k = ", False)]
    assert _feed(stream, '{"content": "k = \'' + key + "'\\n") == [("content", 0, "'" + key + "'\n", False)]


def test_the_closing_quote_flushes_the_last_line_once():
    stream = CallStream()
    _feed(stream, '{"content": "a\\nb')
    assert _feed(stream, '{"content": "a\\nb"}') == [("content", 1, "b", True)]
    assert _feed(stream, '{"content": "a\\nb"}', final=True) == []


def test_a_chunk_ending_inside_an_escape_is_not_lost():
    stream = CallStream()
    assert _feed(stream, '{"content": "ж\\u04') == []
    assert _feed(stream, '{"content": "ж\\u0416\\n') == [("content", 0, "жЖ\n", False)]


def test_a_final_flush_closes_an_unterminated_field():
    stream = CallStream()
    assert _feed(stream, '{"content": "x = 1', final=True) == [("content", 0, "x = 1", True)]


def test_replace_streams_both_sides_and_other_tools_nothing():
    stream = CallStream()
    arguments = '{"path": "a", "old_text": "a\\n", "new_text": "b\\n'
    assert _feed(stream, arguments, tool="replace_in_file") == [
        ("old_text", 0, "a\n", True),
        ("new_text", 0, "b\n", False),
    ]
    assert _feed(CallStream(), '{"command": ["ls"]}', tool="run_shell") == []


def test_the_event_carries_the_call_and_redacts_secrets():
    channel = FrontendChannel()
    EventStreamUI(channel).print_call_stream(
        "c1", "write_file", "a.py", "content", 4, "key = 'sk-abcdefghijklmnopqrstuvwxyz0123456789'\n", False
    )
    (event,) = channel.drain()
    assert isinstance(event, CallStreamEvent)
    assert (event.call_id, event.line, event.done) == ("c1", 4, False)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in event.text
    assert event.to_dict()["type"] == "call_stream"


def test_a_streamed_native_turn_delivers_lines_before_the_call_is_complete(agent_factory):
    agent = agent_factory(max_steps=1)
    seen: list[tuple] = []
    agent._ui.print_call_stream = lambda *args: seen.append(args)
    body = json.dumps({"path": "a.py", "content": "import os\nprint(1)\n"})
    # Cut inside the content so the first line is complete while the call is not.
    cuts = [body[:30], body[30:45], body[45:]]

    def fragment(call_id, arguments):
        return SimpleNamespace(index=0, id=call_id, function=SimpleNamespace(name="write_file", arguments=arguments))

    def chunk(piece):
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[piece]))])

    def slow_stream():
        for number, cut in enumerate(cuts):
            yield chunk(fragment("c1" if number == 0 else None, cut))
            time.sleep(agent_model._CALL_LINES_INTERVAL + 0.02)

    turn = agent_model._native_turn_from_stream(agent, slow_stream(), time.monotonic())

    texts = "".join(args[5] for args in seen)
    assert texts == "import os\nprint(1)\n"
    assert seen[0][4] == 0 and not seen[0][6]
    assert seen[-1][6] is True
    assert turn.tool_calls[0].arguments == body


def test_a_call_ends_when_the_next_one_begins_not_when_the_response_ends(agent_factory):
    agent = agent_factory(max_steps=1)
    seen: list[tuple] = []
    agent._ui.print_call_stream = lambda *args: seen.append(args)

    def chunk(index, call_id, name, arguments):
        function = SimpleNamespace(name=name, arguments=arguments)
        piece = SimpleNamespace(index=index, id=call_id, function=function)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[piece]))])

    def stream():
        yield chunk(0, "c1", "write_file", '{"path": "a.py", "content": "one two')
        yield chunk(1, "c2", "write_file", '{"path": "b.py", "content": "x')
        time.sleep(0.3)  # the response goes on; the first call must already be closed
        yield chunk(1, None, "write_file", ' y"}')

    ended_before_the_response_did: list[bool] = []
    original = agent._ui.print_call_stream

    def watch(*args):
        original(*args)
        if args[0] == "c1" and args[6]:
            ended_before_the_response_did.append(not any(a[0] == "c2" and a[6] for a in seen))

    agent._ui.print_call_stream = watch
    agent_model._native_turn_from_stream(agent, stream(), time.monotonic())

    assert [a[5] for a in seen if a[0] == "c1" and a[6]] == ["two"]
    assert ended_before_the_response_did == [True]


def test_a_call_is_announced_once_the_model_moves_on_to_the_next(agent_factory):
    agent = agent_factory(max_steps=1)
    log: list[tuple] = []
    agent._ui.print_call_begin = lambda *args: log.append(("begin", *args))

    def chunk(index, call_id, name, arguments):
        function = SimpleNamespace(name=name, arguments=arguments)
        piece = SimpleNamespace(index=index, id=call_id, function=function)
        return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[piece]))])

    def stream():
        # The stream is read on another thread; the pause lets the turn digest each chunk first.
        for chunk_number, piece in enumerate(
            [
                chunk(0, "c1", "read_file", '{"path": "a.py", "description": "reads a.py"}'),
                chunk(1, "c2", "run_shell", '{"command": ["ls"]}'),
                chunk(2, "c3", "final_answer", '{"message": "done"}'),
            ]
        ):
            log.append(("chunk", chunk_number))
            yield piece
            time.sleep(0.05)

    agent_model._native_turn_from_stream(agent, stream(), time.monotonic())

    assert log == [
        ("chunk", 0),
        ("chunk", 1),
        ("begin", "c1", "read_file", "a.py", "reads a.py"),
        ("chunk", 2),
        ("begin", "c2", "run_shell", "", ""),
    ]
