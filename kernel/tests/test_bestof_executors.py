"""The executors of a best-of run: staggered start, styles, events, child sessions, approvals, check, stop."""
from __future__ import annotations

import copy
import json
import os
import re
import subprocess
import threading
import time
from itertools import groupby
from pathlib import Path
from types import SimpleNamespace

import pytest

from kernel.bestof import executors as executors_module
from kernel.bestof import worktrees
from kernel.bestof.executors import (
    EXECUTOR_INSTRUCTION,
    REPORT_CHARS,
    ExecutorError,
    ExecutorPool,
    executor_ids,
)
from kernel.bestof.styles import styles_for
from kernel.protocol.protocol import Action, AgentCommand
from kernel.sandbox.approvals import DenyApprovalGate
from kernel.sandbox.executor import ExecutionResult, SafeExecutor
from kernel.tests.conftest import NullUI

RUN_ID = "run1"


@pytest.fixture(autouse=True)
def _no_native_probe(monkeypatch):
    # The probe would spend a scripted reply on the tool-calling check.
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


@pytest.fixture
def repo(tmp_path, tmp_path_factory, monkeypatch):
    """A git repository at tmp_path with one commit; git sees neither the user's config nor their hooks."""
    config = tmp_path_factory.mktemp("gitconfig") / "empty"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
        subprocess.run(["git", *command], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def _git_status(repo) -> str:
    return subprocess.run(["git", "status", "--porcelain"], cwd=repo, check=True, capture_output=True,
                          text=True).stdout


def _call(name, arguments):
    return f'<tool_call>{json.dumps({"name": name, "arguments": arguments})}</tool_call>'


def _style_of(kwargs) -> str:
    for message in kwargs["messages"]:
        if message["role"] == "system":
            found = re.search(r"WORKING STYLE: ([a-z-]+)", message["content"])
            return found.group(1) if found else "?"
    return "?"


class StyledClient:
    """One model client for all executors; each is told apart by the style in its system prompt.

    ``scripts`` maps a style to its replies in order; ``gates`` holds a style's answers until released;
    ``events`` is the order in which requests arrived and answers left.
    """

    def __init__(self, scripts=None, gates=None, failing=()):
        self.scripts = {style: list(replies) for style, replies in (scripts or {}).items()}
        self.gates = gates or {}
        self.failing = set(failing)
        self.requests: list[tuple[str, list[dict]]] = []
        self.events: list[str] = []
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        style = _style_of(kwargs)
        with self._lock:
            self.requests.append((style, copy.deepcopy(kwargs["messages"])))
            self.events.append(f"ask:{style}")
        gate = self.gates.get(style)
        if gate is not None:
            gate.wait(10)
        if style in self.failing:
            raise RuntimeError("the provider is down")
        with self._lock:
            replies = self.scripts.get(style)
            text = replies.pop(0) if replies else "Nothing more to report."
            self.events.append(f"answer:{style}")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)

    def asked(self, style: str) -> list[list[dict]]:
        with self._lock:
            return [messages for asking, messages in self.requests if asking == style]


class Watching(NullUI):
    """The main interface: keeps what the pool tells it."""

    def __init__(self):
        self.statuses: list[dict] = []
        self.activity: list[dict] = []
        self.eliminated: list[dict] = []

    def print_bestof_status(self, payload):
        self.statuses.append(payload)

    def print_executor_activity(self, payload):
        self.activity.append(payload)

    def print_bestof_eliminated(self, payload):
        self.eliminated.append(payload)

    def states(self, executor_id: str) -> list[str]:
        return [state for state, _ in groupby(p["state"] for p in self.statuses if p["executor_id"] == executor_id)]


class RecordingGate:
    """A main-agent gate that can be answered from any thread; it records what it was asked."""

    answers_from_any_thread = True

    def __init__(self, approve: bool = False):
        self.approve = approve
        self.confirms: list[str] = []
        self.commands: list[list[str]] = []

    def confirm(self, title, body, question, timeout=None):
        self.confirms.append(title)
        return self.approve

    def confirm_command(self, command, reason, timeout=None):
        self.commands.append(command)
        return self.approve

    def confirm_fetch_domain(self, host, url, timeout=None):
        return "deny"

    def commit_remembered_rule(self):
        return None

    def waiting_confirm_command(self):
        return None


class CheckSeam:
    """Stands in for the sandbox at its edge for one marker command, and records where it was run."""

    MARKER = "kvist-check"

    def __init__(self, monkeypatch):
        self.calls: list[tuple] = []
        self.result = ExecutionResult(ok=True, action_type="run_shell", data={
            "command": [self.MARKER], "returncode": 0, "stdout": "3 passed in 0.1s\n", "stderr": "",
            "timed_out": False,
        })
        self.error: Exception | None = None
        real = SafeExecutor.execute

        def execute(sandbox, command):
            args = command.action.args
            if command.action.type != "run_shell" or args["command"][0] != self.MARKER:
                return real(sandbox, command)
            self.calls.append((sandbox.workspace, args))
            if self.error is not None:
                raise self.error
            return self.result

        monkeypatch.setattr(SafeExecutor, "execute", execute)


def _until(predicate, seconds=10.0):
    deadline = time.monotonic() + seconds
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.02)
    return predicate()


def _main(agent_factory, client):
    agent = agent_factory(max_steps=12, stream=False)
    agent.client = client
    watching = Watching()
    object.__setattr__(agent._ui, "_ui", watching)
    agent._state.response_language = "en"
    agent._state.message_id = 1
    agent.control.begin()
    return agent, watching


