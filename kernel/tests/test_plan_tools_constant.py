# -*- coding: utf-8 -*-
"""The tool list of a run is fixed at its first native step; the plan phase turns calls away instead."""
from __future__ import annotations

import json

import pytest

from kernel.agent import agent_runtime
from kernel.agent.agent import LocalAgent
from kernel.agent.agent_state import PLAN_APPROVED, PLAN_OFFERED, PLAN_PLANNING, AgentConfig
from kernel.plan import plan_mode
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullPerfLogger, NullUI, make_command
from kernel.tests.test_plan_mode import PlanUI

_REASON = "Two designs fit, and they differ in what breaks."
_PLAN = "## Goal\nAdd retries."


def _agent(tmp_path, ui=None, role="coder") -> LocalAgent:
    agent = LocalAgent(
        client=FakeLLMClient(),
        executor=SafeExecutor(tmp_path),
        config=AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0),
        ui=ui if ui is not None else PlanUI(),
    )
    agent._perf_logger = NullPerfLogger()
    agent._state.current_agent_role = role
    agent._state.response_language = "en"
    agent._state.native_tools = True
    # The system prompt and the runtime prompt are not what these tests are about.
    agent._state.native_messages = [{"role": "system", "content": "s"}]
    return agent


@pytest.fixture
def offered(monkeypatch):
    """What the model is shown on each native step, as the JSON the provider receives."""
    import kernel.agent.agent_model as agent_model

    seen: list[list[dict]] = []
    monkeypatch.setattr(agent_model, "call_native_action_model", lambda agent, messages, tools: seen.append(list(tools)))
    return seen


def _step(agent, offered_tools):
    agent_runtime._call_native_turn(agent, "goal", 1)
    return offered_tools[-1]


def _names(tools):
    return [tool["function"]["name"] for tool in tools]


def _refusal(agent, command):
    """The feedback the run loop registers when it turns the command away, or None when it lets it run."""
    registered: list[dict] = []
    agent._register_feedback = lambda step, cmd, feedback: registered.append(feedback)
    return registered[0] if agent_runtime._reject_before_execution(agent, 1, command) else None


def _write():
    return make_command("write_file", {"path": "a.py", "content": "x"}, plan_step="edit")


# ── The list does not move ───────────────────────────────────────────────────


def test_the_tool_list_is_the_same_before_the_offer_while_planning_and_after_acceptance(tmp_path, offered):
    agent = _agent(tmp_path, PlanUI(choices=["plan", "accept"]))
    before = _step(agent, offered)

    plan_mode.offer_plan(agent, make_command("offer_plan", {"reason": _REASON}, plan_step="plan"))
    assert agent._state.plan_phase == PLAN_PLANNING
    planning = _step(agent, offered)

    plan_mode.propose_plan(agent, make_command("propose_plan", {"plan": _PLAN}, plan_step="plan"))
    assert agent._state.plan_phase == PLAN_APPROVED
    approved = _step(agent, offered)

    assert {"offer_plan", "propose_plan", "write_file", "read_file"} <= set(_names(before))
    assert _names(before) == _names(planning) == _names(approved)
    assert json.dumps(before) == json.dumps(planning) == json.dumps(approved)


def test_the_tool_list_is_the_same_after_the_person_declines_the_offer(tmp_path, offered):
    agent = _agent(tmp_path, PlanUI(choices=["go"]))
    before = _step(agent, offered)

    plan_mode.offer_plan(agent, make_command("offer_plan", {"reason": _REASON}, plan_step="plan"))
    assert agent._state.plan_phase == PLAN_OFFERED

    assert json.dumps(_step(agent, offered)) == json.dumps(before)


def test_the_tool_list_is_the_same_after_a_plan_nobody_accepts(tmp_path, offered):
    agent = _agent(tmp_path, PlanUI(choices=["plan", "revise"], texts=[""]))
    before = _step(agent, offered)

    plan_mode.offer_plan(agent, make_command("offer_plan", {"reason": _REASON}, plan_step="plan"))
    plan_mode.propose_plan(agent, make_command("propose_plan", {"plan": _PLAN}, plan_step="plan"))

    assert json.dumps(_step(agent, offered)) == json.dumps(before)


def test_a_run_nobody_can_answer_never_shows_the_plan_tools_and_keeps_that(tmp_path, offered):
    agent = _agent(tmp_path, NullUI())
    first = _step(agent, offered)
    agent._state.plan_phase = PLAN_APPROVED

    assert not set(_names(first)) & {"offer_plan", "propose_plan"}
    assert json.dumps(_step(agent, offered)) == json.dumps(first)


