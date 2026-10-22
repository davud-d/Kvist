"""A running task keeps its skill version, a resumed task never replays a skill's side effect,
and the builder reports installed skills that already do the job."""
from __future__ import annotations

import pytest

from kernel.agent.agent_runtime import _build_pending_action
from kernel.plugins.checks import similar_skills
from kernel.plugins.manifest import load_manifest
from kernel.plugins.registry import SkillRegistry
from kernel.tests.conftest import NullUI, make_command
from kernel.tests.skill_fixtures import install, requires_runner, write_skill

WRITING_TOOL = {
    "name": "send",
    "summary": "Send the text to the outside world.",
    "readonly": False,
    "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"],
                   "additionalProperties": False},
}

VERSION_PLUGIN = """
def describe():
    return {"tools": ["say"]}

def execute(tool_name, arguments, context):
    return {"version": "%s"}
"""


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    path = tmp_path / "config"
    path.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(path))
    return path


@pytest.fixture
def registry(config_dir):
    return SkillRegistry(config_dir / "plugins" / "skill")


class _Approving(NullUI):
    def confirm(self, title, body=""):
        return True


def _agent(agent_factory):
    agent = agent_factory(max_steps=4, interactive=True)
    object.__setattr__(agent._ui, "_ui", _Approving())
    agent._state.response_language = "en"
    return agent


@requires_runner
def test_a_task_keeps_the_version_it_attached_when_the_skill_is_updated(agent_factory, registry, tmp_path):
    install(registry, write_skill(tmp_path / "v1", plugin=VERSION_PLUGIN % "0.1.0"))
    agent = _agent(agent_factory)
    agent.skills.attach("echo")

    second = registry.install(write_skill(tmp_path / "v2", version="0.2.0", plugin=VERSION_PLUGIN % "0.2.0"))
    registry.activate("echo", "0.2.0", second.digest)

    during = agent.skills.call("echo", "say", {"text": "x"}, "c1")
    assert during["ok"] and during["data"]["version"] == "0.1.0"

    agent.skills.finish_task("task_finished")
    fresh = _agent(agent_factory)
    fresh.skills.attach("echo")
    after = fresh.skills.call("echo", "say", {"text": "x"}, "c2")
    assert after["ok"] and after["data"]["version"] == "0.2.0"
    fresh.skills.finish_task("task_finished")


@requires_runner
def test_an_interrupted_skill_call_with_effects_is_reported_not_replayed_on_resume(agent_factory, registry, tmp_path):
    install(registry, write_skill(tmp_path / "src", tools=[WRITING_TOOL],
                                  plugin="def describe():\n    return {'tools': ['send']}\n"
                                         "def execute(tool_name, arguments, context):\n    return {}\n"))
    agent = _agent(agent_factory)
    agent.skills.attach("echo")
    command = make_command("skill_call", {"skill": "echo", "tool": "send", "arguments": {"text": "hi"}})

    pending = _build_pending_action(agent, command, 3)

    assert pending is not None
    assert pending["action_kind"] == "side_effect" and pending["tool"] == "send"
    assert "arguments" not in pending  # identity only: nothing that could be re-sent
    assert agent._reconcile_pending_action(pending) is None  # "stop and ask", never "run it again"
    agent.skills.finish_task("task_finished")


@requires_runner
def test_a_read_only_skill_call_has_nothing_to_protect_on_resume(agent_factory, registry, tmp_path):
    install(registry, write_skill(tmp_path / "src"))
    agent = _agent(agent_factory)
    agent.skills.attach("echo")
    command = make_command("skill_call", {"skill": "echo", "tool": "say", "arguments": {"text": "hi"}})

    assert _build_pending_action(agent, command, 3) is None
    agent.skills.finish_task("task_finished")


def test_the_builder_reports_installed_skills_that_already_do_the_job(registry, tmp_path):
    install(registry, write_skill(tmp_path / "old", "pagecheck", tools=[{
        **WRITING_TOOL, "name": "check_page", "summary": "Check a web page for broken links and errors."}],
        plugin="def describe():\n    return {'tools': ['check_page']}\n"
               "def execute(tool_name, arguments, context):\n    return {}\n",
        extra={"summary": {"en": "Check a web page for broken links and errors", "ru": "Проверка страницы"}}))
    draft = write_skill(tmp_path / "draft", "linkcheck", tools=[{
        **WRITING_TOOL, "name": "check_page", "summary": "Check a web page for broken links."}],
        plugin="def describe():\n    return {'tools': ['check_page']}\n"
               "def execute(tool_name, arguments, context):\n    return {}\n",
        extra={"summary": {"en": "Check a web page for broken links and errors", "ru": "Проверка страницы"}})
    unrelated = write_skill(tmp_path / "other", "notes", tools=[{
        **WRITING_TOOL, "name": "summarise", "summary": "Summarise meeting minutes."}],
        plugin="def describe():\n    return {'tools': ['summarise']}\n"
               "def execute(tool_name, arguments, context):\n    return {}\n",
        extra={"summary": {"en": "Meeting minutes helper", "ru": "Протоколы"}})

    assert similar_skills(load_manifest(draft), registry) == ["pagecheck"]
    assert similar_skills(load_manifest(unrelated), registry) == []
