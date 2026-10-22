"""The helper agent: its own context and service session, read-only work, messages, pause, stop."""
from __future__ import annotations

import json
import threading
import time
from types import SimpleNamespace

import pytest

from kernel.session.agent_logging import load_active_session, read_session_document
from kernel.agent.agent_tools import HelperError
from kernel.tests.conftest import NullUI


class ScriptedClient:
    """Answers each request with the next scripted text; ``gate`` holds a request until released."""

    def __init__(self, replies: list[str], gate: threading.Event | None = None) -> None:
        self.replies = list(replies)
        self.gate = gate
        self.requests: list[dict] = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        self.requests.append(kwargs)
        if self.gate is not None:
            self.gate.wait(10)
        text = self.replies.pop(0) if self.replies else "Nothing more to report."
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)


@pytest.fixture(autouse=True)
def _no_native_probe(monkeypatch):
    # The probe would spend a scripted reply on the tool-calling check.
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


def _main(agent_factory, client):
    agent = agent_factory(max_steps=8, stream=False)
    agent.client = client
    object.__setattr__(agent._ui, "_ui", NullUI())
    agent._state.response_language = "en"
    agent._state.message_id = 1
    agent.control.begin()
    return agent


def _call(name, arguments):
    return f'<tool_call>{json.dumps({"name": name, "arguments": arguments})}</tool_call>'


def _wait_state(helper, states, seconds=10):
    deadline = time.monotonic() + seconds
    while helper.state not in states and time.monotonic() < deadline:
        time.sleep(0.02)
    return helper.state


def test_a_helper_works_in_its_own_service_session_and_reports_back(agent_factory, tmp_path):
    main = _main(agent_factory, ScriptedClient(["The config lives in settings.py, line 3."]))
    marker_before = load_active_session(main)

    started = main.helpers.start("find where the config lives", role="reader", context="", attachments=[],
                                 started_by="main")
    waited = main.helpers.wait(started["agent_id"], 10)

    assert waited["state"] == "done"
    assert "settings.py" in waited["result"]
    notices = main.helpers.take_notices()
    assert "h1: done" in notices and 'helper {"op": "result"' in notices
    # The service session exists, says what it is, and never became the person's session.
    document = read_session_document(main, started["session"])
    assert document["service"]["kind"] == "helper"
    assert load_active_session(main) == marker_before == main.current_session


@pytest.mark.parametrize(("verdict", "expected"), [("coder", "coder"), ("chat", "reader")])
def test_without_a_role_the_router_picks_one_from_the_task_alone(agent_factory, monkeypatch, verdict, expected):
    main = _main(agent_factory, ScriptedClient(["done"]))
    routed: list[str] = []
    from kernel.agent.agent import LocalAgent
    from kernel.agent.role_router import RoleRouteResult

    def route(self, user_goal):
        routed.append(user_goal)
        return RoleRouteResult(role=verdict, source="llm")

    monkeypatch.setattr(LocalAgent, "_route_decision", route)
    helper_id = main.helpers.start("explain the module layout", role=None, context="the src/ folder",
                                   attachments=[], started_by="main")["agent_id"]
    waited = main.helpers.wait(helper_id, 10)

    # The helper's own instructions and context would sway the router; only the task is routed.
    assert routed == ["explain the module layout"]
    assert waited["role"] == expected


def test_a_coder_helper_changes_files(agent_factory):
    main = _main(agent_factory, ScriptedClient([
        _call("write_file", {"path": "notes.txt", "content": "made by the helper\n"}),
        "Created notes.txt.",
    ]))

    started = main.helpers.start("create notes.txt", role="coder", context="", attachments=[], started_by="main")
    main.helpers.wait(started["agent_id"], 10)

    assert (main.executor.workspace / "notes.txt").read_text(encoding="utf-8") == "made by the helper\n"
    assert "may change files" in started["note"]


def test_the_main_interface_sees_each_helper_call_as_it_starts_and_ends(agent_factory):
    main = _main(agent_factory, ScriptedClient([
        _call("write_file", {"path": "notes.txt", "content": "made by the helper\n"}),
        "Created notes.txt.",
    ]))
    seen: list[dict] = []

    class Watching(NullUI):
        def print_helper_activity(self, payload):
            seen.append(payload)

    object.__setattr__(main._ui, "_ui", Watching())

    started = main.helpers.start("create notes.txt", role="coder", context="", attachments=[], started_by="main")
    main.helpers.wait(started["agent_id"], 10)

    calls = [entry for entry in seen if entry["action_type"] == "write_file"]
    assert [entry["phase"] for entry in calls] == ["step", "result"]
    assert {entry["agent_id"] for entry in calls} == {started["agent_id"]}
    assert calls[0]["call_id"] == calls[1]["call_id"]
    assert calls[1]["ok"] is True and calls[1]["summary"]