def test_a_resumed_planning_run_starts_with_the_plan_tools(tmp_path, offered):
    agent = _agent(tmp_path, PlanUI())
    agent._state.plan_phase = PLAN_PLANNING

    assert {"offer_plan", "propose_plan"} <= set(_names(_step(agent, offered)))


# ── What the phase forbids is refused, not hidden ────────────────────────────


def test_propose_plan_outside_plan_mode_is_refused_while_the_tool_is_offered(tmp_path, offered):
    agent = _agent(tmp_path)
    assert "propose_plan" in _names(_step(agent, offered))

    refusal = _refusal(agent, make_command("propose_plan", {"plan": _PLAN}, plan_step="plan"))

    assert refusal is not None and refusal["ok"] is False
    assert "plan mode" in refusal["error"]


@pytest.mark.parametrize("phase", [PLAN_PLANNING, PLAN_OFFERED, PLAN_APPROVED])
def test_offer_plan_after_a_plan_phase_began_is_refused(tmp_path, offered, phase):
    agent = _agent(tmp_path)
    _step(agent, offered)
    agent._state.plan_phase = phase

    refusal = _refusal(agent, make_command("offer_plan", {"reason": _REASON}, plan_step="plan"))

    assert refusal is not None and "already offered" in refusal["error"]


@pytest.mark.parametrize("action", [
    ("write_file", {"path": "a.py", "content": "x"}),
    ("append_file", {"path": "a.py", "content": "x"}),
    ("replace_in_file", {"path": "a.py", "old": "a", "new": "b"}),
])
def test_writes_while_planning_are_refused_not_unknown(tmp_path, offered, action):
    agent = _agent(tmp_path)
    agent._state.plan_phase = PLAN_PLANNING
    assert action[0] in _names(_step(agent, offered))

    refusal = _refusal(agent, make_command(action[0], action[1], plan_step="edit"))

    assert refusal is not None and refusal["ok"] is False
    assert "read-only" in refusal["error"] and "propose_plan" in refusal["error"]


def test_delegating_while_planning_is_refused(tmp_path, offered):
    agent = _agent(tmp_path)
    agent._state.plan_phase = PLAN_PLANNING

    refusal = _refusal(agent, make_command("delegate_task", {"task": "do it", "files": []}, plan_step="edit"))

    assert refusal is not None and "read-only" in refusal["error"]


def test_a_write_is_allowed_again_once_the_plan_is_accepted(tmp_path, offered):
    agent = _agent(tmp_path)
    agent._state.plan_phase = PLAN_APPROVED

    assert _refusal(agent, _write()) is None


# ── Only attached skills change the list, and only at its end ────────────────


def test_a_skill_attached_mid_run_is_appended_and_nothing_else_moves(tmp_path, offered, monkeypatch):
    agent = _agent(tmp_path)
    skill_tools = []
    monkeypatch.setattr(agent.skills, "tool_definitions", lambda: list(skill_tools))
    first = _step(agent, offered)

    skill_tools.append({"type": "function", "function": {"name": "echo__say", "parameters": {"type": "object"}}})
    agent._state.plan_phase = PLAN_PLANNING
    second = _step(agent, offered)
    skill_tools.append({"type": "function", "function": {"name": "mcp__docs__search", "parameters": {"type": "object"}}})
    third = _step(agent, offered)

    assert second[: len(first)] == first
    assert _names(second)[len(first):] == ["echo__say"]
    assert third[: len(second)] == second
    assert _names(third)[len(second):] == ["mcp__docs__search"]


def test_a_skill_attached_before_the_first_step_is_part_of_the_fixed_list(tmp_path, offered, monkeypatch):
    agent = _agent(tmp_path)
    tool = {"type": "function", "function": {"name": "echo__say", "parameters": {"type": "object"}}}
    monkeypatch.setattr(agent.skills, "tool_definitions", lambda: [tool])

    first = _step(agent, offered)
    second = _step(agent, offered)

    assert _names(first).count("echo__say") == 1
    assert first == second


def test_a_new_run_starts_from_a_new_list(tmp_path, offered):
    agent = _agent(tmp_path, NullUI())
    headless = _step(agent, offered)
    agent._reset_run_state()
    agent._state.current_agent_role = "coder"
    agent._state.native_messages = [{"role": "system", "content": "s"}]
    agent._ui = PlanUI()

    assert "offer_plan" not in _names(headless)
    assert "offer_plan" in _names(_step(agent, offered))
