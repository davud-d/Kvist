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

from kernel import event_stdio
from kernel.agent_state import RunCancelled
from kernel.frontend_protocol import is_cancel_message


def _prepare_loop_agent(agent_factory, **overrides):
    agent = agent_factory(max_steps=6, **overrides)
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


def test_a_stop_that_arrives_with_the_response_leaves_its_calls_unrun(agent_factory):
    # The calls of a response the model finished while the person pressed stop
    # have not started yet: after a stop or a pause they must not run until the
    # model has seen the correction.
    agent = _prepare_loop_agent(agent_factory)

    def fake(prompt):
        agent.request_cancel()
        return json.dumps(
            {
                "action": {
                    "type": "write_file",
                    "args": {"path": "kept.py", "content": "print('kept')\n"},
                },
            }
        )

    agent._call_action_model = fake

    agent._run_loop("create kept.py", start_step=1)

    assert not (agent.executor.workspace / "kept.py").exists()
    assert agent._state.stopped_by_user is True


def test_an_action_already_executing_completes_after_a_stop(agent_factory):
    agent = _prepare_loop_agent(agent_factory)
    original = agent.executor.execute

    def execute(command):
        # The stop lands while the write is running: it finishes (atomic
        # replace), and only the next step is not taken.
        agent.request_cancel()
        return original(command)

    agent.executor.execute = execute
    agent._call_action_model = lambda prompt: json.dumps(
        {"action": {"type": "write_file", "args": {"path": "kept.py", "content": "print('kept')\n"}}}
    )

    agent._run_loop("create kept.py", start_step=1)

    written = agent.executor.workspace / "kept.py"
    assert written.read_text() == "print('kept')\n"
    assert agent.control.state == "cancelled"


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

    # A stop that is already requested reads nothing from the stream, and the
    # HTTP stream is closed on the way out.
    assert state["consumed"] == 0
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
    agent = agent_factory()
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

    def respond(self, request_id, *, value=None, approved=None, remember=False):
        self.responded.append((request_id, value, approved, remember))

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

    assert session.responded == [("r1", "hi", None, False), ("r2", None, True, False)]


def test_read_responses_forwards_remember():
    """`remember` must reach the session, or "don't ask again" is silently lost.

    The ACP transport earns `allow_always` only when Kvist handed the request a
    saveable pattern; if the transport's reply is dropped on the way to
    ``EventSession.respond``, the approval works but the rule is never saved.
    """
    session = _SpySession()
    lines = [json.dumps({"request_id": "r1", "approved": True, "remember": True}) + "\n"]

    event_stdio._read_responses(session, iter(lines))

    assert session.responded == [("r1", None, True, True)]
    assert session.cancelled == 0
    assert session.closed == 1


# ── A stop is part of the session's context ───────────────────────────────────


def test_stop_inside_the_loop_is_recorded_for_later_turns(agent_factory):
    agent = agent_factory()

    def stop(prompt):
        raise KeyboardInterrupt

    agent._call_action_model = stop

    result = agent.run("refactor the parser", routed_role="coder")

    assert result == agent._build_cancellation_message()
    entry = agent._session_history[-1]
    assert entry["goal"] == "refactor the parser"
    assert entry["stopped_by_user"] is True


def test_stop_before_the_loop_ends_the_turn_and_is_recorded(agent_factory):
    agent = agent_factory()

    def stop_setup():
        raise KeyboardInterrupt

    agent._select_action_model_for_run = stop_setup
    agent._run_loop = lambda goal, start_step: pytest.fail("loop must not start")

    result = agent.run("refactor the parser", routed_role="coder")

    assert result == agent._build_cancellation_message()
    assert agent._session_history[-1]["stopped_by_user"] is True


def test_a_finished_turn_is_not_marked_as_stopped(agent_factory):
    agent = agent_factory()
    agent._run_loop = lambda goal, start_step: "done"

    agent.run("refactor the parser", routed_role="coder")

    assert "stopped_by_user" not in agent._session_history[-1]


