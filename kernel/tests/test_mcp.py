"""MCP servers through the shared tool registry: approval of the tool list, guards, cancellation, cleanup."""
from __future__ import annotations

import os
import sys
import threading
from pathlib import Path

import pytest

from kernel.plugins.mcp import save_server
from kernel.plugins.resources import Owner
from kernel.tests.conftest import NullUI

SERVER = Path(__file__).parent / "fixtures" / "mcp_echo_server.py"


class Approver(NullUI):
    def __init__(self) -> None:
        self.dialogs: list[tuple[str, str]] = []

    def confirm(self, title, body=""):
        self.dialogs.append((title, body))
        return True


@pytest.fixture
def mcp_agent(agent_factory, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(config))
    save_server("echo", {"command": [sys.executable, "-I", str(SERVER)], "sandbox": "none",
                         "env": {"API_TOKEN": "secret-token-value"}, "description": "test echo server"})
    agent = agent_factory(interactive=True)
    from kernel.plugins.grants import GrantStore

    agent.skills.grants = GrantStore(config / "plugins" / "grants.json")
    agent._state.response_language = "en"
    agent._run_id = "mcp-run"
    ui = Approver()
    object.__setattr__(agent._ui, "_ui", ui)
    yield agent, ui
    agent.skills.finish_task("test_end")


def test_attaching_shows_the_tools_for_approval_and_names_them_apart_from_everything_else(mcp_agent):
    agent, ui = mcp_agent

    attached = agent.skills.attach("mcp:echo")

    names = [tool["name"] for tool in attached["tools"]]
    assert names == ["mcp__echo__echo", "mcp__echo__slow_op", "mcp__echo__grow"]
    assert "echo" in ui.dialogs[0][1] and "secret-token-value" not in ui.dialogs[0][1]
    assert "mcp:echo" in agent.skills.prompt_section("en")


def test_a_call_runs_through_the_parser_and_its_result_is_untrusted(mcp_agent):
    from kernel.agent.agent_tools import run_agent_tool
    from kernel.protocol.parser import build_command
    from kernel.protocol.protocol import ToolCallRequest

    agent, ui = mcp_agent
    agent.skills.attach("mcp:echo")
    command = build_command(ToolCallRequest("c1", "mcp__echo__echo", {"text": "hi"}), text="", batch_id="b",
                            plan_step="inspect", language="en", resolve_skill_tool=agent.skills.resolve)

    feedback = run_agent_tool(agent, command)

    assert feedback["ok"] and feedback["data"]["text"] == "echo: hi" and feedback["data"]["untrusted"]


def test_the_servers_own_read_only_hint_is_not_trusted(mcp_agent):
    from kernel.agent.agent_runtime import _is_read_only_violation
    from kernel.protocol.protocol import Action, AgentCommand

    agent, ui = mcp_agent
    agent.skills.attach("mcp:echo")
    agent._state.current_agent_role = "reader"
    agent._state.read_only_goal = True
    command = AgentCommand("inspect", Action("skill_call", {"skill": "mcp:echo", "tool": "echo", "arguments": {}}))

    assert _is_read_only_violation(agent, command) is True


def test_a_changed_tool_list_needs_a_new_approval(mcp_agent):
    agent, ui = mcp_agent
    agent.skills.attach("mcp:echo")
    assert agent.skills.call("mcp:echo", "grow", {}, "c1")["ok"]

    refused = agent.skills.call("mcp:echo", "echo", {"text": "again"}, "c2")

    assert not refused["ok"] and "changed its tools" in refused["error"]
    assert "echo" not in agent.skills.mcp_attached


def test_cancelling_a_call_says_the_remote_effect_is_unknown(mcp_agent):
    agent, ui = mcp_agent
    agent.skills.attach("mcp:echo")
    agent.control.begin()
    threading.Timer(0.3, agent.control.request_cancel).start()

    feedback = agent.skills.call("mcp:echo", "slow-op", {}, "c1")

    assert not feedback["ok"] and "unknown" in feedback["error"]


def test_the_server_process_belongs_to_the_task_and_stops_with_it(mcp_agent):
    agent, ui = mcp_agent
    agent.skills.attach("mcp:echo")
    pid = agent.skills.mcp_attached["echo"].client.process.pid

    report = agent.skills.finish_task("task_finished")

    assert report.ok
    assert not os.path.exists(f"/proc/{pid}")
    assert agent.resources.owned_by(Owner()) == []


def test_without_a_person_an_unapproved_server_cannot_be_attached(mcp_agent):
    agent, ui = mcp_agent
    agent.config.interactive = False

    with pytest.raises(Exception, match="nobody can answer"):
        agent.skills.attach("mcp:echo")
    assert agent.resources.owned_by(Owner()) == []
