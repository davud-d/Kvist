# -*- coding: utf-8 -*-
"""Plan mode: the model offers a plan, explores read-only, the person decides.

``offer_plan`` asks whether to plan before any change;
``propose_plan`` shows the plan and takes the person's decision. ``edit_plan``
revises it without losing work; ``cancel_plan`` releases the active task.
These run on
the agent's main thread and block on the frontend's dialog, so they share the
step, checkpoint and journal path of every other action.

While a plan is being made the run loop turns away every call that is not a
read, exactly as it does for the reader role
(agent_runtime._is_read_only_violation).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from kernel.agent.agent_state import PLAN_APPROVED, PLAN_OFFERED, PLAN_PLANNING, RunState
from kernel.protocol.protocol import AgentCommand

# Roles whose runs change files; a reader or a chat reply has nothing to plan.
PLAN_ROLES = frozenset({"coder", "devops", "universal"})
PLAN_ACTIONS = frozenset({"offer_plan", "propose_plan", "edit_plan", "cancel_plan"})
# Tools the model no longer sees while it is making the plan.
_HIDDEN_WHILE_PLANNING = frozenset({"write_file", "append_file", "replace_in_file", "delegate_task", "offer_plan"})

OUTCOME_DECLINED = "declined"
OUTCOME_NOT_ACCEPTED = "not_accepted"
OUTCOME_APPROVED = "approved"


@dataclass(slots=True, frozen=True)
class PlanStep:
    """What running a plan action gave back.

    ``instruction`` is the person's own words, sent on the user channel: the
    tool result is labelled untrusted data, and their feedback is not.
    ``ended`` is set when the turn ends on the plan instead of going on.
    """

    feedback: dict[str, Any]
    instruction: str = ""
    ended: bool = False
    final_message: str = ""


def can_ask(ui: Any) -> bool:
    return getattr(ui, "ask_choice", None) is not None


def offer_available(agent: Any) -> bool:
    """Whether the model may offer a plan now: a changing role, nothing offered yet, someone to ask."""
    state = agent._state
    return (
        state.current_agent_role in PLAN_ROLES
        and not state.plan_phase
        and not state.read_only_goal
        and can_ask(agent._ui)
    )


def tools_for_phase(tools: list[dict[str, Any]], agent: Any) -> list[dict[str, Any]]:
    """The role's tool definitions narrowed to what the plan phase allows."""
    planning = agent._state.plan_phase == PLAN_PLANNING
    hidden: set[str] = set()
    if not offer_available(agent):
        hidden.add("offer_plan")
    if planning:
        hidden |= _HIDDEN_WHILE_PLANNING
    else:
        hidden.add("propose_plan")
    return [tool for tool in tools if tool["function"]["name"] not in hidden]


def rejection_reason(agent: Any, command: AgentCommand) -> str:
    """Why a plan action cannot run now, or an empty string."""
    state = agent._state
    if command.action.type in {"edit_plan", "cancel_plan"}:
        return ""
    if command.action.type == "propose_plan":
        if state.plan_phase == PLAN_PLANNING:
            return ""
        return "propose_plan works only in plan mode, which starts when the person accepts offer_plan."
    if offer_available(agent):
        return ""
    if state.plan_phase:
        return "A plan was already offered in this run."
    if not can_ask(agent._ui):
        return "Nobody can be asked in this run. Carry on with the task."
    return "A plan is not offered for a task that changes nothing."


def offer_plan(agent: Any, command: AgentCommand) -> PlanStep:
    state = agent._state
    reason = str(command.action.args["reason"]).strip()
    if _ask_offer(agent, reason) == "plan":
        state.plan_phase = PLAN_PLANNING
        note = (
            "The person wants a plan first. Files are read-only now: explore with read-only "
            "calls, then call propose_plan."
        )
        return PlanStep(_ok("offer_plan", {"answer": "plan", "note": note}))
    state.plan_phase = PLAN_OFFERED
    state.plan_outcome = OUTCOME_DECLINED
    note = "The person chose to start right away. Do the task; no plan is wanted for it."
    return PlanStep(_ok("offer_plan", {"answer": "go", "note": note}))


