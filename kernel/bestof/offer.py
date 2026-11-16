# -*- coding: utf-8 -*-
"""The main agent offers best-of and waits for the person's yes.

``offer_best_of`` is the second way into best-of (the first is the typed ``/best-of``): when several solutions
are possible and a wrong one is expensive, the model asks. Nothing starts without a yes. A yes ends the current
turn with a short ``final``; the frontend loop then asks ``take_agreed`` for the follow-up and starts a best-of
turn with the person's own words as the task (``turns.take_follow_up``). A no goes back to the model as a note and
the run goes on.

The question is written for the person, so it names no internal roles; it says what will happen, how much more
it costs than an ordinary run (N attempts and a check: N + 1 times), and, only when this session has measured
turns, what that is in money or tokens.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.bestof import worktrees
from kernel.bestof.styles import DEFAULT_EXECUTORS
from kernel.plan import plan_mode
from kernel.plan.plan_mode import PlanStep
from kernel.protocol.protocol import AgentCommand

ACTION = "offer_best_of"
ANSWER_BEST_OF = "best_of"
ANSWER_GO = "go"

# The money or token counts below are shown only for a session that has at least this many of the person's
# messages: the first one is still being answered, so nothing has been measured yet.
_MIN_MESSAGES_FOR_AVERAGE = 2


@dataclass(slots=True)
class Offer:
    """What came of the offer, kept on the agent: ``state`` is the run it was made in (one offer per run)."""

    state: Any
    count: int
    task: str
    agreed: bool


def _offer(agent: Any) -> Offer | None:
    return getattr(agent, "_best_of_offer", None)


def person_task(agent: Any) -> str:
    """The person's own words of this turn, or an empty string when the turn is not an answer to them.

    A wake-up or a resumed turn has no message of the person's (its goal is the runtime's text), so best-of is
    not offered there: the task it would run is not theirs.
    """
    state = agent._state
    ledger = getattr(agent, "_message_ledger", None) or {}
    if state.message_id not in ledger.get("user_ids", ()):
        return ""
    return state.user_goal.strip()


def unavailable_reason(agent: Any) -> str:
    """Why best-of cannot be offered now, in the model's language (English), or an empty string."""
    state = agent._state
    if state.current_agent_role not in plan_mode.PLAN_ROLES or state.read_only_goal:
        return "Best-of is offered for a task that changes files, and this one does not."
    if getattr(agent, "is_helper", False) or not plan_mode.can_ask(agent._ui):
        return "Nobody can be asked in this run. Carry on with the task."
    if not getattr(agent, "bestof_follow_up", False):
        return "This interface cannot start best-of after an answer. Carry on with the task."
    offer = _offer(agent)
    if offer is not None and offer.state is state:
        return "Best-of was already offered in this run."
    if state.files_changed:
        return "Best-of starts from the committed project, and this run already changed files. Carry on with the task."
    if not person_task(agent):
        return "This turn is not the person's own request, so there is no task of theirs to run. Carry on."
    try:
        worktrees.preflight(Path(agent.executor.workspace))
    except worktrees.BestOfRefused as refused:
        return f"Best-of cannot run here: {str(refused).split(' / ')[0]}."
    return ""


def offer_available(agent: Any) -> bool:
    return not unavailable_reason(agent)


def tools_for_run(tools: list[dict[str, Any]], agent: Any) -> list[dict[str, Any]]:
    """The tool list with ``offer_best_of`` only when it can be offered at the start of the run.

    The list is fixed for the whole run (a changing list changes the cached prompt prefix), so a later change of
    circumstances is turned away by ``rejection_reason`` instead of by taking the tool back.
    """
    if offer_available(agent):
        return list(tools)
    return [tool for tool in tools if tool["function"]["name"] != ACTION]


def rejection_reason(agent: Any, command: AgentCommand) -> str:
    """Why ``command`` cannot run now, or an empty string."""
    if command.action.type != ACTION:
        return ""
    return unavailable_reason(agent)


