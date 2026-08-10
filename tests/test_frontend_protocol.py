# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import threading
import time

import pytest

from frontend_protocol import (
    DebugEvent,
    ErrorEvent,
    FinalEvent,
    FrontendChannel,
    HelloEvent,
    InputRequestEvent,
    InputResponse,
    LiveStartEvent,
    LiveStopEvent,
    MessageEvent,
    PlanEvent,
    ReasoningChunkEvent,
    ReasoningCommitEvent,
    ReasoningStartEvent,
    SandboxNoticeEvent,
    StatusEvent,
    StepEvent,
    StreamChunkEvent,
    StreamCommitEvent,
    StreamStartEvent,
)


def _all_sample_events():
    return [
        (HelloEvent(), "hello"),
        (LiveStartEvent(), "live_start"),
        (LiveStopEvent(), "live_stop"),
        (
            StatusEvent(
                user_message="hi",
                status="running",
                step=3,
                progress=42,
                plan_step="edit",
                is_error=False,
            ),
            "status",
        ),
        (
            StepEvent(
                step=1,
                plan_step="inspect",
                thought="thinking",
                user_message="looking",
                action_type="read_file",
                action_args={"path": "a.py"},
                progress=10,
            ),
            "step",
        ),
        (
            PlanEvent(
                goal="do it",
                complexity="simple",
                plan=["a", "b"],
                success_criteria=["works"],
                risks=["none"],
            ),
            "plan",
        ),
        (SandboxNoticeEvent(note="degraded"), "sandbox_notice"),
        (FinalEvent(message="done", user_messages=["ok"]), "final"),
        (MessageEvent(markdown="# hi"), "message"),
        (StreamStartEvent(stream_id="s1"), "stream_start"),
        (StreamChunkEvent(stream_id="s1", text="chunk"), "stream_chunk"),
        (StreamCommitEvent(stream_id="s1", text="full"), "stream_commit"),
        (ReasoningStartEvent(stream_id="s1"), "reasoning_start"),
        (ReasoningChunkEvent(stream_id="s1", text="think"), "reasoning_chunk"),
        (ReasoningCommitEvent(stream_id="s1", duration_seconds=3.5), "reasoning_commit"),
        (DebugEvent(message="dbg"), "debug"),
        (ErrorEvent(message="boom"), "error"),
        (
            InputRequestEvent(
                request_id="r1",
                kind="text",
                title="Name?",
                body="please",
                options=None,
                current_value="x",
                help_text="help",
            ),
            "input_request",
        ),
    ]


def test_to_dict_type_and_json_serializable():
    for event, tag in _all_sample_events():
        d = event.to_dict()
        assert d["type"] == tag
        assert event.type == tag
        # Must be JSON-serializable.
        json.dumps(d)


def test_status_event_fields_roundtrip():
    d = StatusEvent(
        user_message="hi",
        status="running",
        step=3,
        progress=42,
        plan_step="edit",
        is_error=True,
    ).to_dict()
    assert d == {
        "type": "status",
        "user_message": "hi",
        "status": "running",
        "step": 3,
        "progress": 42,
        "plan_step": "edit",
        "is_error": True,
    }


def test_reasoning_events_to_dict():
    assert ReasoningStartEvent(stream_id="s1").to_dict() == {
        "type": "reasoning_start",
        "stream_id": "s1",
    }
    assert ReasoningChunkEvent(stream_id="s1", text="think").to_dict() == {
        "type": "reasoning_chunk",
        "stream_id": "s1",
        "text": "think",
    }
    assert ReasoningCommitEvent(stream_id="s1", duration_seconds=3.5).to_dict() == {
        "type": "reasoning_commit",
        "stream_id": "s1",
        "duration_seconds": 3.5,
    }


