# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import threading
import time

import pytest

from kernel.protocol.frontend_protocol import (
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
    UsageEvent,
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
            ),
            "step",
        ),
        (
            PlanEvent(markdown="**Goal**\n- do it"),
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
            UsageEvent(
                input_tokens=1200,
                output_tokens=300,
                model="gpt-test",
                kind="step",
                step=2,
            ),
            "usage",
        ),
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
        "append": True,
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
    ).to_dict()["reasoning_seconds"] == 0.0


def test_hello_event_carries_protocol_version():
    assert HelloEvent().to_dict() == {"type": "hello", "protocol_version": 1}


def test_hello_event_carries_optional_runtime_configuration():
    assert HelloEvent(
        provider="omniroute",
        models={"fast": "f", "smart": "s"},
        role="devops",
        approval_mode="deny",
    ).to_dict() == {
        "type": "hello",
        "protocol_version": 1,
        "provider": "omniroute",
        "models": {"fast": "f", "smart": "s"},
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


def test_hello_event_carries_optional_session_state():
    payload = HelloEvent(session="feature", has_checkpoint=True).to_dict()
    assert payload["session"] == "feature"
    assert payload["has_checkpoint"] is True
    # Omitted when unknown, so protocol v1 consumers see no new fields.
    assert "session" not in HelloEvent().to_dict()
    assert "has_checkpoint" not in HelloEvent().to_dict()


def test_hello_event_carries_optional_kvist_version():
    payload = HelloEvent(kvist_version="0.1.0b1").to_dict()
    assert payload["kvist_version"] == "0.1.0b1"
    # Additive: unset is omitted, so an old frontend sees no new field.
    assert "kvist_version" not in HelloEvent().to_dict()


def test_usage_event_separates_spend_from_context_size():
    payload = UsageEvent(
        input_tokens=9000,
        output_tokens=200,
        model="m",
        kind="step",
        step=4,
        context_tokens=9000,
        context_limit=120000,
    ).to_dict()

    assert payload["input_tokens"] == 9000
    assert payload["context_tokens"] == 9000
    assert payload["context_limit"] == 120000


def test_usage_event_defaults_context_to_zero():
    payload = UsageEvent(input_tokens=10, output_tokens=2).to_dict()

    assert payload["context_tokens"] == 0
    assert payload["context_limit"] == 0


def test_v2_call_fields_are_additive():
    from kernel.protocol.frontend_protocol import ActionOutputEvent, ActionResultEvent, FinalEvent, StepEvent

    step = StepEvent(1, "inspect", "", "Читаю", "read_file", {"path": "a"}, 0.0, "читает a", "call_1", "b1").to_dict()
    assert step["thought"] == "" and step["user_message"] == "Читаю"
    assert (step["call_id"], step["batch_id"]) == ("call_1", "b1")

    result = ActionResultEvent(1, "read_file", True, "Read 1 line", call_id="call_1", batch_id="b1").to_dict()
    assert (result["call_id"], result["batch_id"]) == ("call_1", "b1")
    assert ActionOutputEvent(2, "out", "call_2").to_dict()["call_id"] == "call_2"

    assert FinalEvent("ok", []).to_dict()["outcome"] == "done"
    assert FinalEvent("not yet", [], "incomplete").to_dict()["outcome"] == "incomplete"