def propose_plan(agent: Any, command: AgentCommand) -> PlanStep:
    state = agent._state
    plan = str(command.action.args["plan"]).strip()
    agent._ui.print_plan(plan)
    decision = _ask_decision(agent)
    if decision == "accept":
        state.plan_phase = PLAN_APPROVED
        state.approved_plan = plan
        state.plan_outcome = OUTCOME_APPROVED
        note = "The person accepted the plan. Files are writable again; carry the plan out."
        return PlanStep(_ok("propose_plan", {"decision": "accepted", "note": note}))
    changes = _ask_changes(agent) if decision == "revise" else ""
    if changes:
        return PlanStep(
            _ok("propose_plan", {"decision": "not accepted", "note": "Their words follow as an instruction."}),
            instruction=(
                f'The person did not accept the plan and wrote: "{changes}". '
                "Change the plan accordingly and call propose_plan again."
            ),
        )
    state.plan_outcome = OUTCOME_NOT_ACCEPTED
    return PlanStep(
        _ok("propose_plan", {"decision": "not accepted", "note": "The turn ends here."}),
        ended=True,
    )


def ended_message(state: RunState, plan: str) -> str:
    """What the person is left with when the turn ends on a plan nobody accepted."""
    if state.response_language == "ru":
        return f"{plan}\n\n_План не принят, изменения не вносились._"
    return f"{plan}\n\n_The plan was not accepted; nothing was changed._"


def edit_plan(agent: Any, command: AgentCommand) -> PlanStep:
    state = agent._state
    state.approved_plan = str(command.action.args["plan"]).strip()
    # Editing a draft cannot bypass the person's acceptance gate.
    if state.plan_phase != PLAN_PLANNING:
        state.plan_phase = PLAN_APPROVED
    return PlanStep(_ok("edit_plan", {"plan": state.approved_plan, "note": "Task context and evidence preserved."}))


def cancel_plan(agent: Any, command: AgentCommand) -> PlanStep:
    state = agent._state
    state.task_cancelled = True
    state.finished_incomplete = True
    reason = str(command.action.args["reason"]).strip()
    message = f"Задача отменена. {reason}" if state.response_language == "ru" else f"Task cancelled. {reason}"
    return PlanStep(_ok("cancel_plan", {"reason": reason}), ended=True, final_message=message)


def _ok(action_type: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "action_type": action_type, "data": data}


def _ask_offer(agent: Any, reason: str) -> str | None:
    ru = agent._state.response_language == "ru"
    if ru:
        title = f"{reason}\nСначала план — безопаснее; сразу делать — быстрее, но рискованнее."
        options = [("plan", "Сначала показать план"), ("go", "Сразу делать")]
    else:
        title = f"{reason}\nA plan first is safer; starting right away is faster but riskier."
        options = [("plan", "Show the plan first"), ("go", "Start right away")]
    return agent._ui.ask_choice(title, options)


def _ask_decision(agent: Any) -> str | None:
    if not can_ask(agent._ui):
        return None
    if agent._state.response_language == "ru":
        title = "Принять этот план?"
        options = [("accept", "Принять и приступить"), ("revise", "Нет, нужны правки")]
    else:
        title = "Accept this plan?"
        options = [("accept", "Accept and start"), ("revise", "No, I want changes")]
    return agent._ui.ask_choice(title, options)


def _ask_changes(agent: Any) -> str:
    ask = getattr(agent._ui, "prompt_text", None)
    if ask is None:
        return ""
    ru = agent._state.response_language == "ru"
    answer = ask("Что изменить в плане?" if ru else "What should change in the plan?")
    return " ".join(str(answer or "").split())
