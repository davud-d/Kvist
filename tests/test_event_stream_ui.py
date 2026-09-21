# -*- coding: utf-8 -*-
from __future__ import annotations

import threading

from event_stream_ui import EventStreamUI
from frontend_protocol import (
    DebugEvent,
    ErrorEvent,
    FinalEvent,
    FrontendChannel,
    InputRequestEvent,
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
from tests.conftest import make_command, make_plan


def test_live_start_stop_events():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.start_live()
    ui.stop_live()
    drained = ch.drain()
    assert [type(e) for e in drained] == [LiveStartEvent, LiveStopEvent]


def test_update_live_emits_status():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.update_live("hi", "running", 2, progress=40, plan_step="edit", is_error=True)
    (event,) = ch.drain()
    assert isinstance(event, StatusEvent)
    assert event.user_message == "hi"
    assert event.status == "running"
    assert event.step == 2
    assert event.progress == 40
    assert event.plan_step == "edit"
    assert event.is_error is True


def test_print_plan_emits_plan_event():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    plan = make_plan()
    ui.print_plan(plan)
    (event,) = ch.drain()
    assert isinstance(event, PlanEvent)
    assert event.goal == plan.goal
    assert event.plan == list(plan.plan)
    assert event.success_criteria == list(plan.success_criteria)
    assert event.risks == list(plan.risks)


def test_print_step_header_emits_step_event():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.print_step_header(1, make_command("run_shell", {"command": "ls"}), 7.4)
    (event,) = ch.drain()
    assert isinstance(event, StepEvent)
    assert event.step == 1
    assert event.action_type == "run_shell"
    assert isinstance(event.action_args, dict)
    assert event.action_args == {"command": "ls"}
    assert event.reasoning_seconds == 7.4
    assert event.to_dict()["reasoning_seconds"] == 7.4


def test_print_step_header_defaults_reasoning_to_zero():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.print_step_header(1, make_command("run_shell", {"command": "ls"}))
    (event,) = ch.drain()
    assert event.reasoning_seconds == 0.0


def test_sandbox_notice_emitted_only_when_present():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.print_sandbox_notice("")
    assert ch.drain() == []
    ui.print_sandbox_notice("degraded")
    (event,) = ch.drain()
    assert isinstance(event, SandboxNoticeEvent)
    assert event.note == "degraded"


def test_message_and_result_and_debug_events():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.print_user_markdown("# hello")
    ui.print_result("done")
    ui.debug_print("trace")
    drained = ch.drain()
    assert isinstance(drained[0], MessageEvent)
    assert drained[0].markdown == "# hello"
    assert isinstance(drained[1], MessageEvent)
    assert drained[1].markdown == "done"
    assert isinstance(drained[2], DebugEvent)
    assert drained[2].message == "trace"


def test_print_error_emits_error_event():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.print_error("it broke")
    (event,) = ch.drain()
    assert isinstance(event, ErrorEvent)
    assert event.message == "it broke"


def test_render_user_markdown_returns_plain_str():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    result = ui.render_user_markdown("# heading", fallback_style="dim")
    assert isinstance(result, str)
    assert result == "# heading"


def test_print_final_redacts_secret():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    synthetic = "sk-proj-" + "u" * 24
    ui.print_final(f"token api_key={synthetic}")
    (event,) = ch.drain()
    assert isinstance(event, FinalEvent)
    assert synthetic not in event.message
    assert "[REDACTED]" in event.message


def test_step_header_redacts_secret_in_thought_and_args():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    synthetic = "sk-proj-" + "u" * 24
    command = make_command("run_shell", {"command": f"deploy --token {synthetic}"})
    command.thought = f"using api_key={synthetic}"
    ui.print_step_header(3, command)
    (event,) = ch.drain()
    assert synthetic not in event.thought
    assert synthetic not in str(event.action_args)


def test_live_markdown_stream_sequence():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    with ui.live_markdown() as update:
        update("a")
        update("ab")
    drained = ch.drain()
    assert [type(e) for e in drained] == [
        StreamStartEvent,
        StreamChunkEvent,
        StreamChunkEvent,
        StreamCommitEvent,
    ]
    stream_id = drained[0].stream_id
    assert all(e.stream_id == stream_id for e in drained)
    assert drained[1].text == "a"
    assert drained[2].text == "ab"
    assert drained[3].text == "ab"


def test_live_markdown_without_reasoning_emits_no_reasoning_events():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    with ui.live_markdown() as update:
        update("hi")
    drained = ch.drain()
    assert [type(e) for e in drained] == [
        StreamStartEvent,
        StreamChunkEvent,
        StreamCommitEvent,
    ]


def test_live_markdown_reasoning_channel_sequence():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    with ui.live_markdown() as update:
        update.reasoning("think")
        update.reasoning("thinking")
        update("answer")
    drained = ch.drain()
    assert [type(e) for e in drained] == [
        StreamStartEvent,
        ReasoningStartEvent,
        ReasoningChunkEvent,
        ReasoningChunkEvent,
        ReasoningCommitEvent,
        StreamChunkEvent,
        StreamCommitEvent,
    ]
    stream_id = drained[0].stream_id
    assert all(getattr(e, "stream_id", stream_id) == stream_id for e in drained)
    # Reasoning is sealed before the first answer token so thoughts never leak.
    reasoning_commit_index = [type(e) for e in drained].index(ReasoningCommitEvent)
    first_answer_index = [type(e) for e in drained].index(StreamChunkEvent)
    assert reasoning_commit_index < first_answer_index
    commit = drained[reasoning_commit_index]
    assert isinstance(commit.duration_seconds, float)
    assert commit.duration_seconds >= 0.0


def test_live_markdown_reasoning_start_only_on_first_token():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    with ui.live_markdown() as update:
        update.reasoning("a")
        update.reasoning("ab")
    drained = ch.drain()
    assert [type(e) for e in drained].count(ReasoningStartEvent) == 1
    assert [type(e) for e in drained].count(ReasoningCommitEvent) == 1


def test_choose_option_blocking_roundtrip():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    result: dict[str, object] = {}

    def worker():
        result["value"] = ui.choose_option(
            "Provider?",
            ["openai", "freeflow"],
            help_text="pick one",
        )

    t = threading.Thread(target=worker)
    t.start()
    event = ch.get(timeout=1)
    assert isinstance(event, InputRequestEvent)
    assert event.kind == "choice"
    assert event.options == [
        {"value": "openai", "label": "openai"},
        {"value": "freeflow", "label": "freeflow"},
    ]
    ch.respond(event.request_id, value="openai")
    t.join(timeout=1)
    assert not t.is_alive()
    assert result["value"] == "openai"


def test_prompt_text_blocking_roundtrip():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    result: dict[str, object] = {}

    def worker():
        result["value"] = ui.prompt_text("Name?", current_value="x")

    t = threading.Thread(target=worker)
    t.start()
    event = ch.get(timeout=1)
    assert isinstance(event, InputRequestEvent)
    assert event.kind == "text"
    assert event.current_value == "x"
    ch.respond(event.request_id, value="openai")
    t.join(timeout=1)
    assert not t.is_alive()
    assert result["value"] == "openai"


def test_prompt_secret_does_not_forward_current_value():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    result: dict[str, object] = {}

    def worker():
        result["value"] = ui.prompt_secret(
            "API key?",
            current_value="should-not-leak",
            help_text="paste it",
        )

    t = threading.Thread(target=worker)
    t.start()
    event = ch.get(timeout=1)
    assert isinstance(event, InputRequestEvent)
    assert event.kind == "secret"
    assert event.current_value == ""
    ch.respond(event.request_id, value="secret-value")
    t.join(timeout=1)
    assert not t.is_alive()
    assert result["value"] == "secret-value"


def test_verbose_is_settable_and_does_not_suppress():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    assert ui.verbose is False
    ui.verbose = True
    ui.debug_print("still emitted")
    (event,) = ch.drain()
    assert isinstance(event, DebugEvent)


def test_satisfies_agent_ui_surface():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    for name in (
        "start_live",
        "stop_live",
        "update_live",
        "print_plan",
        "print_step_header",
        "debug_print",
        "live_markdown",
        "render_user_markdown",
        "print_user_markdown",
        "print_final",
        "print_result",
        "print_error",
        "print_sandbox_notice",
        "choose_option",
        "prompt_text",
        "prompt_secret",
    ):
        assert callable(getattr(ui, name))
    assert hasattr(ui, "verbose")


def test_loop_reasoning_is_streamed_and_sealed_once():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.set_thinking("first")
    ui.set_thinking("first second")
    ui.end_thinking()
    ui.end_thinking()
    start, chunk1, chunk2, commit = ch.drain()
    assert isinstance(start, ReasoningStartEvent)
    assert [chunk1.text, chunk2.text] == ["first", "first second"]
    assert isinstance(commit, ReasoningCommitEvent)
    assert {start.stream_id, chunk1.stream_id, chunk2.stream_id, commit.stream_id} == {
        start.stream_id
    }


def test_loop_reasoning_is_not_reported_twice_on_the_step():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.set_thinking("thinking")
    ui.end_thinking()
    ch.drain()
    ui.print_step_header(1, make_command("run_shell", {"command": "ls"}), 4.0)
    (event,) = ch.drain()
    assert isinstance(event, StepEvent)
    assert event.reasoning_seconds == 0.0


def test_step_seals_reasoning_that_was_never_ended():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.set_thinking("thinking")
    ch.drain()
    ui.print_step_header(1, make_command("run_shell", {"command": "ls"}), 4.0)
    commit, step = ch.drain()
    assert isinstance(commit, ReasoningCommitEvent)
    assert isinstance(step, StepEvent)
    assert step.reasoning_seconds == 0.0


def test_new_call_resets_and_reasoning_free_step_keeps_its_seconds():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.set_thinking("")
    assert ch.drain() == []
    ui.print_step_header(1, make_command("run_shell", {"command": "ls"}), 2.5)
    (event,) = ch.drain()
    assert event.reasoning_seconds == 2.5


def test_loop_reasoning_is_redacted():
    ch = FrontendChannel()
    ui = EventStreamUI(ch)
    ui.set_thinking("key sk-abcdefghijklmnopqrstuvwxyz0123456789")
    _, chunk = ch.drain()
    assert "sk-abcdefghijklmnopqrstuvwxyz0123456789" not in chunk.text
