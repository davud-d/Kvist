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


def test_only_whole_lines_go_out_and_the_tail_waits():
    stream = CallStream()
    assert _feed(stream, '{"path": "a.py", "content": "one\\ntw') == [("content", 0, "one\n", False)]
    assert _feed(stream, '{"path": "a.py", "content": "one\\ntw') == []
    assert _feed(stream, '{"path": "a.py", "content": "one\\ntwo\\nthr') == [("content", 1, "two\n", False)]


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