def _pool(main, repo, count=2, task="Make the greeting friendlier."):
    run = worktrees.create_run(repo, RUN_ID, executor_ids(count))
    return ExecutorPool(main, run, task, styles_for(count))


# --- start-up ------------------------------------------------------------


def test_the_second_executor_asks_the_model_only_after_the_first_was_answered(agent_factory, repo):
    gate = threading.Event()
    client = StyledClient(gates={"senior-dev": gate})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)

    pool.start()
    assert _until(lambda: client.asked("senior-dev"))
    time.sleep(0.3)  # time enough for a wrongly eager second executor to have asked

    assert client.events == ["ask:senior-dev"]
    assert watching.states("e2") == ["waiting"]
    gate.set()
    assert pool.wait(10)["done"]
    assert client.events.index("ask:minimalist") > client.events.index("answer:senior-dev")
    assert watching.states("e2") == ["waiting", "running", "finished"]


def test_the_others_start_on_their_own_when_the_first_gets_no_answer_in_time(agent_factory, repo, monkeypatch):
    monkeypatch.setattr(executors_module, "STAGGER_TIMEOUT_SECONDS", 0.2)
    gate = threading.Event()
    client = StyledClient(gates={"senior-dev": gate})
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo)

    pool.start()

    assert _until(lambda: client.asked("minimalist"))
    assert "answer:senior-dev" not in client.events
    gate.set()
    assert pool.wait(10)["done"]


def test_the_others_start_when_the_first_fails_before_any_answer(agent_factory, repo):
    client = StyledClient(failing=["senior-dev"])
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo)

    pool.start()
    waited = pool.wait(20)

    assert waited["done"]
    states = {row["executor_id"]: row["state"] for row in waited["executors"]}
    assert states["e1"] == "failed"
    assert states["e2"] == "finished"
    # The first one never got an answer, so the 180 s timeout was not what released the second.
    assert client.events.index("ask:minimalist") > client.events.index("ask:senior-dev")


def test_every_executor_gets_the_same_task_bytes_and_only_its_own_style(agent_factory, repo):
    client = StyledClient()
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo, count=3, task="  Make the greeting friendlier.  ")

    pool.start()
    assert pool.wait(15)["done"]

    goals = [executor.agent._state.user_goal.encode("utf-8") for executor in pool.executors()]
    assert len(set(goals)) == 1
    goal = goals[0].decode("utf-8")
    assert goal == f"Make the greeting friendlier.\n\n{EXECUTOR_INSTRUCTION}"
    # Nothing that tells the executors apart is in the task: not an id, a style, a branch or a path.
    for executor in pool.executors():
        for private in (executor.executor_id, executor.style, executor.branch, str(executor.workspace), RUN_ID):
            assert private not in goal
    for style, other_styles in (("senior-dev", ("minimalist", "paranoid")), ("minimalist", ("senior-dev", "paranoid")),
                                ("paranoid", ("senior-dev", "minimalist"))):
        first_request = client.asked(style)[0]
        system = next(message["content"] for message in first_request if message["role"] == "system")
        user = " ".join(message["content"] for message in first_request if message["role"] == "user")
        assert f"WORKING STYLE: {style}" in system
        assert not any(f"WORKING STYLE: {other}" in system for other in other_styles)
        assert goal in user


def test_the_executor_instruction_says_git_is_unavailable_and_asks_for_tests_and_a_report():
    assert "git is not available" in EXECUTOR_INSTRUCTION
    assert "tests" in EXECUTOR_INSTRUCTION and "report" in EXECUTOR_INSTRUCTION
    assert "only inside your folder" in EXECUTOR_INSTRUCTION


def test_the_pool_refuses_a_style_list_that_does_not_match_the_run(agent_factory, repo):
    main, _ = _main(agent_factory, StyledClient())
    run = worktrees.create_run(repo, RUN_ID, executor_ids(2))

    with pytest.raises(ValueError, match="2 executors but 3 styles"):
        ExecutorPool(main, run, "task", styles_for(3))
    with pytest.raises(ValueError, match="No agent style"):
        ExecutorPool(main, run, "task", ("senior-dev", "friendly"))


def test_a_pool_starts_once_and_knows_only_its_own_executors(agent_factory, repo):
    main, _ = _main(agent_factory, StyledClient())
    pool = _pool(main, repo)
    pool.start()

    with pytest.raises(ExecutorError, match="already started"):
        pool.start()
    with pytest.raises(ExecutorError, match=r"no executor 'e9'.*known: e1, e2"):
        pool.summary("e9")
    assert pool.wait(15)["done"]


# --- events and sessions ---------------------------------------------------


def test_the_main_interface_hears_each_executor_go_from_waiting_to_running_to_finished(agent_factory, repo):
    client = StyledClient(scripts={"senior-dev": [_call("list_dir", {"path": "."}), "Looked around."]})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)

    pool.start()
    assert pool.wait(15)["done"]

    for executor_id, style in (("e1", "senior-dev"), ("e2", "minimalist")):
        assert watching.states(executor_id) == ["waiting", "running", "finished"]
        last = [p for p in watching.statuses if p["executor_id"] == executor_id][-1]
        assert last["run_id"] == RUN_ID and last["style"] == style
        assert last["branch"] == f"kvist/bestof/{RUN_ID}/{executor_id}"
        assert last["tests"] == {"state": "unknown", "summary": ""}
        assert last["diff"] == {"files": 0, "added": 0, "removed": 0}
        assert {"input_tokens", "output_tokens"} <= set(last["usage"]) <= {
            "input_tokens", "output_tokens", "cache_read_tokens", "cost_usd", "cost_approx"}
    # The step counter only goes up, and the calls of the executor reached the main interface with its id.
    steps = [p["step"] for p in watching.statuses if p["executor_id"] == "e1"]
    assert steps == sorted(steps) and steps[-1] >= 1
    calls = [entry for entry in watching.activity if entry["action_type"] == "list_dir"]
    assert [entry["phase"] for entry in calls] == ["step", "result"]
    assert {(entry["run_id"], entry["executor_id"]) for entry in calls} == {(RUN_ID, "e1")}
    assert calls[0]["call_id"] == calls[1]["call_id"] and calls[1]["ok"] is True