def offer_best_of(agent: Any, command: AgentCommand) -> PlanStep:
    args = command.action.args
    reason = str(args["reason"]).strip()
    count = int(args.get("count", DEFAULT_EXECUTORS))
    language = agent._state.response_language
    answer = agent._ui.ask_choice(question_text(agent, reason, count), _options(language, count))
    agreed = answer == ANSWER_BEST_OF
    agent._best_of_offer = Offer(agent._state, count, person_task(agent) if agreed else "", agreed)
    if not agreed:
        note = "The person chose an ordinary run. Do the task yourself; best-of is not offered again in this run."
        return PlanStep(_ok({"answer": ANSWER_GO, "note": note}))
    note = "The person agreed. Your turn ends here and best-of starts as the next turn with their own request."
    return PlanStep(_ok({"answer": ANSWER_BEST_OF, "note": note}), ended=True, final_message=_started_text(language, count))


def take_agreed(agent: Any) -> tuple[int, str] | None:
    """``(count, task)`` once when the person said yes to the offer of this run, then nothing until the next yes."""
    offer = _offer(agent)
    if offer is None or not offer.agreed:
        return None
    offer.agreed = False
    return offer.count, offer.task


# ---------------------------------------------------------------- what the person reads


def question_text(agent: Any, reason: str, count: int) -> str:
    """The question: the model's reason first, then what happens, the cost multiple, and a measured amount if any."""
    ru = agent._state.response_language == "ru"
    spend = _expected_spend(agent, count + 1, ru)
    if ru:
        lines = [
            reason,
            f"Режим best-of: запустить {count} {_attempts_ru(count)} решить задачу параллельно, каждую в своей копии "
            "проекта, и оставить лучшую по итогам проверки. Твои файлы не меняются, пока ты не примешь результат.",
            f"Примерно в {count + 1} раз дороже обычного прогона: {count} {_attempts_ru(count)} и проверка."
            + (f" Это ≈ {spend} по средним прошлым ходам этой сессии." if spend else ""),
        ]
    else:
        lines = [
            reason,
            f"Best-of mode: run {count} independent attempts at the task in parallel, each in its own copy of the "
            "project, and keep the best one after a check. Your files do not change until you accept a result.",
            f"Roughly {count + 1} times the cost of an ordinary run: {count} attempts and a check."
            + (f" That is ≈ {spend} by the average of this session's past turns." if spend else ""),
        ]
    return "\n".join(lines)


def _options(language: str, count: int) -> list[tuple[str, str]]:
    if language == "ru":
        return [(ANSWER_BEST_OF, f"Запустить best-of ({count})"), (ANSWER_GO, "Обычный прогон")]
    return [(ANSWER_BEST_OF, f"Start best-of ({count})"), (ANSWER_GO, "Ordinary run")]


def _started_text(language: str, count: int) -> str:
    if language == "ru":
        return f"Запускаю best-of: {count} {_attempts_ru(count)} решить твою задачу. Результат дождётся твоего решения."
    return f"Starting best-of: {count} attempts at your task. The result will wait for your decision."


def _attempts_ru(count: int) -> str:
    return "попытки" if count in (2, 3, 4) else "попыток"


def _expected_spend(agent: Any, runs: int, ru: bool) -> str:
    """What ``runs`` average turns of this session cost, from its own measured totals, or an empty string.

    Money when every call that reported a cost gave one (``cost_usd`` is known), tokens otherwise. Nothing is
    guessed: before the second message, or with no measured call, the question carries the multiple only.
    """
    ledger = getattr(agent, "_message_ledger", None) or {}
    messages = len(ledger.get("user_ids", ()))
    totals = getattr(agent, "_usage_totals", None)
    if messages < _MIN_MESSAGES_FOR_AVERAGE or totals is None:
        return ""
    measured = totals.to_dict()
    if not measured["calls"]:
        return ""
    if measured.get("cost_usd") is not None:
        return _money(measured["cost_usd"] / messages * runs)
    tokens = (measured["input_tokens"] + measured["output_tokens"]) / messages * runs
    return f"{_tokens(tokens)} {'токенов' if ru else 'tokens'}"


def _money(dollars: float) -> str:
    return f"${dollars:.2f}" if dollars >= 0.01 else "<$0.01"


def _tokens(tokens: float) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{round(tokens / 1_000)}k"
    return str(round(tokens))


def _ok(data: dict[str, Any]) -> dict[str, Any]:
    return {"ok": True, "action_type": ACTION, "data": data}
