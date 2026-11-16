# -*- coding: utf-8 -*-
"""The executors of a best-of run: N agents, each in its own git worktree, each with its own working style.

An executor is a ``LocalAgent`` like a helper (own thread, own context, a child session that the person can
read but never types into, no helpers of its own, nobody to ask), with three differences. It works in the
worktree that ``worktrees.create_run`` made for it, so it may change files freely; it is told one fixed task
text, the same bytes for every executor, so that attempts differ only by style; and its result is the diff
of that worktree, which this module records when it ends.

Start-up is staggered. The first executor starts at once; the others wait for the first answer of the model
to the first one (so the provider's prompt cache is warm), for the first executor to end, or for a timeout.

Approvals. The executor's folder is disposable, so overwriting a file inside it is never put to the person
(N parallel diff dialogs would be unusable): ``write_approval_enabled`` is off on the executor's own
``SafeExecutor``. A dangerous command goes through the main agent's gate when that gate can be answered from
any thread, and is refused otherwise, as for a writing helper.

The check (tests) runs through the executor's own sandbox, by the same ``run_shell`` path and gate as the
executor's own commands, in its worktree. Nothing here talks to a model of its own: ``wait`` sends no request.
"""
from __future__ import annotations

import copy
import hashlib
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Sequence

from kernel.agent.helpers import _HelperUI
from kernel.bestof import worktrees
from kernel.bestof.styles import style_name
from kernel.bestof.worktrees import BestOfRefused, GitError, RunInfo
from kernel.protocol.protocol import Action, AgentCommand
from kernel.sandbox.redaction import redact_text

EXECUTOR_MAX_STEPS = 80
# How long the others wait for the first executor's first answer before starting anyway.
STAGGER_TIMEOUT_SECONDS = 180.0
STOP_WAIT_SECONDS = 15.0
CHECK_TIMEOUT_SECONDS = 300.0
REPORT_CHARS = 4000
CHECK_SUMMARY_LINES = 10
CHECK_LINE_CHARS = 200
# Executors write code; the router would only spend a model call to say so.
ROLE = "coder"

WAITING = "waiting"
RUNNING = "running"
FINISHED = "finished"
FAILED = "failed"
STOPPED = "stopped"
ELIMINATED = "eliminated"
_ACTIVE = (WAITING, RUNNING)
_ENDED = (FINISHED, FAILED, STOPPED)

TESTS_UNKNOWN = "unknown"
TESTS_RUNNING = "running"
TESTS_PASSED = "passed"
TESTS_FAILED = "failed"

# The same bytes for every executor: the task, then this. No id, no style, no path.
EXECUTOR_INSTRUCTION = (
    "You are one of several independent attempts at this task. Each attempt works in its own copy of the "
    "repository and none sees the others' work; a reviewer compares the results afterwards, so the result "
    "must stand on its own.\n"
    "- Work only inside your folder: your changes are taken from there, and a file outside it is lost.\n"
    "- git is not available in your copy (it points outside your folder, which the sandbox forbids). The "
    "environment records your changes itself, so do not try to commit, stage or branch.\n"
    "- Nobody can answer questions. Decide, and say in your report what you assumed.\n"
    "- Run the project's tests if it has any, and fix what they show.\n"
    "- Finish with a short report: what you changed and how you checked it."
)
_RESTART_NOTE = (
    "The reviewer asks you to continue in this same copy of the repository, with everything you did so far "
    "still in place. Their instruction:\n{instruction}\n"
    "Finish with a short report again: what you changed since, and how you checked it."
)


class ExecutorError(Exception):
    """A request the pool cannot honour; the message says why, for the caller (the judge) to read."""


def executor_ids(count: int) -> tuple[str, ...]:
    """The ids ``create_run`` is called with: e1, e2, ..."""
    return tuple(f"e{number}" for number in range(1, count + 1))