def test_each_executor_has_a_child_session_beside_its_parents_that_it_stamps_with_its_id(agent_factory, repo):
    client = StyledClient()
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo)

    pool.start()
    assert pool.wait(15)["done"]

    sessions = repo / ".kvist" / "sessions"
    for executor in pool.executors():
        document = json.loads((sessions / f"{executor.session}.json").read_text(encoding="utf-8"))
        assert document["service"] == {
            "kind": "executor", "parent_session": main.current_session, "run_id": RUN_ID,
            "executor_id": executor.executor_id, "style": executor.style, "branch": executor.branch,
            "parent_run": None,
        }
        assert document["session"] == executor.session
        assert not (executor.workspace / ".kvist" / "sessions" / f"{executor.session}.json").exists()
        journal = sessions / f"{executor.session}.events.jsonl"
        assert journal.is_file(), "the child session's journal lies beside its document"
        events = [json.loads(line) for line in journal.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert events and all(event["event"].get("executor_id") == executor.executor_id for event in events)
    # The person's own session is untouched: executors never become the active session.
    assert main.current_session not in {executor.session for executor in pool.executors()}


def test_the_executors_style_is_in_its_session_document_and_survives_loading_the_session_again(agent_factory, repo):
    from kernel.agent.agent import LocalAgent
    from kernel.config.user_config import get_saved_style
    from kernel.session.agent_logging import read_session_document

    client = StyledClient()
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo, count=3)
    pool.start()
    assert pool.wait(15)["done"]
    paranoid = pool.get("e3")
    assert get_saved_style("agents") != "Paranoid"

    stored = read_session_document(main, paranoid.session)
    assert stored["styles"]["agents"] == "Paranoid"

    reopened = LocalAgent(client, SafeExecutor(paranoid.workspace), copy.copy(main.config), ui=NullUI(),
                          session=paranoid.session, session_workspace=main.session_workspace,
                          service_session=stored["service"])
    assert reopened.session_style("agents") == "Paranoid"
    assert stored["title"] == "best-of e3 · paranoid"


def test_an_executor_is_a_helper_with_no_helpers_and_bounded_steps(agent_factory, repo):
    main, _ = _main(agent_factory, StyledClient())
    main.config.max_steps = 500
    main.config.interactive = True
    pool = _pool(main, repo)
    pool.start()

    agent = pool.get("e1").agent
    assert agent.is_helper and agent.helpers is None
    assert agent.config.max_steps == executors_module.EXECUTOR_MAX_STEPS
    assert agent.config.interactive is False and agent.config.end_session_enabled is False
    assert agent.config.memory_auto_processing is False and agent.config.voice_mode is False
    assert agent.executor.workspace == worktrees.executor_workspace(pool._run, "e1")
    assert pool.wait(15)["done"]


# --- approvals and the sandbox ---------------------------------------------


def test_an_executor_writes_and_overwrites_files_in_its_worktree_without_asking_and_the_users_tree_stays(
        agent_factory, repo, monkeypatch):
    monkeypatch.setenv("AGENT_WRITE_APPROVAL", "1")
    gate = RecordingGate(approve=False)
    scripts = {"senior-dev": [
        _call("write_file", {"path": "a.txt", "content": "one\n"}),
        _call("write_file", {"path": "a.txt", "content": "two\n"}),
        _call("write_file", {"path": "new.txt", "content": "fresh\n"}),
        "Rewrote a.txt and added new.txt.",
    ]}
    client = StyledClient(scripts=scripts)
    main, _ = _main(agent_factory, client)
    main.executor._approvals = gate
    before = _git_status(repo)
    pool = _pool(main, repo)

    pool.start()
    assert pool.wait(15)["done"]

    worktree = pool.get("e1").workspace
    assert (worktree / "a.txt").read_text(encoding="utf-8") == "two\n"
    assert (worktree / "new.txt").read_text(encoding="utf-8") == "fresh\n"
    assert pool.get("e1").state == "finished"
    assert gate.confirms == []
    # The user's own files and git state are exactly as before; the second executor never saw the first's edits.
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert not (repo / "new.txt").exists()
    assert (pool.get("e2").workspace / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert _git_status(repo) == before


def test_the_overwrite_question_is_really_asked_of_an_executor_that_keeps_the_gate_on(tmp_path, monkeypatch):
    # The control for the test above: with the same gate and the same switch, a plain SafeExecutor does ask.
    monkeypatch.setenv("AGENT_WRITE_APPROVAL", "1")
    gate = RecordingGate(approve=False)
    sandbox = SafeExecutor(tmp_path / "plain", approvals=gate)
    (sandbox.workspace / "a.txt").write_text("hello\n", encoding="utf-8")

    from kernel.sandbox.executor import ExecutorError as SandboxError

    with pytest.raises(SandboxError, match="denied"):
        sandbox.execute(AgentCommand("edit", Action("write_file", {"path": "a.txt", "content": "one\n"})))
    assert gate.confirms == ["File overwrite — Human-in-the-Loop"]


def test_a_dangerous_command_goes_to_the_main_gate_when_it_answers_from_any_thread_and_is_refused_otherwise(
        agent_factory, repo):
    main, _ = _main(agent_factory, StyledClient())
    shared = RecordingGate()
    main.executor._approvals = shared
    pool = _pool(main, repo)
    pool.start()
    assert pool.get("e1").agent.executor.approvals is shared
    assert pool.wait(15)["done"]

    main2, _ = _main(agent_factory, StyledClient())
    main2.executor._approvals = SimpleNamespace(confirm_command=lambda *args, **kwargs: True)
    run = worktrees.create_run(repo, "run2", executor_ids(2))
    pool2 = ExecutorPool(main2, run, "task", styles_for(2))
    pool2.start()
    gate = pool2.get("e1").agent.executor.approvals
    assert isinstance(gate, DenyApprovalGate)
    assert gate.confirm_command(["rm", "-rf", "x"], "dangerous") is False
    assert pool2.wait(15)["done"]


def test_a_git_command_of_an_executor_is_refused_and_the_executor_carries_on(agent_factory, repo):
    client = StyledClient(scripts={"senior-dev": [
        _call("run_shell", {"command": ["git", "status"]}),
        _call("run_shell", {"command": ["git", "commit", "-m", "mine"]}),
        "git is not available here, so I did not commit.",
    ]})
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo)

    pool.start()
    assert pool.wait(15)["done"]

    executor = pool.get("e1")
    refused = [log for log in executor.agent._state.step_logs if log.get("status") == "error"]
    assert len(refused) == 2
    assert all("Hard security denial" in json.dumps(log) for log in refused)
    assert executor.state == "finished"
    assert "did not commit" in pool.summary("e1")["report"]
    # And nothing reached the repository: the user's HEAD and the executor's branch tip are where they were.
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, check=True, capture_output=True, text=True).stdout
    assert head.strip() == pool._run.head


