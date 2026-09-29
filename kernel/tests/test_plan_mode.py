# -*- coding: utf-8 -*-
"""Plan mode: the model offers a plan, explores read-only, the person decides."""
from __future__ import annotations

import pytest

from kernel import plan_mode
from kernel.agent import LocalAgent
from kernel.agent_context import render_session_history
from kernel.agent_model import build_role_system_prompt
from kernel.agent_state import PLAN_APPROVED, PLAN_OFFERED, PLAN_PLANNING, AgentConfig
from kernel.executor import SafeExecutor
from kernel.parser import CommandValidationError, validate_action_args
from kernel.tests.conftest import FakeLLMClient, NullPerfLogger, NullUI, make_command

_REASON = "Two designs fit, and they differ in what breaks."
_PLAN = "## Goal\nAdd retries.\n\n## Key changes\n- retry in client.py"


class PlanUI(NullUI):
    """A UI that answers dialogs from a script and remembers what it was shown."""

    def __init__(self, choices=(), texts=()):
        self.choices = list(choices)
        self.texts = list(texts)
        self.questions: list[tuple[str, list]] = []
        self.text_questions: list[str] = []
        self.plans: list[str] = []

    def ask_choice(self, title, options):
        self.questions.append((title, options))
        return self.choices.pop(0) if self.choices else None

    def prompt_text(self, title, current_value="", help_text=""):
        self.text_questions.append(title)
        return self.texts.pop(0) if self.texts else None

    def print_plan(self, markdown):
        self.plans.append(markdown)


def _agent(tmp_path, ui=None, **config) -> LocalAgent:
    agent = LocalAgent(
        client=FakeLLMClient(),
        executor=SafeExecutor(tmp_path),
        config=AgentConfig(
            persist_logs=False, api_request_interval=0, retry_base_delay=0, **config
        ),
        ui=ui if ui is not None else PlanUI(),
    )
    agent._perf_logger = NullPerfLogger()
    agent._state.current_agent_role = "coder"
    agent._state.response_language = "en"
    return agent


def _offer():
    return make_command("offer_plan", {"reason": _REASON}, plan_step="plan")


def _propose(plan=_PLAN):
    return make_command("propose_plan", {"plan": plan}, plan_step="plan")


def _planning_agent(tmp_path, ui=None):
    agent = _agent(tmp_path, ui)
    agent._state.plan_phase = PLAN_PLANNING
    return agent


# ── The offer ────────────────────────────────────────────────────────────────


def test_accepting_the_offer_makes_the_run_read_only(tmp_path):
    ui = PlanUI(choices=["plan"])
    agent = _agent(tmp_path, ui)

    step = plan_mode.offer_plan(agent, _offer())

    assert agent._state.plan_phase == PLAN_PLANNING
    assert step.feedback["data"]["answer"] == "plan"
    assert not step.ended
    title, options = ui.questions[0]
    assert _REASON in title
    assert [value for value, _label in options] == ["plan", "go"]


def test_declining_the_offer_is_recorded_and_changes_nothing_else(tmp_path):
    agent = _agent(tmp_path, PlanUI(choices=["go"]))

    step = plan_mode.offer_plan(agent, _offer())

    assert agent._state.plan_phase == PLAN_OFFERED
    assert agent._state.plan_outcome == "declined"
    assert step.feedback["data"]["answer"] == "go"


def test_a_cancelled_offer_counts_as_starting_right_away(tmp_path):
    agent = _agent(tmp_path, PlanUI(choices=[None]))

    plan_mode.offer_plan(agent, _offer())

    assert agent._state.plan_phase == PLAN_OFFERED


def test_the_offer_dialog_speaks_the_response_language(tmp_path):
    ui = PlanUI(choices=["go"])
    agent = _agent(tmp_path, ui)
    agent._state.response_language = "ru"

    plan_mode.offer_plan(agent, _offer())

    title, options = ui.questions[0]
    assert "безопаснее" in title
    assert options[0][1] == "Сначала показать план"


@pytest.mark.parametrize(
    "setup, expected",
    [
        ("nobody_to_ask", "Nobody can be asked"),
        ("already_offered", "already offered"),
        ("read_only", "changes nothing"),
    ],
)
def test_an_offer_that_cannot_be_made_says_why(tmp_path, setup, expected):
    agent = _agent(tmp_path, NullUI() if setup == "nobody_to_ask" else PlanUI())
    if setup == "already_offered":
        agent._state.plan_phase = PLAN_OFFERED
    if setup == "read_only":
        agent._state.read_only_goal = True

    assert expected in plan_mode.rejection_reason(agent, _offer())


def test_a_reader_is_never_offered_a_plan(tmp_path):
    agent = _agent(tmp_path)
    agent._state.current_agent_role = "reader"

    assert not plan_mode.offer_available(agent)