@dataclass(slots=True)
class Executor:
    executor_id: str
    index: int
    style: str
    session: str
    branch: str
    workspace: Path
    agent: Any = None
    state: str = WAITING
    thread: threading.Thread | None = None
    # Set to let the thread start: at once for the first executor, on the release for the others.
    go: threading.Event = field(default_factory=threading.Event)
    steps: int = 0
    step_base: int = 0
    report: str = ""
    error: str = ""
    reason: str = ""
    # The state a stop request is heading for; it wins over whatever the run itself ended as.
    forced: str = ""
    tests: dict[str, str] = field(default_factory=lambda: {"state": TESTS_UNKNOWN, "summary": ""})
    diff: worktrees.DiffSummary | None = None
    inbox: deque = field(default_factory=deque)
    messages: list[dict[str, Any]] = field(default_factory=list)


class _ExecutorUI(_HelperUI):
    """A helper's silent UI, plus the two moments the pool cares about: a step began, the model answered."""

    def __init__(self, report: Any, on_step: Any, on_answer: Any) -> None:
        super().__init__(report)
        self._on_step = on_step
        self._on_answer = on_answer

    def print_step_header(self, step: int, command: Any, reasoning_seconds: float = 0.0) -> None:
        super().print_step_header(step, command, reasoning_seconds)
        self._on_step(step)

    def print_usage(self, usage: dict) -> None:
        self._on_answer()


