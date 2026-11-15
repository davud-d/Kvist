# -*- coding: utf-8 -*-
"""Whole-session token, cache and cost totals, and a parent that sums its child sessions."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

import pytest

from kernel.agent import agent_model
from kernel.llm import pricing
from kernel.session.agent_logging import read_session_document
from kernel.session.session_store import run_session_op
from kernel.session.session_usage import UsageTotals, family_totals
from kernel.tests.conftest import NullUI

_PRICES = {
    "claude-sonnet-5": {"provider": "anthropic", "input": 2.0, "output": 10.0, "cache_read": 0.2, "cache_write": 2.5},
}


def _call(totals: UsageTotals, **fields: Any) -> None:
    totals.add_call(**{
        "input_tokens": 0, "output_tokens": 0, "cache_read_tokens": None, "cache_write_tokens": None,
        "cost_usd": None, "cost_approx": False, **fields,
    })


def test_calls_add_up_and_an_unreported_cache_stays_unreported():
    totals = UsageTotals()
    _call(totals, input_tokens=100, output_tokens=10, cost_usd=0.25)
    _call(totals, input_tokens=50, output_tokens=5, cost_usd=0.5)

    assert totals.to_dict() == {
        "input_tokens": 150, "output_tokens": 15, "calls": 2, "cost_usd": 0.75, "cost_approx": False,
    }

    # A cache count, once any call reports it, sums only the calls that did; a reported 0 counts as reported.
    _call(totals, input_tokens=10, cache_read_tokens=0, cost_usd=0.0)
    _call(totals, input_tokens=10, cache_read_tokens=7, cache_write_tokens=3, cost_usd=0.0)
    assert totals.cache_read_tokens == 7 and totals.cache_write_tokens == 3


def test_cost_stays_unknown_until_a_call_is_priced_and_a_missing_price_makes_it_approximate():
    totals = UsageTotals()
    _call(totals, input_tokens=10)
    assert totals.cost_usd is None and totals.cost_approx is True
    assert "cost_usd" not in totals.to_dict()

    _call(totals, input_tokens=10, cost_usd=0.5)
    assert totals.cost_usd == 0.5 and totals.cost_approx is True

    exact = UsageTotals()
    _call(exact, cost_usd=0.5)
    _call(exact, cost_usd=0.25, cost_approx=True)
    assert exact.cost_usd == 0.75 and exact.cost_approx is True


def test_a_damaged_stored_total_reads_as_zero_or_unknown_never_as_an_error():
    assert UsageTotals.from_document(None) is None
    assert UsageTotals.from_document([1]) is None
    damaged = UsageTotals.from_document({
        "input_tokens": -5, "output_tokens": 1.5, "cache_read_tokens": True, "cache_write_tokens": 7,
        "calls": "3", "cost_usd": float("nan"), "cost_approx": 1,
    })
    assert damaged == UsageTotals(cache_write_tokens=7)
    assert UsageTotals.from_document({"cost_usd": 2}) == UsageTotals(cost_usd=2.0)
    assert UsageTotals.from_document({"cost_usd": True}) == UsageTotals()


def test_calls_reported_from_several_threads_are_all_counted():
    totals = UsageTotals()

    def report() -> None:
        for _ in range(500):
            _call(totals, input_tokens=1, output_tokens=2, cache_read_tokens=1, cost_usd=0.5)

    threads = [threading.Thread(target=report) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert (totals.calls, totals.input_tokens, totals.output_tokens, totals.cache_read_tokens) == (2000, 2000, 4000, 2000)
    assert totals.cost_usd == 1000.0


def _document(name: str, parent: str | None = None, usage: dict | None = None, kind: str = "executor") -> dict:
    document: dict[str, Any] = {"version": 1, "session": name, "updated_at": 1.0}
    if parent is not None:
        document["service"] = {"kind": kind, "parent_session": parent}
    if usage is not None:
        document["usage_totals"] = usage
    return document


def test_a_parent_sums_two_executors_and_a_grandchild():
    documents = {
        "lead": _document("lead", usage={"input_tokens": 10, "output_tokens": 1, "calls": 1, "cost_usd": 0.25, "cost_approx": False}),
        "exec-a": _document("exec-a", "lead", {"input_tokens": 100, "output_tokens": 20, "cache_read_tokens": 60, "calls": 3}),
        "exec-b": _document("exec-b", "lead", {"input_tokens": 200, "output_tokens": 30, "calls": 4, "cost_usd": 0.5}),
        "grandchild": _document("grandchild", "exec-b", {"input_tokens": 1000, "output_tokens": 2, "calls": 1, "cost_usd": 1.0, "cost_approx": True}),
        "unrelated": _document("unrelated", usage={"input_tokens": 7, "calls": 1}),
        "idle": _document("idle"),
    }

    family = {name: totals.to_dict() for name, totals in family_totals(documents).items()}

    assert family["lead"] == {
        "input_tokens": 1310, "output_tokens": 53, "cache_read_tokens": 60, "calls": 9,
        "cost_usd": 1.75, "cost_approx": True,
    }
    assert family["exec-b"]["input_tokens"] == 1200 and family["exec-a"]["input_tokens"] == 100
    assert family["grandchild"]["input_tokens"] == 1000
    assert family["unrelated"]["input_tokens"] == 7
    # Nothing to sum below a session without usage and without children that have any.
    assert "idle" not in family


def test_a_session_without_totals_still_sums_its_children():
    family = family_totals({"lead": _document("lead"), "exec": _document("exec", "lead", {"input_tokens": 3, "calls": 1})})

    assert family["lead"].input_tokens == 3


def test_a_loop_in_the_parent_links_does_not_hang_and_counts_each_session_once():
    documents = {
        "a": _document("a", "b", {"input_tokens": 1}),
        "b": _document("b", "a", {"input_tokens": 2}),
        "self": _document("self", "self", {"input_tokens": 4}),
    }

    family = family_totals(documents)

    assert (family["a"].input_tokens, family["b"].input_tokens, family["self"].input_tokens) == (3, 3, 4)


def test_nothing_is_summed_when_no_session_has_totals():
    assert family_totals({"a": _document("a"), "b": _document("b", "a")}) == {}


# --- The agent counts every model call and keeps the totals in its session ---------------


def _two_calls(agent) -> None:
    agent_model._emit_usage(
        agent, model="claude-sonnet-5", input_tokens=1000, output_tokens=50, kind="step",
        cache_read_tokens=600, cache_write_tokens=100, usage_source="provider",
    )
    agent_model._emit_usage(agent, model="claude-nope", input_tokens=200, output_tokens=10, kind="report")


def test_two_turns_add_up_and_a_restart_continues_the_count(agent_factory, monkeypatch):
    monkeypatch.setattr(pricing, "_table", lambda: _PRICES)
    agent = agent_factory(provider="anthropic")
    agent.new_session("work")

    _two_calls(agent)
    agent.save_session()
    stored = read_session_document(agent)["usage_totals"]

    assert stored["input_tokens"] == 1200 and stored["output_tokens"] == 60 and stored["calls"] == 2
    assert (stored["cache_read_tokens"], stored["cache_write_tokens"]) == (600, 100)
    assert stored["cost_usd"] == pytest.approx(0.00147)
    # One call had no price, so the cost is a lower bound.
    assert stored["cost_approx"] is True

    restarted = agent_factory(provider="anthropic")
    assert restarted.current_session == "work"
    agent_model._emit_usage(restarted, model="claude-nope", input_tokens=5, output_tokens=1, kind="step")
    restarted.save_session()

    again = read_session_document(restarted)["usage_totals"]
    assert (again["input_tokens"], again["output_tokens"], again["calls"]) == (1205, 61, 3)


def test_a_call_is_counted_without_any_ui_listening(agent_factory):
    agent = agent_factory()
    agent._ui = NullUI()
    assert getattr(agent._ui, "print_usage", None) is None

    agent_model._emit_usage(agent, model="m", input_tokens=10, output_tokens=2, kind="step")

    assert (agent._usage_totals.input_tokens, agent._usage_totals.calls) == (10, 1)


def test_a_new_session_counts_from_zero_and_switching_back_restores_the_count(agent_factory, monkeypatch):
    monkeypatch.setattr(pricing, "_table", lambda: _PRICES)
    agent = agent_factory(provider="anthropic")
    agent.new_session("first")
    _two_calls(agent)

    agent.new_session("second")
    assert agent._usage_totals == UsageTotals()
    agent_model._emit_usage(agent, model="claude-nope", input_tokens=4, output_tokens=1, kind="step")
    agent.save_session()
    agent.switch_session("first")

    assert (agent._usage_totals.input_tokens, agent._usage_totals.calls) == (1200, 2)
    assert read_session_document(agent, "second")["usage_totals"]["input_tokens"] == 4


def test_a_session_nobody_called_a_model_in_stores_no_totals(agent_factory):
    agent = agent_factory()
    agent.new_session("quiet")
    agent._session_history = [{"goal": "g", "role": "coder", "result": "ok"}]
    agent.save_session()

    assert "usage_totals" not in read_session_document(agent)


def test_reset_clears_the_totals(agent_factory, monkeypatch):
    monkeypatch.setattr(pricing, "_table", lambda: _PRICES)
    agent = agent_factory(provider="anthropic")
    agent.new_session("work")
    _two_calls(agent)
    agent.save_session()
    workspace = agent.executor.workspace
    assert "usage" in _item(run_session_op(workspace, "status"), "work")

    run_session_op(workspace, "reset")

    assert "usage" not in _item(run_session_op(workspace, "status"), "work")
    assert agent_factory()._usage_totals == UsageTotals()


# --- What status and list report ----------------------------------------------------------


def _item(payload: dict, name: str) -> dict:
    return next(item for item in payload["session_items"] if item["name"] == name)


def _seed(workspace: Path, documents: dict[str, dict]) -> None:
    sessions = workspace / ".kvist" / "sessions"
    sessions.mkdir(parents=True)
    for name, document in documents.items():
        (sessions / f"{name}.json").write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def test_status_and_list_report_a_sessions_own_usage_and_the_total_with_its_children(tmp_path):
    _seed(tmp_path, {
        "lead": _document("lead", usage={"input_tokens": 10, "output_tokens": 1, "calls": 1, "cost_usd": 0.25}),
        "exec-a": _document("exec-a", "lead", {"input_tokens": 100, "calls": 3}),
        "quiet": _document("quiet"),
    })

    for op in ("status", "list"):
        payload = run_session_op(tmp_path, op)
        lead = _item(payload, "lead")
        assert list(lead["usage"]) == ["input_tokens", "output_tokens", "calls", "cost_usd", "cost_approx"]
        assert lead["usage"]["input_tokens"] == 10
        assert lead["usage_total"] == {
            "input_tokens": 110, "output_tokens": 1, "calls": 4, "cost_usd": 0.25, "cost_approx": False,
        }
        child = _item(payload, "exec-a")
        assert child["usage"] == child["usage_total"] and child["service"]["parent_session"] == "lead"
        assert "usage" not in _item(payload, "quiet") and "usage_total" not in _item(payload, "quiet")


def test_a_parent_without_totals_of_its_own_still_reports_the_total(tmp_path):
    _seed(tmp_path, {"lead": _document("lead"), "exec": _document("exec", "lead", {"input_tokens": 3, "calls": 1})})

    lead = _item(run_session_op(tmp_path, "list"), "lead")

    assert "usage" not in lead and lead["usage_total"]["input_tokens"] == 3


@pytest.mark.parametrize("kind", ["helper", "executor", "council_member"])
def test_a_child_session_is_never_active_never_resumable_and_says_what_it_is(tmp_path, kind):
    child = _document("child", "lead", kind=kind)
    child["checkpoint"] = {"goal": "work", "step": 2}
    child["updated_at"] = 900.0
    lead = _document("lead")
    lead["updated_at"] = 100.0
    _seed(tmp_path, {"lead": lead, "child": child})
    sessions = tmp_path / ".kvist" / "sessions"
    os.utime(sessions / "lead.json", (1_600_000_000, 1_600_000_000))
    os.utime(sessions / "child.json", (1_600_000_100, 1_600_000_100))

    payload = run_session_op(tmp_path, "list")

    # The child is the newest file, yet the person's session is the one they talk in.
    assert payload["active_session"] == "lead"
    assert payload["resumable_session"] is None
    assert _item(payload, "child")["service"] == {"kind": kind, "parent_session": "lead"}
