"""The bundled browser skill on a real virtual screen (slow: Xvfb, i3, Chromium, Playwright).

Runs only where the skill and its Python dependencies are installed: point
KVIST_TEST_SKILL_CONFIG at a Kvist configuration directory that holds
``plugins/skill/browser`` with its environment (``kvist --skill-op install``
and ``--skill-op install-deps``). Approvals go to a temporary grant file, so the
configuration directory is only read.
"""
from __future__ import annotations

import os
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from kernel.plugins.grants import GrantStore
from kernel.plugins.registry import SkillRegistry
from kernel.resources import Owner
from kernel.tests.conftest import AllowAllApprovals, NullUI

pytestmark = pytest.mark.slow

_CONFIG = os.environ.get("KVIST_TEST_SKILL_CONFIG", "")

_SITE = """<!doctype html><html><head><title>Greeter</title></head><body>
<label for="name">Your name</label><input id="name">
<button onclick="document.getElementById('out').textContent='Hello, '+document.getElementById('name').value+'!'">Greet</button>
<button onclick="throw new Error('boom from test')">Break</button>
<p id="out"></p></body></html>"""


class _UI(NullUI):
    def __init__(self) -> None:
        self.screens: list[dict] = []

    def confirm(self, title, body=""):
        return True

    def print_screen(self, payload):
        self.screens.append(payload)


@pytest.fixture
def browser_agent(agent_factory, tmp_path):
    root = Path(_CONFIG) / "plugins" / "skill"
    registry = SkillRegistry(root)
    entry = registry.entry("browser") if root.is_dir() else None
    if entry is None or not entry.available:
        pytest.skip(f"browser skill not usable here: {entry.reason if entry else 'KVIST_TEST_SKILL_CONFIG not set'}")
    agent = agent_factory(interactive=True)
    ui = _UI()
    object.__setattr__(agent._ui, "_ui", ui)
    agent._state.response_language = "en"
    agent._run_id = "browser-test"
    agent.skills.registry = registry
    agent.skills.grants = GrantStore(tmp_path / "grants.json")
    agent.skills.attach("browser")
    yield agent, ui
    agent.skills.finish_task("test_end")


def _call(agent, tool, arguments):
    feedback = agent.skills.call("browser", tool, arguments, "c")
    assert feedback["ok"], feedback.get("error")
    return feedback["data"]["result"]


def test_a_click_is_verified_by_its_effect_and_errors_and_screenshots_are_kept(browser_agent):
    agent, ui = browser_agent
    site = agent.executor.workspace / "site"
    site.mkdir()
    (site / "index.html").write_text(_SITE, encoding="utf-8")
    (site / ".env").write_text("SECRET=1", encoding="utf-8")

    page = _call(agent, "open_workspace_page", {"path": "site"})
    refs = {element["name"]: element["ref"] for element in page["elements"]}
    _call(agent, "type", {"ref": refs["Your name"], "value": "Kvist"})
    clicked = _call(agent, "click", {"ref": refs["Greet"], "expect_text": "Hello, Kvist!"})
    broken = _call(agent, "click", {"ref": refs["Break"]})
    shot = _call(agent, "screenshot", {})

    assert page["title"] == "Greeter" and page["files_not_copied"] == [".env: denied"]
    assert clicked["expectation_met"] is True and "Hello, Kvist!" in clicked["new_text"]
    assert any("boom from test" in error for error in broken["console_errors"])
    assert Path(shot["artifact_path"]).stat().st_size > 1000


def test_the_preview_is_the_same_screen_and_needs_its_token(browser_agent):
    agent, ui = browser_agent
    (agent.executor.workspace / "index.html").write_text(_SITE, encoding="utf-8")
    _call(agent, "open_workspace_page", {"path": "index.html"})

    screen = ui.screens[-1]
    frame = urllib.request.urlopen(screen["frame_url"], timeout=10).read()
    assert frame[:3] == b"\xff\xd8\xff"
    with pytest.raises(urllib.error.HTTPError) as anonymous:
        urllib.request.urlopen(screen["frame_url"].split("?")[0], timeout=10)
    assert anonymous.value.code == 401


def test_a_port_no_task_server_exposes_is_refused_by_the_core(browser_agent):
    agent, ui = browser_agent

    page = _call(agent, "open_page", {"url": "http://localhost:9999/"})

    assert page["http_status"] == 403
    assert "no server of this task" in page["text"]


def test_finishing_the_task_leaves_no_browser_screen_or_viewer(browser_agent):
    agent, ui = browser_agent
    (agent.executor.workspace / "index.html").write_text(_SITE, encoding="utf-8")
    _call(agent, "open_workspace_page", {"path": "index.html"})
    host = agent.skills.attached["browser"].host
    pid, scope = host.process.pid, host.process.scope.path
    viewer = ui.screens[-1]["frame_url"]

    report = agent.skills.finish_task("task_finished")

    assert report.ok, report.summary("en")
    assert not os.path.exists(f"/proc/{pid}") and not scope.exists()
    assert agent.resources.owned_by(Owner()) == []
    with pytest.raises(OSError):
        urllib.request.urlopen(viewer, timeout=2)


def test_a_dev_server_reaches_the_browser_only_through_the_broker(agent_factory, browser_agent, monkeypatch,
                                                                    trusted_python_name):
    import socket

    from kernel.protocol import Action, AgentCommand

    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    agent, ui = browser_agent
    from kernel.executor import SafeExecutor

    agent.executor._terminal = SafeExecutor(agent.executor.workspace, approvals=AllowAllApprovals())._terminal
    (agent.executor.workspace / "index.html").write_text(_SITE, encoding="utf-8")
    command = AgentCommand("inspect", Action("run_shell", {
        "command": [trusted_python_name, "-m", "http.server", "8124", "--bind", "127.0.0.1"],
        "background": True, "expose_port": 8124}))

    started = agent._execute_command(command)
    time.sleep(1.0)

    assert started["ok"], started
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", 8124), timeout=1)
    page = _call(agent, "open_page", {"url": "http://localhost:8124/index.html"})
    assert page["http_status"] == 200 and page["title"] == "Greeter"
    agent.executor.jobs.stop_all()