# --- results, summaries, the check -------------------------------------------


def test_a_finished_executor_has_its_result_recorded_and_a_summary_for_the_judge(agent_factory, repo):
    long_report = "Rewrote a.txt. " + "x" * (REPORT_CHARS + 500)
    client = StyledClient(scripts={"senior-dev": [
        _call("write_file", {"path": "a.txt", "content": "hello, friend\nsecond\n"}),
        _call("write_file", {"path": "b.txt", "content": "b\n"}),
        long_report,
    ]})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)

    pool.start()
    assert pool.wait(15)["done"]

    summary = pool.summary("e1")
    assert set(summary) == {"executor_id", "style", "state", "steps", "diff", "files", "stat", "tests", "report",
                            "usage"}
    assert summary["state"] == "finished" and summary["style"] == "senior-dev" and summary["steps"] == 3  # two writes and the final answer
    assert summary["diff"] == {"files": 2, "added": 3, "removed": 1}
    assert sorted(summary["files"]) == ["a.txt", "b.txt"]
    assert "a.txt" in summary["stat"]
    assert summary["tests"] == {"state": "unknown", "summary": ""}
    assert summary["report"].startswith("Rewrote a.txt. ")
    assert summary["report"].endswith("\n[... 515 characters omitted ...]")
    assert len(summary["report"]) == REPORT_CHARS + len("\n[... 515 characters omitted ...]")
    assert summary["usage"]["calls"] >= 3 and summary["usage"]["input_tokens"] > 0
    assert pool.finished() == ["e1", "e2"]
    info = worktrees.load_run(repo, RUN_ID).executors["e1"]
    assert info.status == "done" and info.result
    assert "+hello, friend" in pool.full_diff("e1") and "+b" not in pool.full_diff("e1", "a.txt")
    last = [p for p in watching.statuses if p["executor_id"] == "e1"][-1]
    assert last["diff"] == {"files": 2, "added": 3, "removed": 1}
    assert pool.summary("e2")["diff"] == {"files": 0, "added": 0, "removed": 0}


def test_check_runs_the_command_in_the_executors_own_worktree_and_reports_passed_or_failed(
        agent_factory, repo, monkeypatch):
    seam = CheckSeam(monkeypatch)
    main, watching = _main(agent_factory, StyledClient())
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15)["done"]

    passed = pool.check("e1", [seam.MARKER, "--fast"])
    seam.result = ExecutionResult(ok=False, action_type="run_shell", data={
        "command": [seam.MARKER], "returncode": 1, "stdout": "\n".join(f"line {n}" for n in range(30)) + "\n",
        "stderr": "FAILED test_greeting\n", "timed_out": False})
    failed = pool.check("e2", [seam.MARKER])

    assert passed == {"state": "passed", "summary": "3 passed in 0.1s"}
    assert failed["state"] == "failed"
    lines = failed["summary"].splitlines()
    assert lines[-1] == "FAILED test_greeting" and lines[0] == "line 21" and len(lines) == 10
    # Each ran in its own worktree, with the sandbox's own timeout argument.
    assert [workspace for workspace, _ in seam.calls] == [pool.get("e1").workspace, pool.get("e2").workspace]
    assert seam.calls[0][1] == {"command": [seam.MARKER, "--fast"], "timeout": executors_module.CHECK_TIMEOUT_SECONDS}
    assert pool.summary("e2")["tests"] == failed
    last = [p for p in watching.statuses if p["executor_id"] == "e2"][-1]
    assert last["tests"] == failed
    running = [p["tests"]["state"] for p in watching.statuses if p["executor_id"] == "e1"]
    assert running[-2:] == ["running", "passed"]


