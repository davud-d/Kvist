# -*- coding: utf-8 -*-
"""What the reviewer of a best-of run can do: the ``executor`` and ``verdict`` actions, on one run's pool.

The reviewer is the main agent in the internal role ``judge`` for the length of the ``/best-of`` turn. These two
actions are the only ones that reach the executors, and they exist for that role only: the runtime hands them to
``run_judge_action``, which refuses them for any other role or when no review is under way (no ``judge_tools`` on
the agent).

``executor`` talks to the ``ExecutorPool`` (its summaries, waiting, messages, the check, eliminating); it never
makes a model request of its own. ``verdict`` ends the review, and the code verifies it against what the pool
says the executors left, so the reviewer cannot name a winner that did not finish or made no change.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from kernel.bestof import executors as pool_states
from kernel.bestof.executors import ExecutorError, ExecutorPool
from kernel.bestof.worktrees import BestOfRefused, GitError
from kernel.protocol.protocol import AgentCommand

JUDGE_ROLE = "judge"
JUDGE_ACTIONS = frozenset({"executor", "verdict"})
DEFAULT_WAIT_SECONDS = 120.0
_WORKING = (pool_states.WAITING, pool_states.RUNNING)


class JudgeError(Exception):
    """A verdict or request the runtime refuses; the message tells the reviewer what to fix."""


@dataclass(slots=True)
class Verdict:
    """An accepted verdict. ``rows`` carry style, tests and diff taken from the pool, never from the reviewer."""

    rows: list[dict[str, Any]]
    winner: str
    reason: str


def _error(action_type: str, message: str) -> dict[str, Any]:
    return {"ok": False, "action_type": action_type, "error": message, "raw_error": message,
            "normalized_error": message}


class JudgeTools:
    """The reviewer's two actions for one run. ``verdict`` is set once a verdict passed every check."""

    def __init__(self, pool: ExecutorPool) -> None:
        self._pool = pool
        self.verdict: Verdict | None = None
        self._ops: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "status": self._status, "wait": self._wait, "inspect": self._inspect, "message": self._message,
            "start": self._start, "diff": self._diff, "check": self._check, "set_check": self._set_check,
            "eliminate": self._eliminate,
        }

    def run(self, command: AgentCommand) -> dict[str, Any]:
        """The action's feedback, in the shape the run loop expects from every tool."""
        action_type = command.action.type
        args = command.action.args
        try:
            data = self._accept_verdict(args) if action_type == "verdict" else self._ops[args["op"]](args)
        except (ExecutorError, BestOfRefused, GitError, JudgeError) as error:
            return _error(action_type, str(error))
        return {"ok": True, "action_type": action_type, "data": data}

    # --- executor ----------------------------------------------------------

    def _status(self, args: dict[str, Any]) -> dict[str, Any]:
        pool = self._pool
        return {"executors": [pool.summary(executor.executor_id) for executor in pool.executors()],
                "untrusted": True}

    def _wait(self, args: dict[str, Any]) -> dict[str, Any]:
        timeout = float(args.get("timeout", DEFAULT_WAIT_SECONDS))
        return self._pool.wait(timeout, args.get("executor_id"))

    def _inspect(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._pool.inspect(args["executor_id"], int(args.get("cursor", 0)), int(args.get("limit", 20)))

    def _message(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._pool.message(args["executor_id"], args["text"])

    def _start(self, args: dict[str, Any]) -> dict[str, Any]:
        return self._pool.restart(args["executor_id"], args["instruction"])

    def _diff(self, args: dict[str, Any]) -> dict[str, Any]:
        path = args.get("path")
        patch = self._pool.full_diff(args["executor_id"], path)
        return {"executor_id": args["executor_id"], "path": path, "patch": patch, "untrusted": True}

    def _check(self, args: dict[str, Any]) -> dict[str, Any]:
        result = self._pool.check(args["executor_id"], args.get("command"))
        return {"executor_id": args["executor_id"], **result, "untrusted": True}

    def _set_check(self, args: dict[str, Any]) -> dict[str, Any]:
        self._pool.set_check(args["command"])
        return {"check": list(args["command"]),
                "note": "it runs on each executor that ends from now on; use op check for those that already ended"}

    def _eliminate(self, args: dict[str, Any]) -> dict[str, Any]:
        summary = self._pool.eliminate(args["executor_id"], args["reason"])
        return {**summary, "untrusted": True}

    # --- verdict -----------------------------------------------------------

    def _accept_verdict(self, args: dict[str, Any]) -> dict[str, Any]:
        verdict = self._check_verdict(args)
        self.verdict = verdict
        return {"accepted": True, "winner": verdict.winner,
                "note": "The review is over; the person decides next. Do not call anything else."}

    def _check_verdict(self, args: dict[str, Any]) -> Verdict:
        pool = self._pool
        rows = args["rows"]
        known = [executor.executor_id for executor in pool.executors()]
        named = [row["executor_id"] for row in rows]
        unknown = sorted({name for name in named if name not in known})
        if unknown:
            raise JudgeError(f"no such executor in the verdict: {', '.join(unknown)} (the run has: {', '.join(known)})")
        repeated = sorted({name for name in named if named.count(name) > 1})
        if repeated:
            raise JudgeError(f"more than one row for: {', '.join(repeated)}; give exactly one row per executor")
        missing = [name for name in known if name not in named]
        if missing:
            raise JudgeError(f"no row for: {', '.join(missing)}; give exactly one row per executor")
        for name in known:
            self._require_settled(name)
        winners = [row["executor_id"] for row in rows if row["outcome"] == "winner"]
        if len(winners) != 1:
            raise JudgeError(f"exactly one row must have outcome winner; {len(winners)} do")
        if winners[0] != args["winner"]:
            raise JudgeError(f"'winner' is {args['winner']} but the winner row is {winners[0]}; they must match")
        self._require_finished_with_changes(winners[0])
        for row in rows:
            if row["outcome"] == "finalist":
                self._require_finished_with_changes(row["executor_id"])
        return Verdict([self._row(row) for row in rows], winners[0], args["reason"].strip())

    def _require_settled(self, executor_id: str) -> None:
        executor = self._pool.get(executor_id)
        if executor.state in _WORKING or executor.tests["state"] == pool_states.TESTS_RUNNING:
            raise JudgeError(f"{executor_id} is still working or being checked; wait for it (executor wait) or "
                             "eliminate it before the verdict")

    def _require_finished_with_changes(self, executor_id: str) -> None:
        summary = self._pool.summary(executor_id)
        if summary["state"] != pool_states.FINISHED:
            raise JudgeError(f"{executor_id} is {summary['state']}, not finished, so it cannot be a winner or finalist")
        if summary["diff"]["files"] == 0:
            raise JudgeError(f"{executor_id} made no changes, so it cannot be a winner or finalist")

    def _row(self, row: dict[str, Any]) -> dict[str, Any]:
        summary = self._pool.summary(row["executor_id"])
        return {"executor_id": row["executor_id"], "style": summary["style"], "outcome": row["outcome"],
                "tests": summary["tests"], "diff": summary["diff"], "note": str(row.get("note") or "").strip()}


def run_judge_action(agent: Any, command: AgentCommand) -> dict[str, Any]:
    """Run ``executor`` or ``verdict`` for the reviewer; any other caller is told these do not exist for it."""
    tools = getattr(agent, "judge_tools", None)
    if tools is None or agent._state.current_agent_role != JUDGE_ROLE:
        return _error(command.action.type, f"{command.action.type} is not available: it belongs to the review "
                                           "of a best-of run")
    return tools.run(command)