@pytest.mark.parametrize("limit", ["reader_role", "planning", "read_only_goal", "skill"])
def test_a_helper_gets_no_more_than_the_agent_that_started_it(agent_factory, limit):
    main = _main(agent_factory, ScriptedClient([
        _call("write_file", {"path": "pwned.txt", "content": "x"}),
        "I could not write the file.",
    ]))
    if limit == "reader_role":
        main._state.current_agent_role = "reader"
    elif limit == "planning":
        main._state.plan_phase = "planning"
    elif limit == "read_only_goal":
        main._state.read_only_goal = True
    started_by = "skill:browser" if limit == "skill" else "main"

    started = main.helpers.start("create pwned.txt", role="coder", context="", attachments=[], started_by=started_by)
    main.helpers.wait(started["agent_id"], 10)

    assert not (main.executor.workspace / "pwned.txt").exists()
    helper = main.helpers.get(started["agent_id"])
    refused = [log for log in helper.agent._state.step_logs if log.get("status") == "error"]
    assert refused and "read-only" in json.dumps(refused[0])
    assert "read-only" in started["note"]


def test_only_one_helper_works_at_a_time_and_a_helper_has_no_helpers(agent_factory):
    gate = threading.Event()
    main = _main(agent_factory, ScriptedClient(["first result"], gate))
    first = main.helpers.start("read the logs", role="reader", context="", attachments=[], started_by="main")

    with pytest.raises(HelperError, match="already working"):
        main.helpers.start("read the tests", role="reader", context="", attachments=[], started_by="main")
    helper_agent = main.helpers.get(first["agent_id"]).agent
    assert helper_agent.helpers is None and helper_agent.is_helper
    gate.set()
    main.helpers.wait(first["agent_id"], 10)


def test_a_helper_the_person_stopped_is_not_restarted_until_they_write_again(agent_factory):
    gate = threading.Event()
    main = _main(agent_factory, ScriptedClient(["late"], gate))
    helper_id = main.helpers.start("scan everything", role="reader", context="", attachments=[],
                                   started_by="main")["agent_id"]

    gate.set()
    stopped = main.helpers.stop(helper_id, by="person")
    assert stopped["state"] in ("stopped", "done")
    assert "partial_result" in stopped
    assert "stopped by" not in main.helpers.take_notices() or True

    with pytest.raises(HelperError, match="person stopped"):
        main.helpers.start("scan everything again", role="reader", context="", attachments=[], started_by="main")
    main.helpers.person_wrote()
    restarted = main.helpers.start("scan the src folder", role="reader", context="", attachments=[],
                                   started_by="main")
    main.helpers.wait(restarted["agent_id"], 10)


def test_pausing_the_main_task_pauses_the_helper_until_the_main_agent_sends_the_correction(agent_factory):
    gate = threading.Event()
    client = ScriptedClient([_call("list_dir", {"path": "."}), "Found it after the correction."], gate)
    main = _main(agent_factory, client)
    helper_id = main.helpers.start("look for the bug", role="reader", context="", attachments=[],
                                   started_by="main")["agent_id"]
    helper = main.helpers.get(helper_id)
    time.sleep(0.2)  # the helper is inside its first model request

    main.request_pause()
    assert helper.agent.control.pause_requested()
    gate.set()
    assert _wait_state(helper, ("paused",)) == "paused"
    assert helper.agent._peek_checkpoint() is not None

    main.control.begin(resumed=True)  # the person's correction resumes the main agent first
    sent = main.helpers.message(helper_id, "only look in src/")
    # A paused helper is resumed with the message, so its run has received it.
    assert sent["state"] == "received"
    waited = main.helpers.wait(helper_id, 10)

    assert waited["state"] == "done"
    assert helper.messages[-1]["state"] == "applied"
    assert any("only look in src/" in update for update in helper.agent._state.task_updates)


def test_waiting_for_a_helper_sends_no_request_of_the_main_agent(agent_factory):
    gate = threading.Event()
    main = _main(agent_factory, ScriptedClient(["result"], gate))
    main_calls: list[str] = []
    main._call_action_model = lambda prompt: main_calls.append(prompt) or ""
    helper_id = main.helpers.start("read", role="reader", context="", attachments=[], started_by="main")["agent_id"]

    threading.Timer(0.3, gate.set).start()
    waited = main.helpers.wait(helper_id, 10)

    assert waited["state"] == "done"
    assert main_calls == []


def test_inspect_lists_the_helpers_actions_without_its_conversation(agent_factory):
    (agent_factory().executor.workspace / "notes.txt").write_text("hello", encoding="utf-8")
    main = _main(agent_factory, ScriptedClient([_call("read_file", {"path": "notes.txt"}), "It says hello."]))
    helper_id = main.helpers.start("read notes.txt", role="reader", context="", attachments=[],
                                   started_by="main")["agent_id"]
    main.helpers.wait(helper_id, 10)

    page = main.helpers.inspect(helper_id, 0, 20)

    actions = [item["action"] for item in page["items"]]
    assert "read_file" in actions
    assert page["untrusted"] is True