def test_a_check_that_timed_out_failed_and_one_that_was_refused_is_unknown_not_failed(
        agent_factory, repo, monkeypatch):
    from kernel.sandbox.executor import ExecutorError as SandboxError

    seam = CheckSeam(monkeypatch)
    main, _ = _main(agent_factory, StyledClient())
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15)["done"]

    seam.result = ExecutionResult(ok=False, action_type="run_shell", data={
        "command": [seam.MARKER], "returncode": -9, "stdout": "collecting\n", "stderr": "", "timed_out": True})
    timed_out = pool.check("e1", [seam.MARKER])
    seam.result = ExecutionResult(ok=False, action_type="run_shell", data={
        "command": [seam.MARKER], "error": "needs the person's approval", "denied": True})
    denied = pool.check("e1", [seam.MARKER])
    seam.error = SandboxError("Hard security denial: nope")
    refused = pool.check("e1", [seam.MARKER])

    assert timed_out["state"] == "failed" and timed_out["summary"].endswith("the check timed out")
    assert denied["state"] == "unknown" and "did not run" in denied["summary"] and "approval" in denied["summary"]
    assert refused["state"] == "unknown" and "Hard security denial" in refused["summary"]


def test_set_check_runs_the_command_on_each_executor_as_it_ends_and_wait_returns_after_the_check(
        agent_factory, repo, monkeypatch):
    seam = CheckSeam(monkeypatch)
    seam.result = ExecutionResult(ok=True, action_type="run_shell", data={
        "command": [seam.MARKER], "returncode": 0, "stdout": "ok\n", "stderr": "", "timed_out": False})
    gate = threading.Event()
    client = StyledClient(gates={"minimalist": gate})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.set_check([seam.MARKER], 30)

    pool.start()
    assert pool.wait(15, "e1")["done"]
    # e1 ended and was checked; e2 is still held at the model and has not been.
    assert pool.summary("e1")["tests"]["state"] == "passed"
    assert pool.summary("e2")["tests"]["state"] == "unknown"
    gate.set()
    assert pool.wait(15)["done"]

    assert pool.summary("e2")["tests"] == {"state": "passed", "summary": "ok"}
    assert [workspace for workspace, _ in seam.calls].count(pool.get("e2").workspace) == 1
    assert seam.calls[0][1]["timeout"] == 30.0
    assert watching.states("e1") == ["waiting", "running", "finished"]


def test_a_failed_executor_is_checked_too_and_a_stopped_one_is_not(agent_factory, repo, monkeypatch):
    seam = CheckSeam(monkeypatch)
    client = StyledClient(failing=["senior-dev"])
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.set_check([seam.MARKER])

    pool.start()
    assert pool.wait(20)["done"]

    assert pool.get("e1").state == "failed" and pool.summary("e1")["tests"]["state"] == "passed"
    assert [workspace for workspace, _ in seam.calls].count(pool.get("e1").workspace) == 1


def test_check_is_refused_for_an_executor_still_working_and_without_a_command(agent_factory, repo):
    gate = threading.Event()
    main, _ = _main(agent_factory, StyledClient(gates={"senior-dev": gate}))
    pool = _pool(main, repo)
    pool.start()
    assert _until(lambda: pool.get("e1").state == "running")

    with pytest.raises(ExecutorError, match="still working"):
        pool.check("e1", ["true"])
    gate.set()
    assert pool.wait(15)["done"]
    with pytest.raises(ExecutorError, match="no check command"):
        pool.check("e1")
    with pytest.raises(ExecutorError, match="non-empty list of strings"):
        pool.check("e1", [])
    with pytest.raises(ExecutorError, match="non-empty list of strings"):
        pool.set_check(["ok", 3])


# --- control ----------------------------------------------------------------


def test_eliminate_stops_a_working_executor_records_its_result_and_says_why(agent_factory, repo):
    gate = threading.Event()
    client = StyledClient(scripts={"senior-dev": [_call("write_file", {"path": "late.txt", "content": "x\n"})]},
                          gates={"senior-dev": gate})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.start()
    assert _until(lambda: client.asked("senior-dev"))

    threading.Timer(0.3, gate.set).start()
    summary = pool.eliminate("e1", "too slow")

    assert summary["state"] == "eliminated" and summary["reason"] == "too slow"
    assert pool.get("e1").thread is not None and not pool.get("e1").thread.is_alive()
    assert watching.eliminated == [{"run_id": RUN_ID, "executor_id": "e1", "reason": "too slow"}]
    assert watching.states("e1")[-1] == "eliminated" and "stopped" not in watching.states("e1")
    info = worktrees.load_run(repo, RUN_ID).executors["e1"]
    assert info.status == "done" and info.result
    assert pool.finished() != ["e1"]
    assert pool.wait(15)["done"]
    # Eliminating again says nothing new.
    assert pool.eliminate("e1", "again")["reason"] == "too slow"
    assert len(watching.eliminated) == 1


def test_a_finished_executor_can_be_eliminated_from_the_comparison_and_keeps_its_result(agent_factory, repo):
    client = StyledClient(scripts={"minimalist": [_call("write_file", {"path": "m.txt", "content": "m\n"}), "Done."]})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15)["done"]

    summary = pool.eliminate("e2", "tests fail")

    assert summary["state"] == "eliminated" and summary["files"] == ["m.txt"]
    assert watching.eliminated[0]["reason"] == "tests fail"
    assert pool.finished() == ["e1"]


