"""Building a skill in a task: draft, independent checks, the person's approval, atomic install."""
from __future__ import annotations

import pytest

from kernel.agent_tools import run_agent_tool
from kernel.plugins.builder import MAX_CHECKS_PER_TASK, BuildError, BuildLedger, check_draft, install_draft
from kernel.plugins.grants import GrantStore
from kernel.plugins.registry import SkillRegistry
from kernel.protocol import Action, AgentCommand
from kernel.tests.conftest import NullUI
from kernel.tests.skill_fixtures import requires_runner


class Approver(NullUI):
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.dialogs: list[tuple[str, str]] = []

    def confirm(self, title, body=""):
        self.dialogs.append((title, body))
        return self.answer


@pytest.fixture
def builder_agent(agent_factory, tmp_path, monkeypatch):
    config = tmp_path / "config"
    config.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(config))
    agent = agent_factory(interactive=True)
    agent._state.response_language = "en"
    agent.skills.registry = SkillRegistry(config / "plugins" / "skill")
    agent.skills.grants = GrantStore(config / "plugins" / "grants.json")
    ui = Approver(True)
    object.__setattr__(agent._ui, "_ui", ui)
    return agent, ui


def _skills(agent, **args):
    return run_agent_tool(agent, AgentCommand("edit", Action("skills", args)))


@requires_runner
def test_scaffold_check_and_install_with_the_persons_approval(builder_agent):
    agent, ui = builder_agent

    scaffolded = _skills(agent, op="scaffold", skill="greeter", summary="Greets people.")
    checked = _skills(agent, op="check", path="kvist-skills/greeter")
    installed = _skills(agent, op="install", path="kvist-skills/greeter")

    assert scaffolded["ok"] and "kvist-skills/greeter/plugin.py" in scaffolded["data"]["files"]
    assert "kvist-skills/greeter/plugin.py" in agent._state.files_changed  # journaled like any write
    assert checked["data"]["ok"], checked["data"]["summary"]
    assert installed["ok"], installed.get("error")
    assert ui.dialogs and "Built by the agent" in ui.dialogs[0][1] and "Kvist's checks" in ui.dialogs[0][1]
    entry = agent.skills.registry.entry("greeter")
    assert entry.available
    assert agent.skills.grants.get("greeter", entry.digest, agent.executor.workspace) is not None


def test_install_needs_a_passing_check_of_the_same_content(builder_agent):
    agent, ui = builder_agent
    _skills(agent, op="scaffold", skill="greeter")

    refused = _skills(agent, op="install", path="kvist-skills/greeter")

    assert not refused["ok"] and "no passing check" in refused["error"]
    assert agent.skills.registry.entry("greeter") is None
    assert ui.dialogs == []


@requires_runner
def test_a_draft_changed_after_its_check_must_be_checked_again(builder_agent):
    agent, ui = builder_agent
    _skills(agent, op="scaffold", skill="greeter")
    assert _skills(agent, op="check", path="kvist-skills/greeter")["data"]["ok"]
    plugin = agent.executor.workspace / "kvist-skills" / "greeter" / "plugin.py"
    plugin.write_text(plugin.read_text() + "\n# changed after the check\n", encoding="utf-8")

    refused = _skills(agent, op="install", path="kvist-skills/greeter")

    assert "changed after its check" in refused["error"]
    assert agent.skills.registry.entry("greeter") is None


@requires_runner
def test_a_declined_install_installs_nothing(builder_agent):
    agent, ui = builder_agent
    ui.answer = False
    _skills(agent, op="scaffold", skill="greeter")
    _skills(agent, op="check", path="kvist-skills/greeter")

    refused = _skills(agent, op="install", path="kvist-skills/greeter")

    assert "did not approve" in refused["error"]
    assert agent.skills.registry.entry("greeter") is None
    assert agent.skills.grants.granted_digests("greeter") == []


def test_without_a_person_nothing_is_installed(builder_agent):
    agent, ui = builder_agent
    agent.config.interactive = False
    ledger = BuildLedger()
    ledger.checked["kvist-skills/x"] = type("C", (), {"ok": True, "digest": "sha256:x", "report": None})()

    with pytest.raises(BuildError, match="nobody can answer"):
        install_draft(agent, ledger, "kvist-skills/x")


def test_checks_are_limited_per_task(builder_agent):
    agent, ui = builder_agent
    ledger = BuildLedger(checks=MAX_CHECKS_PER_TASK)

    with pytest.raises(BuildError, match="endless fix loops"):
        check_draft(agent, ledger, "kvist-skills/x")


@pytest.mark.parametrize("path", ["../outside", ".kvist/plugins", "/etc"])
def test_a_draft_must_be_a_workspace_folder_outside_kvist(builder_agent, path):
    agent, ui = builder_agent

    result = _skills(agent, op="check", path=path)

    assert not result["ok"]


def test_the_builder_cannot_write_into_installed_skills_or_grants(builder_agent):
    agent, ui = builder_agent
    config_file = agent.skills.grants.path
    command = AgentCommand("edit", Action("write_file", {"path": str(config_file), "content": "{}"}))

    feedback = agent._execute_command(command)

    assert not feedback["ok"]
    assert not config_file.exists()


def test_a_shell_command_cannot_read_a_config_directory_inside_the_project(tmp_path, monkeypatch):
    from kernel.sandbox import SandboxMode, bubblewrap_available, wrap_command_for_sandbox

    if not bubblewrap_available():
        pytest.skip("needs Bubblewrap")
    config = tmp_path / "kvist-config"
    config.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(config))

    argv = wrap_command_for_sandbox(["cat", "x"], SandboxMode.BUBBLEWRAP, tmp_path, tmp_path)

    assert ["--tmpfs", str(config.resolve())] == argv[argv.index(str(config.resolve())) - 1:argv.index(str(config.resolve())) + 1]


@requires_runner
def test_a_skill_the_agent_installed_waits_for_a_restart(builder_agent):
    agent, _ui = builder_agent
    _skills(agent, op="scaffold", skill="greeter", summary="Greets people.")
    _skills(agent, op="check", path="kvist-skills/greeter")

    installed = _skills(agent, op="install", path="kvist-skills/greeter")
    refused = _skills(agent, op="attach", skill="greeter")
    listed = _skills(agent, op="list")

    assert installed["ok"] and installed["data"]["available"] is False
    assert installed["data"]["available_after_restart"] is True
    assert not refused["ok"] and "restart" in refused["error"].lower()
    row = next(item for item in listed["data"]["skills"] if item["id"] == "greeter")
    assert row["available"] is False and "restarted" in row["reason"]
    assert "available after Kvist is restarted" in agent.skills.prompt_section("en")

    from kernel.plugins import skills as skills_module

    skills_module._PENDING_RESTART.clear()  # what a new process starts with
    attached = _skills(agent, op="attach", skill="greeter")
    assert attached["ok"], attached.get("error")