def test_step_event_carries_reasoning_seconds():
    d = StepEvent(
        step=1,
        plan_step="inspect",
        thought="t",
        user_message="m",
        action_type="read_file",
        action_args={"path": "a.py"},
        progress=10,
        reasoning_seconds=8.2,
    ).to_dict()
    assert d["reasoning_seconds"] == 8.2
    # Defaults to 0.0 when the model does not think.
    assert StepEvent(
        step=1,
        plan_step="inspect",
        thought="t",
        user_message="m",
        action_type="read_file",
        action_args={},
        progress=10,
    ).to_dict()["reasoning_seconds"] == 0.0


def test_hello_event_carries_protocol_version():
    assert HelloEvent().to_dict() == {"type": "hello", "protocol_version": 1}


def test_hello_event_carries_optional_runtime_configuration():
    assert HelloEvent(
        provider="omniroute",
        models={"fast": "f", "smart": "s", "planner": "p"},
        role="devops",
        approval_mode="deny",
    ).to_dict() == {
        "type": "hello",
        "protocol_version": 1,
        "provider": "omniroute",
        "models": {"fast": "f", "smart": "s", "planner": "p"},
        "role": "devops",
        "approval_mode": "deny",
    }


def test_error_event_to_dict():
    d = ErrorEvent(message="boom").to_dict()
    assert d == {"type": "error", "message": "boom"}


def test_input_request_carries_options():
    d = InputRequestEvent(
        request_id="r",
        kind="choice",
        title="Pick",
        options=[{"label": "A", "value": "a"}],
    ).to_dict()
    assert d["options"] == [{"label": "A", "value": "a"}]
    assert d["body"] == ""
    assert d["help_text"] == ""


def test_input_response_from_dict_full():
    r = InputResponse.from_dict({"request_id": "r", "value": "x"})
    assert r.request_id == "r"
    assert r.value == "x"
    assert r.approved is None


def test_input_response_from_dict_missing_keys():
    r = InputResponse.from_dict({})
    assert r.request_id == ""
    assert r.value is None
    assert r.approved is None


def test_emit_and_drain_order():
    ch = FrontendChannel()
    ch.emit(LiveStartEvent())
    ch.emit(DebugEvent(message="a"))
    ch.emit(DebugEvent(message="b"))
    drained = ch.drain()
    assert [type(e).__name__ for e in drained] == [
        "LiveStartEvent",
        "DebugEvent",
        "DebugEvent",
    ]
    assert drained[1].message == "a"
    assert drained[2].message == "b"
    # Second drain is empty.
    assert ch.drain() == []


def test_get_returns_none_on_timeout():
    ch = FrontendChannel()
    assert ch.get(timeout=0.05) is None


def test_request_blocks_then_unblocks():
    ch = FrontendChannel()

    def responder():
        time.sleep(0.05)
        # Read the emitted request from the out-queue to learn the id.
        event = ch.get(timeout=0.5)
        assert isinstance(event, InputRequestEvent)
        ch.respond(event.request_id, value="ok")

    t = threading.Thread(target=responder)
    t.start()
    resp = ch.request("text", "Name?")
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert resp.value == "ok"
    assert resp.approved is None


def test_respond_unknown_id_is_noop():
    ch = FrontendChannel()
    ch.respond("does-not-exist", value="x")  # must not raise


def test_close_unblocks_pending_request():
    ch = FrontendChannel()
    result = {}

    def worker():
        result["resp"] = ch.request("confirm", "Proceed?")

    t = threading.Thread(target=worker)
    t.start()
    # Give the worker time to register the waiter.
    for _ in range(50):
        drained = ch.drain()
        if drained:
            break
        time.sleep(0.01)
    ch.close()
    t.join(timeout=1.0)
    assert not t.is_alive()
    assert result["resp"].value is None
    assert result["resp"].approved is None


def test_request_after_close_returns_immediately():
    ch = FrontendChannel()
    ch.close()
    resp = ch.request("confirm", "Proceed?")
    assert resp.value is None
    assert resp.approved is None


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-q"]))
