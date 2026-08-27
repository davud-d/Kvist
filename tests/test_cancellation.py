# -*- coding: utf-8 -*-
"""Phase 1: safe, transport-agnostic cancellation of an action-loop run.

Cancellation stops the loop at a safe point (between steps, or while consuming a
model stream) without aborting an action already in flight and without rolling
back the work the agent already did.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import event_stdio
from agent_state import RunCancelled
from frontend_protocol import is_cancel_message
from tests.conftest import make_plan


def _prepare_loop_agent(agent_factory, **overrides):
    agent = agent_factory(max_steps=6, **overrides)
    agent._state.plan_info = make_plan()
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.response_language = "en"
    return agent


def _chunk(text: str):
    return SimpleNamespace(
        choices=[SimpleNamespace(delta=SimpleNamespace(content=text))]
    )


# ── Level 1: cancellation between steps ──────────────────────────────────────


def test_request_cancel_between_steps_stops_on_safe_stop_path(agent_factory):
    agent = _prepare_loop_agent(agent_factory)
    calls = {"n": 0}

    def fake(prompt):
        calls["n"] += 1
        # Request cancellation while still returning a valid, executable action:
        # the loop must finish this step, then stop at the top of the next one.
        agent.request_cancel()
        return json.dumps(
            {
                "thought": "inspect",
                "plan_step": "inspect",
                "progress": 20,
                "user_message": "listing",
                "action": {"type": "list_dir", "args": {"path": "."}},
            }
        )

    agent._call_action_model = fake

    result = agent._run_loop("inspect the project", start_step=1)

    assert result == agent._build_cancellation_message()
    # Exactly one action ran before the cancel took effect at the next step top.
    assert calls["n"] == 1


def test_cancelled_run_records_clean_stop_not_half_result(agent_factory):
    agent = _prepare_loop_agent(agent_factory)

    def fake(prompt):
        agent.request_cancel()
        return json.dumps(
            {
                "thought": "create the file",
                "plan_step": "edit",
                "progress": 60,
                "user_message": "writing",
                "action": {
                    "type": "write_file",
                    "args": {"path": "hello.py", "content": "print('hi')\n"},
                },
            }
        )

    agent._call_action_model = fake

    result = agent._run_loop("create hello.py", start_step=1)

    # The returned result is the clean cancel notice, never a fabricated success.
    assert result == agent._build_cancellation_message()
    statuses = [log.get("status") for log in agent._state.step_logs]
    assert "final_answer" not in statuses


def test_cancellation_message_is_localized(agent_factory):
    agent = _prepare_loop_agent(agent_factory)
    agent._state.response_language = "ru"
    ru_message = agent._build_cancellation_message()
    agent._state.response_language = "en"
    en_message = agent._build_cancellation_message()
    assert ru_message != en_message
    assert "остановлен" in ru_message
    assert "stopped" in en_message.lower()


# ── Level 3 boundary: an in-flight action is never aborted ───────────────────


def test_cancel_does_not_interrupt_the_current_action(agent_factory):
    agent = _prepare_loop_agent(agent_factory)

    def fake(prompt):
        # The cancel arrives during the step that writes the file; the write
        # must still complete (level 3 is intentionally not implemented).
        agent.request_cancel()
        return json.dumps(
            {
                "thought": "create the file",
                "plan_step": "edit",
                "progress": 60,
                "user_message": "writing",
                "action": {
                    "type": "write_file",
                    "args": {"path": "kept.py", "content": "print('kept')\n"},
                },
            }
        )

    agent._call_action_model = fake

    agent._run_loop("create kept.py", start_step=1)

    written = agent.executor.workspace / "kept.py"
    assert written.exists()
    assert written.read_text() == "print('kept')\n"


# ── Level 2: cancellation while consuming a model stream ──────────────────────


def test_cancel_during_generation_raises_and_closes_stream(agent_factory):
    agent = agent_factory()
    agent._state.response_language = "en"
    state = {"closed": False, "consumed": 0}

    class FakeStream:
        def __init__(self):
            self._chunks = iter([_chunk("a"), _chunk("b"), _chunk("c")])

        def __iter__(self):
            return self

        def __next__(self):
            state["consumed"] += 1
            return next(self._chunks)

        def close(self):
            state["closed"] = True

    agent._create_json_completion = lambda **kwargs: FakeStream()
    agent.request_cancel()

    with pytest.raises(RunCancelled):
        agent._call_model_streaming("mock-model", [{"role": "user", "content": "hi"}])

    # Generation stopped on the first chunk instead of draining the stream,
    # and the HTTP stream was closed on the way out.
    assert state["consumed"] == 1
    assert state["closed"] is True


def test_run_cancelled_is_not_retried_by_call_model(agent_factory):
    agent = agent_factory(max_retries=3)
    agent._state.response_language = "en"
    launches = {"n": 0}

    class FakeStream:
        def __iter__(self):
            return iter([_chunk("x")])

        def close(self):
            pass

    def fake_completion(**kwargs):
        launches["n"] += 1
        return FakeStream()

    agent._create_json_completion = fake_completion
    agent.request_cancel()

    with pytest.raises(RunCancelled):
        agent._call_model(
            model="mock-model",
            system_prompt="sys",
            user_prompt="user",
        )

    # A cancel is not a provider failure: the model layer must not retry it.
    assert launches["n"] == 1


# ── Flag lifecycle: no leak into the next turn ───────────────────────────────


def test_request_cancel_and_reset_toggle_flag(agent_factory):
    agent = agent_factory()
    assert not agent._cancel_event.is_set()
    agent.request_cancel()
    assert agent._cancel_event.is_set()
    agent._reset_cancel()
    assert not agent._cancel_event.is_set()


def test_run_clears_a_stale_cancel_before_the_loop(agent_factory):
    agent = agent_factory(skip_planning=True)
    agent.request_cancel()
    assert agent._cancel_event.is_set()
    seen = {}

    def spy_loop(user_goal, start_step):
        seen["set_at_loop_start"] = agent._cancel_event.is_set()
        return "ok"

    agent._run_loop = spy_loop

    result = agent.run("do the thing", routed_role="coder")

    assert result == "ok"
    assert seen["set_at_loop_start"] is False


def test_resume_clears_a_stale_cancel(agent_factory):
    agent = agent_factory()
    agent.request_cancel()
    # No checkpoint exists, so resume returns None — but it must still have
    # cleared the flag first, before touching any model.
    assert agent.resume() is None
    assert not agent._cancel_event.is_set()


# ── Transport: new inbound cancel type, legacy InputResponse unchanged ────────


class _SpySession:
    def __init__(self):
        self.responded = []
        self.cancelled = 0
        self.closed = 0

    def cancel(self):
        self.cancelled += 1

    def respond(self, request_id, *, value=None, approved=None):
        self.responded.append((request_id, value, approved))

    def close(self):
        self.closed += 1


def test_is_cancel_message_discriminates_on_type():
    assert is_cancel_message({"type": "cancel"})
    assert not is_cancel_message({"request_id": "r1", "value": "v"})
    assert not is_cancel_message({"request_id": "r1", "approved": True})
    assert not is_cancel_message({})


def test_read_responses_routes_cancel_message():
    session = _SpySession()
    lines = [json.dumps({"type": "cancel"}) + "\n"]

    event_stdio._read_responses(session, iter(lines))

    assert session.cancelled == 1
    assert session.responded == []
    assert session.closed == 1


def test_read_responses_parses_legacy_input_response():
    session = _SpySession()
    lines = [
        json.dumps({"request_id": "r1", "value": "hi"}) + "\n",
        json.dumps({"request_id": "r2", "approved": True}) + "\n",
    ]

    event_stdio._read_responses(session, iter(lines))

    assert session.responded == [("r1", "hi", None), ("r2", None, True)]
    assert session.cancelled == 0
    assert session.closed == 1