class ExecutorPool:
    """Runs the executors of one best-of run and answers the judge's questions about them.

    Commands that need approval (``python3``, the project's tests) are never approved on an executor's behalf:
    without an OS-level boundary an auto-approved command would run with the user's rights outside the worktree,
    so they go to the main agent's gate or, when it cannot answer from another thread, are refused.
    """

    def __init__(self, main: Any, run: RunInfo, task: str, styles: Sequence[str]) -> None:
        ids = list(run.executors)
        if len(styles) != len(ids):
            raise ValueError(f"run {run.run_id} has {len(ids)} executors but {len(styles)} styles were given")
        for slug in styles:
            style_name(slug)
        self._main = main
        self._run = run
        self._goal = f"{task.strip()}\n\n{EXECUTOR_INSTRUCTION}"
        self._plan = list(zip(ids, styles))
        self._executors: dict[str, Executor] = {}
        self._cond = threading.Condition()
        self._timer: threading.Timer | None = None
        self._check_command: list[str] | None = None
        self._check_timeout = CHECK_TIMEOUT_SECONDS
        self._started = False

    # --- reading ---------------------------------------------------------

    def get(self, executor_id: str) -> Executor:
        executor = self._executors.get(executor_id)
        if executor is None:
            known = ", ".join(self._executors) or "none"
            raise ExecutorError(f"no executor {executor_id!r} in this run (known: {known})")
        return executor

    def executors(self) -> list[Executor]:
        return list(self._executors.values())

    def finished(self) -> list[str]:
        """Ids of the executors that ended on their own with a result (not failed, stopped or eliminated)."""
        return [executor.executor_id for executor in self._executors.values() if executor.state == FINISHED]

    def summary(self, executor_id: str) -> dict[str, Any]:
        """What a judge needs to compare this executor, without the full diff (see ``full_diff``)."""
        executor = self.get(executor_id)
        summary: dict[str, Any] = {
            "executor_id": executor_id,
            "style": executor.style,
            "state": executor.state,
            "steps": executor.steps,
            "diff": _diff_counts(executor.diff),
            "files": [change.path for change in executor.diff.files] if executor.diff else [],
            "stat": executor.diff.stat if executor.diff else "",
            "tests": dict(executor.tests),
            "report": _cut(redact_text(executor.report), REPORT_CHARS),
            "usage": executor.agent._usage_totals.to_dict(),
        }
        if executor.reason:
            summary["reason"] = executor.reason
        if executor.error:
            summary["error"] = executor.error
        return summary

    def full_diff(self, executor_id: str, path: str | None = None) -> str:
        self.get(executor_id)
        return redact_text(worktrees.full_diff(self._run, executor_id, path))

    def inspect(self, executor_id: str, cursor: int, limit: int) -> dict[str, Any]:
        """The executor's own actions of its latest run, bounded: calls, outcomes, safe command output."""
        logs = self.get(executor_id).agent._state.step_logs
        page = logs[cursor:cursor + limit]
        rows = []
        for log in page:
            command = (log.get("command") or {}).get("action") or {}
            feedback = log.get("feedback") or {}
            data = feedback.get("data") if isinstance(feedback.get("data"), dict) else {}
            rows.append({
                "step": log.get("step"),
                "status": log.get("status"),
                "action": command.get("type"),
                "args": {key: value for key, value in (command.get("args") or {}).items()
                         if key in ("path", "paths", "query", "pattern", "command", "url")},
                "error": redact_text(str(feedback.get("error") or log.get("error") or ""))[:500] or None,
                "output": redact_text(str(data.get("stdout") or data.get("content") or ""))[:800] or None,
            })
        return {"executor_id": executor_id, "items": rows, "next_cursor": cursor + len(page),
                "has_more": cursor + len(page) < len(logs), "untrusted": True}

    # --- starting --------------------------------------------------------

    def start(self) -> None:
        """Build every executor, announce them as ``waiting`` and let the first one go."""
        if self._started:
            raise ExecutorError("this run's executors were already started")
        self._started = True
        for index, (executor_id, style) in enumerate(self._plan):
            self._executors[executor_id] = self._build(index, executor_id, style)
        for executor in self._executors.values():
            self._status(executor)
        self._timer = threading.Timer(STAGGER_TIMEOUT_SECONDS, self._release)
        self._timer.daemon = True
        self._timer.start()
        first = next(iter(self._executors.values()))
        first.go.set()
        for executor in self._executors.values():
            self._launch(executor, self._goal)

    def _build(self, index: int, executor_id: str, style: str) -> Executor:
        from kernel.agent.agent import LocalAgent
        from kernel.sandbox.approvals import DenyApprovalGate
        from kernel.sandbox.executor import SafeExecutor
        from kernel.sandbox.scratchpad import Scratchpad

        main = self._main
        run = self._run
        executor = Executor(
            executor_id=executor_id, index=index, style=style, session=_session_name(run.run_id, executor_id),
            branch=run.executor_branch(executor_id), workspace=worktrees.executor_workspace(run, executor_id),
        )
        config = copy.copy(main.config)
        config.interactive = False
        config.end_session_enabled = False
        config.memory_auto_processing = False
        config.voice_mode = False
        config.max_steps = min(config.max_steps, EXECUTOR_MAX_STEPS)
        main_gate = main.executor.approvals
        gate = main_gate if getattr(main_gate, "answers_from_any_thread", False) else DenyApprovalGate()
        # A scratchpad of its own: attempts must not overwrite each other's throwaway scripts.
        # The worktree lies under <repo>/.kvist/bestof: without the flag the sandbox would refuse every command
        # for the folder's own location. Its own .kvist, below the worktree, stays forbidden.
        sandbox = SafeExecutor(executor.workspace, approvals=gate, scratchpad=Scratchpad(),
                               workspace_inside_kvist=True)
        sandbox.write_approval_enabled = False
        ui = _ExecutorUI(
            lambda payload: self._activity(executor, payload),
            lambda step: self._stepped(executor, step),
            lambda: self._answered(executor),
        )
        executor.agent = LocalAgent(
            main.client, sandbox, config, ui=ui, console=main.console, session=executor.session,
            session_workspace=main.session_workspace,
            service_session={"kind": "executor", "parent_session": main.current_session, "run_id": run.run_id,
                             "executor_id": executor_id, "style": style, "branch": executor.branch,
                             "parent_run": getattr(main, "_run_id", None)},
        )
        if not executor.agent.adopt_session_style(style_name(style)):
            raise ExecutorError(f"session {executor.session} already holds work, so it cannot take style {style}")
        # A title is set by hand, so the session never makes a model call of its own to name itself.
        executor.agent.rename_session(f"best-of {executor_id} · {style}")
        executor.agent.inbox_source = lambda: self._drain_inbox(executor)
        return executor

    def _launch(self, executor: Executor, goal: str) -> None:
        executor.thread = threading.Thread(target=self._work, args=(executor, goal), daemon=True,
                                           name=f"kvist-bestof-{executor.executor_id}")
        executor.thread.start()

    def _release(self) -> None:
        """Let the waiting executors start; idempotent."""
        if self._timer is not None:
            self._timer.cancel()
        for executor in self._executors.values():
            executor.go.set()

    # --- one executor's thread -------------------------------------------

    def _work(self, executor: Executor, goal: str) -> None:
        executor.go.wait()
        if executor.forced:
            self._finish(executor, executor.forced)
            return
        self._set_state(executor, RUNNING)
        self._finish(executor, self._run_agent(executor, goal))

    def _run_agent(self, executor: Executor, goal: str) -> str:
        agent = executor.agent
        try:
            text = agent.run(goal, routed_role=ROLE)
        except Exception as error:  # noqa: BLE001 - one executor's crash is its own result, never the run's
            executor.error = redact_text(f"{type(error).__name__}: {error}")[:1000]
            return FAILED
        executor.report = text or ""
        if agent.control.cancel_requested():
            return STOPPED
        # "completed" is sealed only by a real final answer; a run that ended on errors or on the step budget is
        # "blocked" without being marked incomplete, and must not pass for a finished attempt.
        return FINISHED if agent._state.run_status == "completed" else FAILED

    def _finish(self, executor: Executor, outcome: str) -> None:
        """Record what the executor left, then publish its final state and, if a check is set, run it."""
        final = executor.forced or outcome
        try:
            worktrees.record_result(self._run, executor.executor_id)
            executor.diff = worktrees.summary(self._run, executor.executor_id)
        except (BestOfRefused, GitError, OSError) as error:
            executor.error = redact_text(str(error))[:1000]
            final = FAILED if final == FINISHED else final
        executor.agent.executor.scratchpad.clear()
        if executor.index == 0:
            self._release()
        self._set_state(executor, final)
        if final in (FINISHED, FAILED) and self._check_command is not None:
            self._run_check(executor, self._check_command, self._check_timeout)

    # --- what the executor's UI reports ----------------------------------

    def _activity(self, executor: Executor, payload: dict[str, Any]) -> None:
        self._tell("print_executor_activity",
                   {"run_id": self._run.run_id, "executor_id": executor.executor_id, **payload})

    def _stepped(self, executor: Executor, step: int) -> None:
        total = executor.step_base + step
        if total <= executor.steps:
            return
        executor.steps = total
        self._status(executor)

    def _answered(self, executor: Executor) -> None:
        if executor.index == 0:
            self._release()

    # --- state and events ------------------------------------------------

    def _set_state(self, executor: Executor, state: str) -> None:
        with self._cond:
            if executor.state == state:
                return
            executor.state = state
            self._cond.notify_all()
        self._status(executor)

    def _status(self, executor: Executor) -> None:
        with self._cond:
            payload = {
                "run_id": self._run.run_id, "executor_id": executor.executor_id, "style": executor.style,
                "branch": executor.branch, "state": executor.state, "step": executor.steps,
                "tests": dict(executor.tests), "diff": _diff_counts(executor.diff),
                "usage": _contract_usage(executor.agent._usage_totals.to_dict()),
            }
        self._tell("print_bestof_status", payload)

    def _tell(self, printer_name: str, payload: dict[str, Any]) -> None:
        printer = getattr(self._main._ui, printer_name, None)
        if printer is not None:
            printer(payload)

    # --- the check -------------------------------------------------------

    def set_check(self, command: Sequence[str], timeout: float = CHECK_TIMEOUT_SECONDS) -> None:
        """The command (argv) that runs on every executor that ends from now on; executors already ended keep their result."""
        argv = _argv(command)
        with self._cond:
            self._check_command = argv
            self._check_timeout = float(timeout)

    def check(self, executor_id: str, command: Sequence[str] | None = None) -> dict[str, str]:
        """Run the check in this executor's worktree now, blocking until it ends; returns ``{state, summary}``."""
        executor = self.get(executor_id)
        with self._cond:
            argv = _argv(command) if command is not None else self._check_command
            if argv is None:
                raise ExecutorError("no check command: pass one, or set it with set_check first")
            if executor.state in _ACTIVE or executor.tests["state"] == TESTS_RUNNING:
                raise ExecutorError(f"{executor_id} is still working or already being checked; check it when it ends")
            timeout = self._check_timeout
        return self._run_check(executor, argv, timeout)

    def _run_check(self, executor: Executor, argv: list[str], timeout: float) -> dict[str, str]:
        self._set_tests(executor, {"state": TESTS_RUNNING, "summary": ""})
        command = AgentCommand("verify", Action("run_shell", {"command": argv, "timeout": timeout}))
        # The agent's own entry point: the same sandbox, the same gate and the same redaction as the executor's calls.
        self._set_tests(executor, _tests_from(executor.agent._execute_command(command)))
        return dict(executor.tests)

    def _set_tests(self, executor: Executor, tests: dict[str, str]) -> None:
        with self._cond:
            executor.tests = tests
            self._cond.notify_all()
        self._status(executor)

    # --- talking to an executor ------------------------------------------

    def message(self, executor_id: str, text: str) -> dict[str, Any]:
        executor = self.get(executor_id)
        with self._cond:
            if executor.state not in _ACTIVE:
                raise ExecutorError(f"{executor_id} is {executor.state}; use restart to give it new work")
            entry = {"id": f"m{len(executor.messages) + 1}", "text": text, "state": "sent", "at": time.time()}
            executor.messages.append(entry)
            executor.inbox.append(entry)
        return {"executor_id": executor_id, "message_id": entry["id"], "state": entry["state"]}

    def _drain_inbox(self, executor: Executor) -> list[str]:
        """Called by the executor's own loop right before a model request: what arrived since."""
        texts = []
        while executor.inbox:
            entry = executor.inbox.popleft()
            entry["state"] = "applied"
            texts.append(f"Message from the reviewer: {entry['text']}")
        return texts

    def restart(self, executor_id: str, instruction: str) -> dict[str, Any]:
        """Give an executor that ended new work in the same worktree, with its context and files as they were."""
        executor = self.get(executor_id)
        if executor.state in _ENDED and executor.thread is not None:
            # The thread may still be leaving: it publishes the state just before it ends.
            executor.thread.join(STOP_WAIT_SECONDS)
        with self._cond:
            if executor.state not in _ENDED or (executor.thread is not None and executor.thread.is_alive()):
                raise ExecutorError(f"{executor_id} is {executor.state}; only an executor that ended can be restarted")
            executor.forced = ""
            executor.reason = ""
            executor.error = ""
            executor.report = ""
            executor.step_base = executor.steps
            executor.tests = {"state": TESTS_UNKNOWN, "summary": ""}
        self._set_state(executor, RUNNING)
        self._launch(executor, _RESTART_NOTE.format(instruction=instruction.strip()))
        return {"executor_id": executor_id, "state": executor.state}

    # --- waiting and stopping --------------------------------------------

    def wait(self, timeout: float, executor_id: str | None = None) -> dict[str, Any]:
        """Block until the executor (or, without an id, every executor) has ended and been checked; no model request is made."""
        targets = [self.get(executor_id)] if executor_id else list(self._executors.values())
        started = time.monotonic()
        deadline = started + timeout
        with self._cond:
            while any(_in_progress(executor) for executor in targets):
                remaining = deadline - time.monotonic()
                if remaining <= 0 or self._main.control.interrupted():
                    break
                self._cond.wait(min(remaining, 0.5))
            done = not any(_in_progress(executor) for executor in targets)
        return {"done": done, "waited_seconds": round(time.monotonic() - started, 1),
                "executors": [self._row(executor) for executor in targets]}

    def _row(self, executor: Executor) -> dict[str, Any]:
        return {"executor_id": executor.executor_id, "style": executor.style, "state": executor.state,
                "steps": executor.steps, "tests": dict(executor.tests), "diff": _diff_counts(executor.diff)}

    def eliminate(self, executor_id: str, reason: str) -> dict[str, Any]:
        """Drop an executor from the comparison: stop it if it still works, keep its result, say why."""
        executor = self.get(executor_id)
        if executor.state == ELIMINATED:
            return self.summary(executor_id)
        self._request_stop(executor, ELIMINATED, reason)
        self._await_stop(executor)
        executor.reason = reason
        self._set_state(executor, ELIMINATED)
        self._tell("print_bestof_eliminated",
                   {"run_id": self._run.run_id, "executor_id": executor_id, "reason": reason})
        return self.summary(executor_id)

    def cancel_all(self, reason: str) -> list[str]:
        """Stop every executor that still works, with its result recorded, and mark the run cancelled."""
        active = [executor for executor in self._executors.values() if self._request_stop(executor, STOPPED, reason)]
        for executor in active:
            self._await_stop(executor)
        worktrees.mark(self._run, worktrees.STATE_CANCELLED)
        return [executor.executor_id for executor in active]

    def _request_stop(self, executor: Executor, state: str, reason: str) -> bool:
        """Mark a working executor as heading for ``state``; True if it was still working."""
        with self._cond:
            if executor.state not in _ACTIVE:
                return False
            executor.forced = state
            executor.reason = reason
        executor.go.set()
        return True

    def _await_stop(self, executor: Executor) -> None:
        """Wait for the thread to end. The cancel is repeated: one sent just before the run begins is cleared by it."""
        thread = executor.thread
        deadline = time.monotonic() + STOP_WAIT_SECONDS
        while thread is not None and thread.is_alive() and time.monotonic() < deadline:
            executor.agent.control.request_cancel("parent")
            thread.join(0.5)


