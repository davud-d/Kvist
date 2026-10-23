"""Pause and "stop all": what stops, what survives, and how the task continues."""
from __future__ import annotations

import json
import time

from kernel.tests.conftest import AllowAllApprovals, NullUI


class RecordingUI(NullUI):
    def __init__(self) -> None:
        self.finals: list[tuple[str, str]] = []
        self.states: list[str] = []

    def print_final(self, message, user_messages=None, outcome="done"):
        self.finals.append((message, outcome))

    def print_task_state(self, state, reason):
        self.states.append(state)


def _agent(agent_factory):
    agent = agent_factory(max_steps=6)
    ui = RecordingUI()
    object.__setattr__(agent._ui, "_ui", ui)
    agent._state.selected_action_role = "fast"
    agent._state.selected_action_model = "mock-model"
    agent._state.response_language = "en"
    agent._state.native_tools = False
    agent.executor._approvals = AllowAllApprovals()
    agent.executor._terminal._approvals = AllowAllApprovals()
    return agent, ui


# Without Bubblewrap (or sandbox-exec) a command without network is refused by
# design; these tests are about pause, so their jobs ask for network to start anywhere.
_RUNS_ANYWHERE = {"network": True, "justification": "test"}


def _calls(*calls):
    return "".join(f'<tool_call>{json.dumps({"name": n, "arguments": a})}</tool_call>' for n, a in calls)


def _start(agent, goal):
    agent._reset_cancel()
    agent._run_id = "run-1"
    agent._begin_run(goal, agent._run_id)
    return agent._run_loop(goal, start_step=1)


def test_a_pause_stops_the_rest_of_the_response_and_reports_paused_only_at_the_safe_point(agent_factory):
    agent, ui = _agent(agent_factory)
    original = agent.executor.execute

    def execute(command):
        if command.action.args.get("path") == "a.txt":
            agent.request_pause()
        return original(command)

    agent.executor.execute = execute
    agent._call_action_model = lambda prompt: _calls(
        ("write_file", {"path": "a.txt", "content": "a"}),
        ("write_file", {"path": "b.txt", "content": "b"}),
    )

    _start(agent, "create a.txt and b.txt")

    workspace = agent.executor.workspace
    assert (workspace / "a.txt").exists()  # the step already running finished
    assert not (workspace / "b.txt").exists()  # the rest of the response did not run
    assert ui.states.index("pause_requested") < ui.states.index("paused")
    assert ui.finals[-1][1] == "paused"
    assert agent.control.state == "paused"
    assert agent._peek_checkpoint() is not None  # the task stays resumable
    assert agent._state.paused_by_user is True


def test_the_correction_after_a_pause_reaches_the_model_marked_as_such(agent_factory):
    agent, ui = _agent(agent_factory)
    prompts: list[str] = []

    def first(prompt):
        agent.request_pause()
        return _calls(("list_dir", {"path": "."}))

    agent._call_action_model = first
    _start(agent, "build the page in red")

    def second(prompt):
        prompts.append(prompt)
        return "Switched to blue."

    agent._call_action_model = second
    agent.run("use blue instead")

    assert any("After pausing the work, the person wrote: use blue instead" in prompt for prompt in prompts)
    assert agent._state.paused_by_user is False


def test_background_jobs_survive_a_pause_and_stop_all_ends_them(agent_factory, trusted_python_name):
    agent, ui = _agent(agent_factory)
    sleeper = [trusted_python_name, "-c", "import time; time.sleep(60)"]

    def first(prompt):
        if not agent.executor.jobs.ids():
            return _calls(("run_shell", {"command": sleeper, "purpose": "wait", "background": True, **_RUNS_ANYWHERE}))
        agent.request_pause()
        return _calls(("list_dir", {"path": "."}))

    agent._call_action_model = first
    _start(agent, "start a long job")

    job = agent.executor.jobs.get(agent.executor.jobs.ids()[0])
    assert job.status == "running"
    assert agent.control.state == "paused"

    agent.request_cancel()  # "stop all" while paused: no turn runs, the leftovers stop now

    deadline = time.monotonic() + 10
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    assert job.status == "stopped"
    assert agent.control.state == "cancelled"


def test_a_job_ending_during_a_pause_queues_its_note_without_waking_the_model(agent_factory, trusted_python_name):
    agent, ui = _agent(agent_factory)
    calls = {"n": 0}
    quick = [trusted_python_name, "-c", "import time; time.sleep(0.5); print('built')"]

    def model(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return _calls(("run_shell", {"command": quick, "purpose": "build", "background": True, **_RUNS_ANYWHERE}))
        agent.request_pause()
        return _calls(("list_dir", {"path": "."}))

    agent._call_action_model = model
    _start(agent, "build in the background")
    made = calls["n"]
    job = agent.executor.jobs.get(agent.executor.jobs.ids()[0])

    deadline = time.monotonic() + 10
    while job.status == "running" and time.monotonic() < deadline:
        time.sleep(0.05)
    time.sleep(0.3)

    assert job.status == "exited"
    assert calls["n"] == made  # nothing resumed the agent on its own
    text, job_ids = agent.executor.jobs.take_note()
    assert "built" in text and job_ids == [job.job_id]


def test_stop_all_while_running_ends_the_turn_as_cancelled_not_paused(agent_factory):
    agent, ui = _agent(agent_factory)

    def model(prompt):
        agent.request_cancel()
        return _calls(("list_dir", {"path": "."}))

    agent._call_action_model = model
    _start(agent, "look around")

    assert agent.control.state == "cancelled"
    assert ui.finals[-1][1] != "paused"
    assert "stopped by you" in ui.finals[-1][0]


def test_pause_is_refused_when_nothing_runs(agent_factory):
    agent, _ui = _agent(agent_factory)

    assert agent.request_pause() is False
    assert agent.control.state == "idle"
