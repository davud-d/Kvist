# -*- coding: utf-8 -*-
from __future__ import annotations

import banner
from banner import (
    LivePanelState,
    live_panel_fields,
    render_live_panel,
    wrap_thinking,
)


def test_idle_state_shows_zeroed_scaffold():
    fields = live_panel_fields(LivePanelState(running=False))
    labels = [label for label, _ in fields]
    assert labels == [
        "State",
        "Action",
        "Step",
        "Tokens",
        "Files",
        "Elapsed",
        "Speed",
        "Model",
    ]
    values = dict(fields)
    assert values["State"] == "not started"
    assert values["Tokens"] == "0"
    assert values["Files"] == "0"


def test_running_state_fields_have_no_cost_row():
    state = LivePanelState(
        running=True,
        state_label="working",
        action="generating JSON",
        step=7,
        progress=62,
        plan_step="verify",
        tokens_in=12400,
        tokens_out=3100,
        files_changed=3,
        elapsed_seconds=64,
        tokens_per_second=142,
        model_tier="smart",
        escalated=True,
    )
    values = dict(live_panel_fields(state))
    assert "Cost" not in values
    assert values["Tokens"] == "Σ 15.5k"
    assert values["Files"] == "3 edited"
    assert values["Elapsed"] == "1m 04s"
    assert values["Speed"] == "142 tok/s"
    assert values["Model"] == "smart ↑"
    assert values["Step"] == "7 · verify · 62%"


def test_finished_run_keeps_totals_instead_of_zeroing():
    state = LivePanelState(
        running=False,
        finished=True,
        state_label="done",
        tokens_in=8000,
        tokens_out=1000,
        files_changed=1,
        elapsed_seconds=42,
    )
    values = dict(live_panel_fields(state))
    assert values["State"] == "done"
    assert values["Tokens"] == "Σ 9.0k"
    assert values["Files"] == "1 edited"
    assert values["Elapsed"] == "42s"


def test_running_without_reasoning_shows_a_note_not_a_blank_column():
    state = LivePanelState(running=True, state_label="working", thinking="")
    rows = render_live_panel(
        state, metrics_width=26, thinking_width=40, height=8
    )
    joined = " ".join(rows)
    assert "does not stream" in joined


def test_empty_thinking_gap_after_reasoning_seen_hides_the_note():
    # A streaming provider whose thinking buffer is momentarily empty must not
    # be mislabelled as "does not stream reasoning".
    state = LivePanelState(
        running=True, state_label="working", thinking="", reasoning_seen=True
    )
    rows = render_live_panel(
        state, metrics_width=26, thinking_width=40, height=8
    )
    assert "does not stream" not in " ".join(rows)


def test_wrap_thinking_keeps_only_the_last_lines():
    text = "word " * 200
    lines = wrap_thinking(text, width=20, height=3)
    assert len(lines) == 3
    assert all(len(line) <= 20 for line in lines)


def test_wrap_thinking_empty_when_no_text():
    assert wrap_thinking("", width=20, height=3) == []


def test_render_live_panel_has_fixed_height_and_bounded_width():
    state = LivePanelState(
        running=True,
        state_label="working",
        action="run_shell",
        step=4,
        progress=40,
        plan_step="edit",
        tokens_in=8000,
        tokens_out=1200,
        files_changed=2,
        elapsed_seconds=30,
        tokens_per_second=95,
        model_tier="fast",
        thinking="need to confirm the venv python resolves before spawning",
    )
    rows = render_live_panel(
        state, metrics_width=28, thinking_width=32, height=6
    )
    assert len(rows) == 6
    assert any("🤔" in row for row in rows)


def _demo() -> None:
    state = LivePanelState(
        running=True,
        state_label="working",
        action="generating JSON command",
        step=7,
        progress=62,
        plan_step="verify",
        tokens_in=12400,
        tokens_out=3100,
        files_changed=3,
        elapsed_seconds=64,
        tokens_per_second=142,
        model_tier="smart",
        escalated=True,
        thinking=(
            "the failing assert is in test_role_router; read the fixture "
            "first, then check how the route source gets set to keyword vs "
            "llm before I trust the escalation path"
        ),
    )
    for row in render_live_panel(
        state, metrics_width=26, thinking_width=34, height=8
    ):
        print(f"| {row}")
    _ = banner


if __name__ == "__main__":
    _demo()


def test_last_thought_fills_the_column_when_no_reasoning_streams():
    # Providers without a reasoning channel still write a thought per action.
    state = LivePanelState(
        running=True,
        state_label="working",
        thinking="",
        last_thought="Reading the executor to find the shell gate.",
    )
    joined = " ".join(
        render_live_panel(state, metrics_width=26, thinking_width=60, height=8)
    )
    assert "find the shell gate" in joined
    assert "no live reasoning" in joined
    assert "does not stream" not in joined


def test_live_reasoning_wins_over_the_last_thought():
    state = LivePanelState(
        running=True,
        state_label="working",
        thinking="Streaming right now",
        last_thought="Stale thought",
        reasoning_seen=True,
    )
    joined = " ".join(
        render_live_panel(state, metrics_width=26, thinking_width=60, height=8)
    )
    assert "Streaming right now" in joined
    assert "Stale thought" not in joined
