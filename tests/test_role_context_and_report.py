# -*- coding: utf-8 -*-
from __future__ import annotations


# ── BUG-1: read content survives into session history ───────────────────────

def test_harvest_run_outputs_captures_read_file_content(agent_factory):
    agent = agent_factory()
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_file", "args": {"path": "hypr.conf"}}},
            "feedback": {"ok": True, "data": {"path": "hypr.conf", "content": "bind = SUPER, Q, killactive"}},
        }
    )

    outputs = agent._harvest_run_outputs()

    joined = "\n".join(outputs)
    assert "hypr.conf" in joined
    assert "killactive" in joined


def test_harvest_run_outputs_captures_search_and_multi_read(agent_factory):
    agent = agent_factory()
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_multiple_files", "args": {}}},
            "feedback": {
                "ok": True,
                "data": {"files": [{"path": "a.conf", "content": "monitor=DP-1"}]},
            },
        }
    )
    agent._state.step_logs.append(
        {
            "step": 2,
            "status": "ok",
            "command": {"action": {"type": "search_code", "args": {}}},
            "feedback": {
                "ok": True,
                "data": {"query": "monitor", "results": [{"path": "a.conf", "line": 3, "text": "monitor=DP-1"}]},
            },
        }
    )

    joined = "\n".join(agent._harvest_run_outputs())

    assert "a.conf" in joined
    assert "monitor=DP-1" in joined


def test_harvest_marks_truncated_multi_read(agent_factory):
    """More files than the per-step cap must leave an explicit marker naming the
    hidden files — a silent drop would reintroduce BUG-1 in a new form."""
    agent = agent_factory()
    files = [{"path": f"f{i}.conf", "content": f"content-{i}"} for i in range(7)]
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_multiple_files", "args": {}}},
            "feedback": {"ok": True, "data": {"files": files}},
        }
    )

    joined = "\n".join(agent._harvest_run_outputs())

    assert "more file(s)" in joined
    assert "f6.conf" in joined


def test_chat_history_preamble_allows_admitting_absence():
    from agent import _CHAT_HISTORY_PREAMBLE

    low = _CHAT_HISTORY_PREAMBLE.lower()
    # Positive availability stays; the dangerous gag ("do not say it's missing")
    # is gone; honesty on a harvest miss is explicit.
    assert "available to you" in low
    assert "do not say" not in low
    assert "say so honestly" in low
    assert "inventing it" in low


def test_add_to_session_history_stores_read_content_for_next_role(agent_factory):
    """A coder run that reads a file must leave its content in session history,
    so a later chat turn can answer about it instead of reporting empty context."""
    agent = agent_factory()
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_file", "args": {"path": "hypr.conf"}}},
            "feedback": {"ok": True, "data": {"path": "hypr.conf", "content": "gaps_in = 5"}},
        }
    )

    agent._add_to_session_history("прочитай hypr.conf", "coder", "done")

    entry = agent._session_history[-1]
    assert "gaps_in = 5" in "\n".join(entry.get("outputs", []))
