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
from kernel.plugins.resources import Owner
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


_FAULTY = """<!doctype html><html><head><title>Faults</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  body { margin: 0; background: #fff; color: #111; font: 16px sans-serif }
  @media (prefers-color-scheme: dark) { body { background: #111; color: #eee } }
</style></head><body>
<h1>Faults</h1><h3>Skipped a level</h3>
<div id="wide" style="width: 600px">Six hundred pixels wide</div>
<div id="clip" style="width: 80px; height: 20px; overflow: hidden; white-space: nowrap">A label far too long for its box</div>
<p id="low" style="color: #999">Grey on white</p>
<p id="ok" style="color: oklch(0.25 0 0)">Dark enough</p>
<p id="fixed" style="color: #333">Fine on white, not on black</p>
<button id="tiny" style="width: 16px; height: 16px; padding: 0">x</button>
<button id="icon" style="width: 40px; height: 40px"><svg width="16" height="16"></svg></button>
<img src="data:image/gif;base64,R0lGODlhAQABAAAAACw=" width="10" height="10">
<input id="search" placeholder="Search…">
</body></html>"""


def test_emulate_and_check_page_measure_a_page_as_phones_and_dark_themes_see_it(browser_agent):
    agent, ui = browser_agent
    (agent.executor.workspace / "index.html").write_text(_FAULTY, encoding="utf-8")
    _call(agent, "open_workspace_page", {"path": "index.html"})

    phone = _call(agent, "emulate", {"width": 390, "height": 844, "reduced_motion": "reduce"})
    narrow = _call(agent, "check_page", {})
    dark = _call(agent, "emulate", {"color_scheme": "dark"})
    night = _call(agent, "check_page", {})
    _call(agent, "emulate", {"width": 1440, "height": 900, "color_scheme": "light"})
    wide = _call(agent, "check_page", {})

    assert (phone["width"], phone["height"], phone["reduced_motion"]) == (390, 844, True)
    assert dark["color_scheme"] == "dark" and night["color_scheme"] == "dark"
    assert narrow["page_scrolls_sideways"] and any("div#wide" in row["element"] for row in narrow["wider_than_window"])
    assert not wide["page_scrolls_sideways"] and wide["wider_than_window"] == []
    assert any("div#clip" in row["element"] for row in narrow["clipped_text"])
    low = {row["element"].split(" ")[0]: row for row in narrow["contrast"]["below_aa"]}
    assert "p#low" in low and low["p#low"]["needs"] == 4.5 and low["p#low"]["ratio"] < 3
    assert "p#ok" not in low and "p#fixed" not in low
    assert "p#fixed" in {row["element"].split(" ")[0] for row in night["contrast"]["below_aa"]}
    assert any("button#tiny" in row["element"] for row in narrow["small_targets"])
    assert any("button#icon" in row for row in narrow["controls_without_name"])
    assert len(narrow["images_without_alt"]) == 1
    assert any("input#search" in row and "placeholder only" in row for row in narrow["fields_without_label"])
    assert narrow["document"]["heading_jumps"] == ["h1 → h3"] and narrow["document"]["lang"] is None
    assert narrow["document"]["charset"] != "UTF-8"  # no <meta charset>: "Search…" arrives garbled


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

    from kernel.protocol.protocol import Action, AgentCommand

    monkeypatch.setenv("AGENT_SANDBOX_MODE", "auto")
    agent, ui = browser_agent
    from kernel.sandbox.executor import SafeExecutor

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
