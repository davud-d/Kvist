# -*- coding: utf-8 -*-
"""Kvist event -> ACP update translation.

The translator is the one place where two incompatible event shapes meet: ACP
message chunks are append-only deltas while Kvist's stream/reasoning/output
events are cumulative snapshots. Everything here is I/O-free, so these tests
pin the mapping without a client.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from acp import helpers, schema

from acp_events import AcpEventTranslator, action_tool_kind


def event(type: str, **fields) -> SimpleNamespace:
    """A frontend-protocol event stand-in with only the fields a handler reads."""
    return SimpleNamespace(type=type, **fields)


@pytest.fixture
def translator() -> AcpEventTranslator:
    t = AcpEventTranslator()
    t.workspace_root = "/ws"
    t.begin_turn()
    return t


def texts_of(updates) -> list[str]:
    out = []
    for update in updates:
        content = update.update.content
        out.append(content.text if hasattr(content, "text") else str(content))
    return out


# --- Snapshot to delta -------------------------------------------------------


def test_stream_snapshots_become_deltas(translator):
    first = translator.translate(event("stream_chunk", stream_id="s", text="Hello"))
    second = translator.translate(event("stream_chunk", stream_id="s", text="Hello world"))

    assert texts_of(first) == ["Hello"]
    assert texts_of(second) == [" world"]


def test_stream_commit_sends_only_the_unstreamed_suffix(translator):
    translator.translate(event("stream_chunk", stream_id="s", text="Hello world"))

    commit = translator.translate(event("stream_commit", stream_id="s", text="Hello world"))

    assert commit == []


def test_final_does_not_repeat_a_committed_stream(translator):
    translator.translate(event("stream_chunk", stream_id="s", text="Answer"))
    translator.translate(event("stream_commit", stream_id="s", text="Answer"))

    assert translator.translate(event("final", message="Answer", outcome="done")) == []


def test_final_reaches_the_client_when_nothing_was_streamed(translator):
    updates = translator.translate(event("final", message="Report", outcome="done"))

    assert texts_of(updates) == ["Report"]


def test_restarted_snapshot_replaces_rather_than_duplicates(translator):
    """A buffer reset upstream must not be diffed as if it grew."""
    translator.translate(event("stream_chunk", stream_id="s", text="Hello world"))

    restarted = translator.translate(event("stream_chunk", stream_id="s", text="Hi"))

    assert texts_of(restarted) == ["Hi"]


def test_reasoning_snapshots_become_thought_deltas(translator):
    first = translator.translate(event("reasoning_chunk", stream_id="s", text="think"))
    second = translator.translate(event("reasoning_chunk", stream_id="s", text="thinking"))

    assert texts_of(first) == ["think"]
    assert texts_of(second) == ["ing"]
    assert all(u.kind == "agent_thought_chunk" for u in first + second)


def test_separate_streams_do_not_share_a_buffer(translator):
    translator.translate(event("stream_chunk", stream_id="a", text="one"))
    other = translator.translate(event("stream_chunk", stream_id="b", text="one"))

    assert texts_of(other) == ["one"]


def test_turn_buffers_do_not_leak_into_the_next_turn(translator):
    translator.translate(event("stream_chunk", stream_id="s", text="Hello"))
    translator.end_turn()
    translator.begin_turn()

    fresh = translator.translate(event("stream_chunk", stream_id="s", text="Hello"))

    assert texts_of(fresh) == ["Hello"]


# --- Stop reasons ------------------------------------------------------------


def test_stop_reason_defaults_to_end_turn(translator):
    assert translator.stop_reason() == "end_turn"


def test_user_cancel_reports_cancelled(translator):
    translator.note_user_cancel()

    assert translator.stop_reason() == "cancelled"


def test_refusal_final_reports_refusal(translator):
    translator.translate(event("final", message="no", outcome="refused"))

    assert translator.stop_reason() == "refusal"


def test_step_budget_status_reports_max_turn_requests(translator):
    translator.translate(event("status", status="Step budget limit reached"))

    assert translator.stop_reason() == "max_turn_requests"


# --- Tool calls --------------------------------------------------------------


def test_step_opens_a_tool_call_named_by_call_id(translator):
    updates = translator.translate(
        event(
            "step",
            step=1,
            call_id="c1",
            action_type="run_shell",
            action_args={"command": "ls"},
            action_description="list files",
        )
    )

    assert [u.kind for u in updates] == ["tool_call"]
    assert updates[0].update.tool_call_id == "c1"
    assert updates[0].update.kind == "execute"
    assert updates[0].update.status == "in_progress"
    assert updates[0].update.title == "list files"


def test_parallel_calls_are_correlated_by_call_id(translator):
    translator.translate(
        event("step", step=1, call_id="a", action_type="read_file", action_args={"path": "x"})
    )
    translator.translate(
        event("step", step=2, call_id="b", action_type="write_file", action_args={"path": "y"})
    )

    first = translator.translate(event("action_result", step=1, call_id="a", ok=True, summary="ok"))

    assert first[0].update.tool_call_id == "a"


def test_step_without_call_id_gets_a_stable_fallback(translator):
    first = translator.translate(event("step", step=3, action_type="run_shell", action_args={}))

    assert first[0].update.tool_call_id == "kvist-step-3-single"


def test_action_result_marks_failure(translator):
    translator.translate(event("step", step=1, call_id="c", action_type="run_shell", action_args={}))

    updates = translator.translate(
        event("action_result", step=1, call_id="c", ok=False, summary="failed", output="boom")
    )

    assert updates[0].update.status == "failed"


def test_action_result_without_a_step_still_opens_the_call(translator):
    updates = translator.translate(
        event("action_result", step=1, call_id="late", ok=True, summary="ok", output="done")
    )

    assert updates[0].kind == "tool_call"
    assert updates[0].update.status == "completed"


def test_live_output_is_a_cumulative_snapshot(translator):
    translator.translate(event("step", step=1, call_id="c", action_type="run_shell", action_args={}))

    translator.translate(event("action_output", step=1, call_id="c", output="a"))
    updates = translator.translate(event("action_output", step=1, call_id="c", output="ab"))

    assert updates[0].kind == "tool_call_update"


def test_file_locations_are_absolute_inside_the_workspace(translator):
    updates = translator.translate(
        event(
            "action_result",
            step=1,
            call_id="c",
            action_type="write_file",
            ok=True,
            summary="wrote",
            path="src/app.py",
            first_line=7,
        )
    )

    location = updates[0].update.locations[0]
    assert location.path == "/ws/src/app.py"
    assert location.line == 7


def test_without_a_workspace_root_a_relative_path_is_not_faked(translator):
    translator.workspace_root = ""

    updates = translator.translate(
        event(
            "action_result",
            step=1,
            call_id="c",
            action_type="write_file",
            ok=True,
            summary="wrote",
            path="src/app.py",
        )
    )

    assert updates[0].update.locations[0].path == "src/app.py"


@pytest.mark.parametrize(
    ("action_type", "kind"),
    [
        ("read_file", "read"),
        ("write_file", "edit"),
        ("replace_in_file", "edit"),
        ("search_code", "search"),
        ("run_shell", "execute"),
        ("fetch_url", "fetch"),
        ("delegate_task", "other"),
        ("final_answer", "other"),
    ],
)
def test_action_types_map_to_acp_kinds(action_type, kind):
    assert action_tool_kind(action_type) == kind


# --- Metadata ----------------------------------------------------------------


def test_plan_renders_every_step(translator):
    updates = translator.translate(event("plan", plan=["Read the code", "Fix it"]))

    assert [u.kind for u in updates] == ["plan"]
    assert [entry.content for entry in updates[0].update.entries] == ["Read the code", "Fix it"]


def test_session_title_becomes_session_info_update(translator):
    updates = translator.translate(event("session_title", title="Fix the parser"))

    assert updates[0].update.title == "Fix the parser"


def test_usage_accumulates_and_reports_context_window(translator):
    translator.translate(
        event("usage", input_tokens=100, output_tokens=20, context_tokens=400, context_limit=1000)
    )

    updates = translator.translate(
        event("usage", input_tokens=50, output_tokens=5, context_tokens=420, context_limit=1000)
    )

    assert translator.usage == {"input": 150, "output": 25, "total": 175}
    assert updates[0].kind == "usage_update"
    assert updates[0].update.used == 420
    assert updates[0].update.size == 1000


def test_usage_without_a_limit_sends_no_usage_update(translator):
    updates = translator.translate(event("usage", input_tokens=10, output_tokens=5))

    assert updates == []


def test_error_is_visible_and_recorded(translator):
    updates = translator.translate(event("error", message="provider refused"))

    assert texts_of(updates) == ["provider refused"]
    assert translator.errors == ["provider refused"]


def test_unknown_events_are_ignored(translator):
    assert translator.translate(event("mystery_event", payload=1)) == []
    assert translator.translate(event("status", status="working")) == []