def _session_name(run_id: str, executor_id: str) -> str:
    name = f"bo-{run_id}-{executor_id}".lower()
    if len(name) <= 64:
        return name
    return f"{name[:55]}-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:8]}"


def _argv(command: Sequence[str]) -> list[str]:
    argv = list(command)
    if not argv or not all(isinstance(item, str) for item in argv):
        raise ExecutorError("a check command is a non-empty list of strings")
    return argv


def _in_progress(executor: Executor) -> bool:
    return executor.state in _ACTIVE or executor.tests["state"] == TESTS_RUNNING


def _diff_counts(diff: worktrees.DiffSummary | None) -> dict[str, int]:
    if diff is None:
        return {"files": 0, "added": 0, "removed": 0}
    return {"files": len(diff.files), "added": diff.added, "removed": diff.removed}


def _contract_usage(usage: dict[str, Any]) -> dict[str, Any]:
    """The fields ``bestof_status`` carries (docs/UI_API.md section 7); a missing one stays missing, never 0."""
    keys = ("input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "cost_approx")
    return {key: usage[key] for key in keys if key in usage}


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n[... {len(text) - limit} characters omitted ...]"


def _tests_from(feedback: dict[str, Any]) -> dict[str, str]:
    """``passed``/``failed`` for a command that ran; ``unknown`` for one that was refused or never started."""
    data = feedback.get("data") if isinstance(feedback.get("data"), dict) else {}
    ran = "returncode" in data and not data.get("denied")
    text = "\n".join(str(data.get(stream) or "") for stream in ("stdout", "stderr"))
    if not ran:
        return {"state": TESTS_UNKNOWN,
                "summary": _last_lines(f"the check did not run: {feedback.get('error') or 'refused'}")}
    # The timeout note goes last: the summary keeps the end of the output.
    summary = _last_lines(f"{text}\nthe check timed out" if data.get("timed_out") else text)
    return {"state": TESTS_PASSED if feedback.get("ok") else TESTS_FAILED, "summary": summary}


def _last_lines(text: str) -> str:
    """The end of a command's output, where a test run says how it went."""
    lines = [line for line in text.splitlines() if line.strip()]
    return "\n".join(line[:CHECK_LINE_CHARS] for line in lines[-CHECK_SUMMARY_LINES:])