def test_cancel_all_stops_everyone_records_every_result_and_marks_the_run_cancelled(agent_factory, repo):
    gate = threading.Event()
    client = StyledClient(gates={"senior-dev": gate})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.start()
    assert _until(lambda: client.asked("senior-dev"))

    threading.Timer(0.3, gate.set).start()
    stopped = pool.cancel_all("the person stopped the task")

    assert stopped == ["e1", "e2"]
    for executor_id in ("e1", "e2"):
        assert pool.get(executor_id).state == "stopped"
        assert pool.summary(executor_id)["reason"] == "the person stopped the task"
        assert watching.states(executor_id)[-1] == "stopped"
    run = worktrees.load_run(repo, RUN_ID)
    assert run.state == "cancelled"
    assert all(info.status == "done" and info.result for info in run.executors.values())
    # The one that was still waiting never asked the model at all.
    assert client.asked("minimalist") == []
    assert pool.cancel_all("again") == []


def test_restart_gives_an_executor_that_ended_new_work_in_the_same_worktree(agent_factory, repo):
    client = StyledClient(scripts={"senior-dev": [
        _call("write_file", {"path": "one.txt", "content": "1\n"}), "First part done.",
        _call("write_file", {"path": "two.txt", "content": "2\n"}), "Second part done.",
    ]})
    main, watching = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15, "e1")["done"]
    first = pool.summary("e1")
    assert first["files"] == ["one.txt"]

    restarted = pool.restart("e1", "Now also add two.txt.")
    assert restarted["state"] == "running"
    assert pool.wait(15, "e1")["done"]

    second = pool.summary("e1")
    assert sorted(second["files"]) == ["one.txt", "two.txt"]
    assert second["state"] == "finished" and second["steps"] > first["steps"]
    assert second["report"] == "Second part done."
    assert (pool.get("e1").workspace / "one.txt").is_file() and (pool.get("e1").workspace / "two.txt").is_file()
    user = " ".join(m["content"] for m in client.asked("senior-dev")[2] if m["role"] == "user")
    assert "Now also add two.txt." in user and "The reviewer asks you to continue" in user
    assert watching.states("e1") == ["waiting", "running", "finished", "running", "finished"]


def test_restart_is_refused_for_an_executor_that_is_working(agent_factory, repo):
    gate = threading.Event()
    main, _ = _main(agent_factory, StyledClient(gates={"senior-dev": gate}))
    pool = _pool(main, repo)
    pool.start()
    assert _until(lambda: pool.get("e1").state == "running")

    with pytest.raises(ExecutorError, match="only an executor that ended"):
        pool.restart("e1", "again")
    gate.set()
    assert pool.wait(15)["done"]


def test_a_message_reaches_the_working_executor_before_its_next_request_and_not_an_ended_one(agent_factory, repo):
    gate = threading.Event()
    client = StyledClient(scripts={"senior-dev": [_call("list_dir", {"path": "."}), "Looked."]},
                          gates={"senior-dev": gate})
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.start()
    assert _until(lambda: client.asked("senior-dev"))

    sent = pool.message("e1", "use tabs")
    assert sent["state"] == "sent"
    gate.set()
    assert pool.wait(15)["done"]

    second_request = " ".join(m["content"] for m in client.asked("senior-dev")[1])
    assert "Message from the reviewer: use tabs" in second_request
    assert pool.get("e1").messages[0]["state"] == "applied"
    with pytest.raises(ExecutorError, match="use restart"):
        pool.message("e1", "too late")


def test_wait_sends_no_request_of_the_main_agent_and_returns_at_the_timeout(agent_factory, repo):
    gate = threading.Event()
    main, _ = _main(agent_factory, StyledClient(gates={"senior-dev": gate}))
    main_calls: list[str] = []
    main._call_action_model = lambda prompt: main_calls.append(prompt) or ""
    pool = _pool(main, repo)
    pool.start()

    waited = pool.wait(0.3)

    assert waited["done"] is False and 0.3 <= waited["waited_seconds"] < 3
    assert {row["state"] for row in waited["executors"]} <= {"waiting", "running"}
    gate.set()
    assert pool.wait(15)["done"] and main_calls == []


def test_inspect_lists_the_executors_own_actions(agent_factory, repo):
    client = StyledClient(scripts={"senior-dev": [_call("read_file", {"path": "a.txt"}), "It says hello."]})
    main, _ = _main(agent_factory, client)
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15)["done"]

    page = pool.inspect("e1", 0, 20)

    assert [item["action"] for item in page["items"]][0] == "read_file"
    assert page["items"][0]["args"] == {"path": "a.txt"} and page["untrusted"] is True
    assert pool.inspect("e1", 0, 1)["has_more"] is True


def test_executor_ids_are_e1_to_en():
    assert executor_ids(4) == ("e1", "e2", "e3", "e4")


# --- the real sandbox in a worktree under .kvist ------------------------------

# The sandbox allows a different read-only set on each platform, and a command
# outside its allowlist never runs: cat, grep and pwd do not exist on Windows,
# where the allowlist offers type, findstr and cd instead.
READ = ("type",) if os.name == "nt" else ("cat",)
MISSING = ("findstr", "hello") if os.name == "nt" else ("grep", "hello")
WHERE = ("cd",) if os.name == "nt" else ("pwd",)
PYTHON = "python" if os.name == "nt" else "python3"


def _shell(sandbox, command, **args):
    return sandbox.execute(AgentCommand("verify", Action("run_shell", {"command": command, **args})))