def _logged(action_type, status="ok", **args):
    return {"status": status, "command": {"action": {"type": action_type, "args": args}}}


def test_a_stopped_run_keeps_what_it_read_searched_and_changed(agent_factory):
    agent = agent_factory()
    agent._state.step_logs = [
        _logged("read_file", path="kernel/main.py"),
        _logged("read_file", path="kernel/main.py"),
        _logged("read_file", path="kernel/agent.py", start_line=100, end_line=180),
        _logged("read_multiple_files", paths=["a.py", "b.py"]),
        _logged("read_file", status="blocked", path="never.py"),
        _logged("search_code", query="files_changed"),
        _logged("replace_in_file", path="kernel/main.py", old_text="a", new_text="b"),
        _logged("run_shell", command=["ls"]),
    ]

    agent._add_to_session_history("do it", "coder", "stopped", stopped_by_user=True)

    assert agent._session_history[-1]["trail"] == {
        "read": ["kernel/main.py", "kernel/agent.py:100-180", "a.py", "b.py"],
        "searched": ["files_changed"],
        "changed": ["kernel/main.py"],
    }


def test_the_trail_is_kept_for_a_stopped_turn_only(agent_factory):
    agent = agent_factory()
    agent._state.step_logs = [_logged("read_file", path="a.py")]

    agent._add_to_session_history("do it", "coder", "done")
    agent._add_to_session_history("again", "coder", "stopped", stopped_by_user=True)
    agent._state.step_logs = []
    agent._add_to_session_history("chat", "chat", "stopped", stopped_by_user=True)

    assert "trail" not in agent._session_history[-3]
    assert agent._session_history[-2]["trail"] == {"read": ["a.py"]}
    assert "trail" not in agent._session_history[-1]


def test_the_next_turn_is_told_what_the_stopped_run_had_read(agent_factory):
    from kernel.agent_context import render_session_history

    history = [
        {"goal": "add commands", "role": "coder", "result": "x", "stopped_by_user": True,
         "trail": {"read": ["kernel/main.py:3600-3900"], "searched": ["STDIO_COMMANDS"]}},
    ]

    rendered = render_session_history(history, include_outputs=False)

    assert "before the stop it read: kernel/main.py:3600-3900" in rendered
    assert "before the stop it searched for: STDIO_COMMANDS" in rendered
    assert "by line range" in rendered
    assert "changed" not in rendered


def test_every_history_reader_tells_the_model_about_the_stop(agent_factory):
    from kernel.agent_context import render_session_history

    agent = agent_factory()
    agent._session_history = [
        {"goal": "refactor the parser", "role": "coder", "result": "x",
         "stopped_by_user": True},
    ]

    assert "stopped this turn" in render_session_history(agent._session_history, include_outputs=False)
    assert "stopped this turn" in agent._build_runtime_prompt("carry on", 1)


# ── Level 3: a stop must not wait for a silent provider ───────────────────────


def test_stop_frees_a_loop_blocked_on_a_silent_stream():
    import threading
    import time

    from kernel.cancellable_call import iterate_cancellable

    cancel = threading.Event()
    release = threading.Event()
    closed = threading.Event()

    class SilentStream:
        def __iter__(self):
            return self

        def __next__(self):
            release.wait(10)
            raise StopIteration

        def close(self):
            closed.set()
            release.set()

    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(RunCancelled):
        list(iterate_cancellable(cancel, SilentStream()))

    assert time.monotonic() - started < 1.0
    assert closed.wait(1.0)


def test_stop_frees_a_loop_blocked_on_a_request_that_never_answers():
    import threading
    import time

    from kernel.cancellable_call import call_cancellable

    cancel = threading.Event()
    release = threading.Event()
    threading.Timer(0.1, cancel.set).start()
    started = time.monotonic()
    try:
        with pytest.raises(RunCancelled):
            call_cancellable(cancel, lambda: release.wait(10))
        assert time.monotonic() - started < 1.0
    finally:
        release.set()