# ── The proposal ─────────────────────────────────────────────────────────────


def test_accepting_the_plan_unlocks_writes_and_keeps_the_plan(tmp_path):
    ui = PlanUI(choices=["accept"])
    agent = _planning_agent(tmp_path, ui)

    step = plan_mode.propose_plan(agent, _propose())

    assert ui.plans == [_PLAN]
    assert agent._state.plan_phase == PLAN_APPROVED
    assert agent._state.approved_plan == _PLAN
    assert agent._state.plan_outcome == "approved"
    assert step.feedback["data"]["decision"] == "accepted"


def test_refusing_the_plan_sends_the_persons_words_as_an_instruction(tmp_path):
    ui = PlanUI(choices=["revise"], texts=["use tenacity,  not  a loop"])
    agent = _planning_agent(tmp_path, ui)

    step = plan_mode.propose_plan(agent, _propose())

    assert agent._state.plan_phase == PLAN_PLANNING
    assert not step.ended
    assert 'wrote: "use tenacity, not a loop"' in step.instruction
    assert "use tenacity" not in str(step.feedback)


@pytest.mark.parametrize(
    "ui",
    [
        PlanUI(choices=[None]),
        PlanUI(choices=["revise"], texts=[None]),
        PlanUI(choices=["revise"], texts=["   "]),
        NullUI(),
    ],
    ids=["dialog-cancelled", "no-text", "blank-text", "nobody-to-ask"],
)
def test_a_plan_nobody_accepts_ends_the_turn_on_the_plan(tmp_path, ui):
    agent = _planning_agent(tmp_path, ui)

    step = plan_mode.propose_plan(agent, _propose()) if hasattr(ui, "plans") else None
    if step is None:
        ui.print_plan = lambda markdown: None
        step = plan_mode.propose_plan(agent, _propose())

    assert step.ended
    assert agent._state.plan_outcome == "not_accepted"
    assert agent._state.plan_phase == PLAN_PLANNING
    assert _PLAN in plan_mode.ended_message(agent._state, _PLAN)


def test_a_plan_outside_plan_mode_is_turned_away(tmp_path):
    agent = _agent(tmp_path)

    assert "plan mode" in plan_mode.rejection_reason(agent, _propose())


# ── What the model sees ──────────────────────────────────────────────────────


def _tool_names(agent):
    from kernel.agent_model import _load_role_prompt
    from kernel.native_tools import tools_for_role

    tools = plan_mode.tools_for_phase(tools_for_role(_load_role_prompt("coder")), agent)
    return {tool["function"]["name"] for tool in tools}


def test_tools_follow_the_phase(tmp_path):
    agent = _agent(tmp_path)
    before = _tool_names(agent)
    agent._state.plan_phase = PLAN_PLANNING
    planning = _tool_names(agent)
    agent._state.plan_phase = PLAN_APPROVED
    after = _tool_names(agent)

    assert "offer_plan" in before and "propose_plan" not in before and "write_file" in before
    assert "propose_plan" in planning and "read_file" in planning
    assert not planning & {"write_file", "append_file", "replace_in_file", "delegate_task", "offer_plan"}
    assert "write_file" in after and not after & {"offer_plan", "propose_plan"}


def test_tools_leave_out_the_offer_when_nobody_can_answer(tmp_path):
    assert "offer_plan" not in _tool_names(_agent(tmp_path, NullUI()))


def test_the_plan_guidance_is_in_the_system_prompt_only_where_a_plan_can_happen(tmp_path):
    asked = build_role_system_prompt(_agent(tmp_path), "coder")
    headless = build_role_system_prompt(_agent(tmp_path, NullUI()), "coder")

    assert "== PLAN MODE ==" in asked
    assert "== PLAN MODE ==" not in headless


def test_the_runtime_prompt_states_the_phase(tmp_path):
    agent = _planning_agent(tmp_path)
    agent._state.user_goal = "add retries"
    agent._state.step_budget = 10

    planning = agent._build_runtime_prompt("add retries", 1)
    agent._state.plan_phase = PLAN_APPROVED
    agent._state.approved_plan = _PLAN
    approved = agent._build_runtime_prompt("add retries", 2)

    assert "Plan mode:" in planning and "Approved plan:" not in planning
    assert f"Approved plan:\n{_PLAN}" in approved and "Plan mode:" not in approved


def test_a_declined_plan_is_a_fact_in_the_next_turns_history():
    history = [
        {"goal": "rename foo", "role": "coder", "result": "done", "plan_outcome": "declined"},
        {"goal": "rewrite the client", "role": "coder", "result": _PLAN, "plan_outcome": "not_accepted"},
    ]

    rendered = render_session_history(history, include_outputs=False)

    assert "declined a plan for this turn" in rendered
    assert "did not accept the plan this turn ended with" in rendered