@pytest.fixture
def kvist_folder(tmp_path):
    """A folder like an executor's worktree: its own path contains .kvist, and it holds a.txt and its own .kvist."""
    folder = tmp_path / "outer" / ".kvist" / "bestof" / "run" / "e1"
    (folder / ".kvist").mkdir(parents=True)
    (folder / "a.txt").write_text("hello\n", encoding="utf-8")
    (folder / ".kvist" / "secret.txt").write_text("internal\n", encoding="utf-8")
    (tmp_path / "outer" / ".kvist" / "sessions.txt").write_text("the user's own\n", encoding="utf-8")
    return folder


def test_an_ordinary_workspace_inside_a_kvist_folder_still_refuses_every_shell_command(kvist_folder):
    from kernel.sandbox.executor import ExecutorError as SandboxError

    sandbox = SafeExecutor(kvist_folder, approvals=DenyApprovalGate())

    with pytest.raises(SandboxError, match="internal .kvist state"):
        _shell(sandbox, ["cat", "a.txt"])


def test_with_the_flag_a_command_in_a_workspace_under_kvist_runs(kvist_folder):
    sandbox = SafeExecutor(kvist_folder, approvals=DenyApprovalGate(), workspace_inside_kvist=True)

    result = _shell(sandbox, [*READ, "a.txt"])

    assert result.ok and result.data["returncode"] == 0 and result.data["stdout"].strip() == "hello"
    # A line that names the workspace's own absolute path (a test run's traceback) is not dropped as "internal".
    where = _shell(sandbox, [*WHERE])
    assert Path(where.data["stdout"].strip()) == kvist_folder


@pytest.mark.parametrize("attempt", [
    {"command": ["cat", ".kvist/secret.txt"]},
    {"command": ["ls"], "cwd": ".kvist"},
    {"command": ["cat", "$ABS/.kvist/secret.txt"]},
    {"command": ["ls"], "cwd": "$ABS/.kvist"},
    {"command": ["cat", "link/secret.txt"]},
    {"command": ["ls"], "cwd": "link"},
    {"command": ["cat", "$OUTER/.kvist/sessions.txt"]},
])
def test_with_the_flag_the_workspaces_own_kvist_and_the_outer_one_stay_forbidden(kvist_folder, attempt):
    from kernel.sandbox.executor import ExecutorError as SandboxError

    (kvist_folder / "link").symlink_to(kvist_folder / ".kvist")
    outer = kvist_folder.parents[3]
    sandbox = SafeExecutor(kvist_folder, approvals=DenyApprovalGate(), workspace_inside_kvist=True)
    fill = {"$ABS": kvist_folder.as_posix(), "$OUTER": outer.as_posix()}
    prepared = {key: ([part.replace("$ABS", fill["$ABS"]).replace("$OUTER", fill["$OUTER"]) for part in value]
                      if isinstance(value, list) else
                      value.replace("$ABS", fill["$ABS"]).replace("$OUTER", fill["$OUTER"]))
                for key, value in attempt.items()}

    with pytest.raises(SandboxError, match=r"internal (agent directory|\.kvist)"):
        _shell(sandbox, prepared.pop("command"), **prepared)


def test_the_executors_sandbox_is_built_with_the_flag_and_so_is_every_terminal_it_spawns(agent_factory, repo):
    main, _ = _main(agent_factory, StyledClient())
    pool = _pool(main, repo)
    pool.start()
    sandbox = pool.get("e1").agent.executor

    assert sandbox.workspace_inside_kvist is True and sandbox._terminal.workspace_inside_kvist is True
    with sandbox.parallel_shell_slot(None, threading.Event()):
        assert sandbox._shell_terminal().workspace_inside_kvist is True
    assert pool.wait(15)["done"]


def test_check_goes_through_the_real_sandbox_and_reports_passed_and_failed(agent_factory, repo):
    main, watching = _main(agent_factory, StyledClient())
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15)["done"]

    passed = pool.check("e1", [*READ, "a.txt"])
    failed = pool.check("e1", [*MISSING, "missing.txt"])

    assert passed == {"state": "passed", "summary": "hello"}
    assert failed["state"] == "failed" and "missing.txt" in failed["summary"]
    assert pool.summary("e1")["tests"] == failed
    assert [p["tests"]["state"] for p in watching.statuses if p["executor_id"] == "e1"][-4:] == [
        "running", "passed", "running", "failed"]


def test_set_check_runs_the_real_command_when_each_executor_ends(agent_factory, repo):
    main, _ = _main(agent_factory, StyledClient())
    pool = _pool(main, repo)
    pool.set_check([*READ, "a.txt"])

    pool.start()
    assert pool.wait(15)["done"]

    assert [pool.summary(e)["tests"] for e in ("e1", "e2")] == [{"state": "passed", "summary": "hello"}] * 2


def test_a_command_that_needs_approval_goes_to_the_main_gate_and_without_one_the_check_is_unknown(
        agent_factory, repo):
    gate = RecordingGate(approve=True)
    main, _ = _main(agent_factory, StyledClient())
    main.executor._approvals = gate
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15)["done"]

    ran = pool.check("e1", [PYTHON, "-c", "import sys; sys.exit(3)"])

    assert ran["state"] == "failed"
    assert [command[0] for command in gate.commands] == [PYTHON]

    other, _ = _main(agent_factory, StyledClient())
    other.executor._approvals = SimpleNamespace()  # cannot be answered from another thread
    second = ExecutorPool(other, worktrees.create_run(repo, "run2", executor_ids(2)), "task", styles_for(2))
    second.start()
    assert second.wait(15)["done"]
    refused = second.check("e1", [PYTHON, "-c", "import sys; sys.exit(0)"])
    assert refused["state"] == "unknown" and "approval" in refused["summary"]


