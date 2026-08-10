# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from agent import LocalAgent
from event_stream_ui import EventStreamUI
from frontend_protocol import (
    FrontendChannel,
    ReasoningChunkEvent,
    ReasoningCommitEvent,
    ReasoningStartEvent,
    StreamChunkEvent,
    StreamCommitEvent,
    StreamStartEvent,
)


def _delta(**fields):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(**fields))])


def _consume(stream):
    channel = FrontendChannel()
    fake_self = SimpleNamespace(_ui=EventStreamUI(channel))
    answer = LocalAgent._consume_chat_stream(fake_self, stream)
    return answer, channel.drain()


def test_reasoning_content_is_sealed_before_the_answer():
    stream = [
        _delta(content=None, reasoning_content="Let me think"),
        _delta(content=None, reasoning_content=" about it"),
        _delta(content="Hello", reasoning_content=None),
        _delta(content=" world", reasoning_content=None),
    ]
    answer, events = _consume(stream)
    types = [type(e).__name__ for e in events]

    assert answer == "Hello world"
    assert types == [
        "StreamStartEvent",
        "ReasoningStartEvent",
        "ReasoningChunkEvent",
        "ReasoningCommitEvent",
        "StreamChunkEvent",
        "StreamCommitEvent",
    ]
    # Fast streams must not drop reasoning even when the throttle never fires.
    reasoning = [e for e in events if isinstance(e, ReasoningChunkEvent)][-1]
    assert reasoning.text == "Let me think about it"
    commit = [e for e in events if isinstance(e, ReasoningCommitEvent)][0]
    assert commit.duration_seconds >= 0.0


def test_openrouter_reasoning_field_is_also_captured():
    stream = [
        _delta(content=None, reasoning="pondering"),
        _delta(content="answer"),
    ]
    _answer, events = _consume(stream)
    assert any(isinstance(e, ReasoningStartEvent) for e in events)
    assert [e for e in events if isinstance(e, ReasoningChunkEvent)][-1].text == "pondering"


def test_non_thinking_stream_emits_no_reasoning_events():
    stream = [_delta(content="Hi"), _delta(content=" there")]
    answer, events = _consume(stream)
    assert answer == "Hi there"
    assert [type(e) for e in events] == [
        StreamStartEvent,
        StreamChunkEvent,
        StreamCommitEvent,
    ]
