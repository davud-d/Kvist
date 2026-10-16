# -*- coding: utf-8 -*-
from __future__ import annotations

from kernel import context_meter
from kernel.agent_model import _emit_usage
from kernel.context_meter import (
    ContextUsage,
    begin_call,
    context_usage,
    end_call,
    mute_generation,
    note_generated,
    note_pending,
)


class _CapturingUI:
    def __init__(self):
        self.events = []

    def print_context(self, payload):
        self.events.append(payload)


def test_everything_in_the_window_is_summed(agent_factory):
    agent = agent_factory(max_context_tokens=50_000)
    assert context_usage(agent).used == 0

    begin_call(agent, 1000)
    note_generated(agent, lambda: "word " * 400)
    usage = context_usage(agent)
    assert usage.prompt == 1000
    assert usage.generated > 0
    assert usage.used == usage.prompt + usage.generated
    assert usage.limit == 50_000

    end_call(agent, 1000, 700)
    note_pending(agent, "line of command output\n" * 50)
    usage = context_usage(agent)
    assert (usage.prompt, usage.generated) == (1000, 700)
    assert usage.pending > 0
    assert usage.used == 1700 + usage.pending

    begin_call(agent, 2500)
    assert context_usage(agent).used == 2500


def test_a_call_outside_the_conversation_does_not_move_the_window(agent_factory):
    agent = agent_factory()
    end_call(agent, 900, 100)
    mute_generation(agent)
    note_generated(agent, lambda: "report text " * 500)
    assert context_usage(agent).used == 1000

    _emit_usage(agent, model="m", input_tokens=77, output_tokens=5, kind="report")
    assert context_usage(agent).used == 1000


def test_live_events_are_throttled_while_streaming_but_not_at_boundaries(agent_factory, monkeypatch):
    agent = agent_factory()
    agent._ui = _CapturingUI()
    clock = [100.0]
    monkeypatch.setattr(context_meter.time, "monotonic", lambda: clock[0])

    begin_call(agent, 500)  # boundary: always published
    assert len(agent._ui.events) == 1

    for index in range(1, 6):  # five chunks inside one refresh window
        clock[0] += 0.3
        note_generated(agent, lambda: "token " * (index * 40))
    assert len(agent._ui.events) == 1

    clock[0] += context_meter.REFRESH_SECONDS
    note_generated(agent, lambda: "token " * 400)
    assert len(agent._ui.events) == 2
    assert agent._ui.events[-1]["generated"] > 0

    note_pending(agent, "result")  # boundary again
    assert len(agent._ui.events) == 3
    assert agent._ui.events[-1]["pending"] > 0


def test_usage_event_carries_the_same_numbers(agent_factory):
    agent = agent_factory(max_context_tokens=50_000)
    seen = []
    agent._ui.print_usage = seen.append
    _emit_usage(agent, model="m", input_tokens=1200, output_tokens=300, kind="step")
    assert seen[-1]["context_tokens"] == 1500
    assert seen[-1]["context_limit"] == 50_000


def test_fraction_is_clamped_and_safe_for_zero_limit():
    assert ContextUsage(limit=100, used=250).fraction == 1.0
    assert ContextUsage(limit=100, used=250).free == 0
    assert ContextUsage(limit=0, used=10).fraction == 0.0