@pytest.mark.parametrize(
    "action, args",
    [("offer_plan", {}), ("offer_plan", {"reason": "x", "extra": 1}), ("propose_plan", {}), ("propose_plan", {"plan": " "})],
)
def test_plan_actions_validate_their_arguments(action, args):
    with pytest.raises(CommandValidationError):
        validate_action_args(action, args)


# ── The run loop ─────────────────────────────────────────────────────────────


def _call(name, **arguments):
    import json

    return f'<tool_call>{json.dumps({"name": name, "arguments": arguments})}</tool_call>'


def _run(tmp_path, monkeypatch, ui, responses, goal="create hello.py that prints hello"):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")
    agent = _agent(tmp_path, ui)
    prompts: list[str] = []

    def model(prompt: str) -> str:
        prompts.append(prompt)
        return responses[min(len(prompts) - 1, len(responses) - 1)]

    agent._call_action_model = model
    result = agent.run(goal, routed_role="coder")
    return agent, result, prompts


def test_a_run_writes_nothing_until_the_plan_is_accepted(tmp_path, monkeypatch):
    ui = PlanUI(choices=["plan", "accept"])
    responses = [
        f"Two ways to do this.\n{_call('offer_plan', reason=_REASON)}",
        _call("write_file", path="hello.py", content="print('early')\n"),
        _call("propose_plan", plan=_PLAN),
        _call("write_file", path="hello.py", content="print('hello')\n"),
        _call("read_file", path="hello.py"),
        "Created hello.py.",
    ]

    agent, result, prompts = _run(tmp_path, monkeypatch, ui, responses)

    assert (tmp_path / "hello.py").read_text() == "print('hello')\n"
    assert ui.plans == [_PLAN]
    assert "Plan mode" in prompts[1]
    assert "files stay read-only" in prompts[2].lower() or "read-only" in prompts[2].lower()
    assert f"Approved plan:\n{_PLAN}" in prompts[3]
    assert result == "Created hello.py."
    assert agent._session_history[-1]["plan_outcome"] == "approved"


def test_declining_the_offer_lets_the_run_write_at_once(tmp_path, monkeypatch):
    ui = PlanUI(choices=["go"])
    responses = [
        _call("offer_plan", reason=_REASON),
        _call("write_file", path="hello.py", content="print('hello')\n"),
        _call("read_file", path="hello.py"),
        "Created hello.py.",
    ]

    agent, _result, _prompts = _run(tmp_path, monkeypatch, ui, responses)

    assert (tmp_path / "hello.py").exists()
    assert ui.plans == []
    assert agent._session_history[-1]["plan_outcome"] == "declined"


def test_a_refused_plan_is_revised_and_proposed_again(tmp_path, monkeypatch):
    ui = PlanUI(choices=["plan", "revise", "accept"], texts=["also add a test"])
    responses = [
        _call("offer_plan", reason=_REASON),
        _call("propose_plan", plan="first draft"),
        _call("propose_plan", plan="second draft with a test"),
        _call("write_file", path="hello.py", content="print('hello')\n"),
        _call("read_file", path="hello.py"),
        "Created hello.py.",
    ]

    _agent_, _result, prompts = _run(tmp_path, monkeypatch, ui, responses)

    assert ui.plans == ["first draft", "second draft with a test"]
    assert 'wrote: "also add a test"' in prompts[2]
    assert (tmp_path / "hello.py").exists()


def test_a_turn_ends_on_the_plan_when_nobody_accepts_it(tmp_path, monkeypatch):
    ui = PlanUI(choices=["plan", None])
    responses = [_call("offer_plan", reason=_REASON), _call("propose_plan", plan=_PLAN)]

    agent, result, _prompts = _run(tmp_path, monkeypatch, ui, responses)

    assert _PLAN in result and "not accepted" in result
    assert not (tmp_path / "hello.py").exists()
    assert agent._session_history[-1]["plan_outcome"] == "not_accepted"


def test_ending_a_planning_turn_without_a_plan_is_not_accepted(tmp_path, monkeypatch):
    ui = PlanUI(choices=["plan", None])
    responses = [
        _call("offer_plan", reason=_REASON),
        "Which database do you want?",
        _call("propose_plan", plan=_PLAN),
    ]

    _agent_, _result, prompts = _run(tmp_path, monkeypatch, ui, responses)

    assert "the turn ends with propose_plan" in prompts[2]


def test_the_plan_phase_survives_a_checkpoint(tmp_path):
    agent = _planning_agent(tmp_path)
    agent._state.approved_plan = _PLAN
    agent._state.plan_phase = PLAN_APPROVED
    agent._save_checkpoint("add retries", 3)
    agent._reset_run_state()

    assert agent._load_checkpoint() == ("add retries", 3)

    assert agent._state.plan_phase == PLAN_APPROVED
    assert agent._state.approved_plan == _PLAN