def test_git_is_closed_to_an_executor_because_its_metadata_lies_outside_the_workspace_not_because_of_kvist(
        agent_factory, repo):
    main, _ = _main(agent_factory, StyledClient())
    pool = _pool(main, repo)
    pool.start()
    assert pool.wait(15)["done"]

    for command in (["git", "status"], ["git", "log", "-1"], ["git", "commit", "-m", "mine"]):
        result = pool.check("e1", command)
        assert result["state"] == "unknown"
        assert "Git metadata resolves outside the workspace" in result["summary"]
        assert ".kvist" not in result["summary"]


def test_the_executors_events_have_the_shape_the_contract_gives_through_a_real_event_stream(agent_factory, repo):
    from kernel.protocol.event_stream_ui import EventStreamUI
    from kernel.protocol.frontend_protocol import FrontendChannel

    client = StyledClient(scripts={"senior-dev": [
        _call("write_file", {"path": "b.txt", "content": "b\n"}), "Wrote b.txt."]})
    main, _ = _main(agent_factory, client)
    channel = FrontendChannel()
    object.__setattr__(main._ui, "_ui", EventStreamUI(channel))
    pool = _pool(main, repo)
    pool.set_check([*READ, "a.txt"])

    pool.start()
    assert pool.wait(15)["done"]
    events = [event.to_dict() for event in channel.drain()]

    statuses = [event for event in events if event["type"] == "bestof_status" and event["executor_id"] == "e1"]
    for event in statuses:
        assert set(event) == {"type", "run_id", "executor_id", "style", "branch", "state", "step", "tests", "diff",
                              "usage"}
        assert event["run_id"] == RUN_ID and event["style"] == "senior-dev"
        assert event["branch"] == f"kvist/bestof/{RUN_ID}/e1"
        assert set(event["tests"]) == {"state", "summary"} and set(event["diff"]) == {"files", "added", "removed"}
        assert {"input_tokens", "output_tokens", "cost_approx"} <= set(event["usage"])
    assert [event["state"] for event in statuses][0] == "waiting" and statuses[-1]["state"] == "finished"
    assert statuses[-1]["tests"] == {"state": "passed", "summary": "hello"}
    assert statuses[-1]["diff"] == {"files": 1, "added": 1, "removed": 0}
    assert statuses[-1]["usage"]["input_tokens"] > 0
    activity = [event for event in events if event["type"] == "executor_activity"
                and event["action_type"] == "write_file"]
    assert [event["phase"] for event in activity] == ["step", "result"]
    assert all(set(event) >= {"run_id", "executor_id", "phase", "call_id", "action_type", "label", "summary",
                              "output"} for event in activity)
    assert {(event["run_id"], event["executor_id"]) for event in activity} == {(RUN_ID, "e1")}
    assert activity[0]["call_id"] == activity[1]["call_id"] and activity[1]["ok"] is True and "ok" not in activity[0]
    # Nothing of the executors' own thoughts or steps reaches the main stream.
    assert {event["type"] for event in events} <= {"bestof_status", "executor_activity"}


# --- Kvist's own notebooks stay out of an executor's work ------------------


@pytest.mark.parametrize("name", ["KVIST.md", "KVIST-CHAT.md"])
@pytest.mark.parametrize("action,args", [
    ("write_file", {"content": "rewritten\n"}),
    ("append_file", {"content": "more\n"}),
    ("replace_in_file", {"old_text": "mine", "new_text": "theirs"}),
])
def test_an_executor_cannot_change_kvists_notebooks_and_is_told_why(tmp_path, name, action, args):
    from kernel.sandbox.executor import ExecutorError as SandboxError

    (tmp_path / name).write_text("mine\n", encoding="utf-8")
    sandbox = SafeExecutor(tmp_path, approvals=DenyApprovalGate(), protect_instruction_files=True)

    with pytest.raises(SandboxError, match=r"own notebook.*not part of this task"):
        sandbox.execute(AgentCommand("edit", Action(action, {"path": name, **args})))

    assert (tmp_path / name).read_text(encoding="utf-8") == "mine\n"
    # Reading stays allowed, and an ordinary file is written as before.
    assert sandbox.execute(AgentCommand("read", Action("read_file", {"path": name}))).ok
    assert sandbox.execute(AgentCommand("edit", Action("write_file", {"path": "x.txt", "content": "1\n"}))).ok


def test_the_main_sandbox_still_edits_its_own_notebook(tmp_path):
    (tmp_path / "KVIST.md").write_text("mine\n", encoding="utf-8")
    sandbox = SafeExecutor(tmp_path, approvals=DenyApprovalGate())

    assert sandbox.execute(AgentCommand("edit", Action("write_file", {"path": "KVIST.md", "content": "new\n"}))).ok


def test_an_executors_sandbox_protects_the_notebooks_and_its_attempt_to_write_one_is_refused(agent_factory, repo):
    (repo / "KVIST.md").write_text("the user's notes\n", encoding="utf-8")
    scripts = {"senior-dev": [
        _call("write_file", {"path": "KVIST.md", "content": "overwritten\n"}),
        "Tried to update the notes.",
    ]}
    main, _ = _main(agent_factory, StyledClient(scripts=scripts))
    pool = _pool(main, repo)

    pool.start()
    assert pool.wait(15)["done"]

    assert pool.get("e1").agent.executor.protect_instruction_files is True
    assert (pool.get("e1").workspace / "KVIST.md").read_text(encoding="utf-8") == "the user's notes\n"
    assert pool.summary("e1")["diff"] == {"files": 0, "added": 0, "removed": 0}
