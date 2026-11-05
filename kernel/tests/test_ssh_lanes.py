# -*- coding: utf-8 -*-
"""Several sessions working in one folder at the same time: one daemon each (a lane), and which of them an interface belongs to."""
from __future__ import annotations

import io
import os
import time
from pathlib import Path

import pytest

from kernel.ssh import daemon as daemon_module
from kernel.ssh import proxy as proxy_module
from kernel.ssh import wire
from kernel.ssh.command import Target
from kernel.ssh.paths import daemon_id, daemon_paths, lanes_of
from kernel.ssh.proxy import Proxy
from kernel.tests.ssh_support import FakeCoreDaemon
from kernel.tests.test_ssh_daemon import is_type

pytestmark = pytest.mark.skipif(os.name != "posix", reason="the remote core is POSIX-only")


@pytest.fixture
def config_dir():
    folder = Path(os.environ["KVIST_CONFIG_DIR"])  # the short one conftest made: a socket path lives under it
    return folder


def test_each_lane_of_a_folder_has_its_own_files_and_the_first_keeps_the_old_name(config_dir, tmp_path):
    workspace = str(tmp_path / "project")

    assert daemon_id(workspace, "") == daemon_id(workspace) != daemon_id(workspace, "2")
    assert daemon_paths(workspace, "2").socket != daemon_paths(workspace).socket
    assert lanes_of(workspace) == []

    for lane in ("", "3", "2"):
        daemon_paths(workspace, lane).root.mkdir(parents=True)
    daemon_paths(str(tmp_path / "another"), "2").root.mkdir(parents=True)

    assert lanes_of(workspace) == ["", "2", "3"]


@pytest.fixture
def lane_core(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_module, "_REAP_SECONDS", 0.05)
    made: list[FakeCoreDaemon] = []

    def make(lane: str = "") -> FakeCoreDaemon:
        made.append(FakeCoreDaemon(tmp_path, lane=lane))
        return made[-1]

    yield make
    for item in made:
        item.close()


def test_a_daemon_says_which_lane_it_is_which_session_it_works_on_and_whether_it_is_working(lane_core):
    core = lane_core("2")
    core.agent._session_title = "Fix the build"

    quiet = core.daemon.status()
    core.agent.control._state = "running"
    working = core.daemon.status()

    assert (quiet["lane"], quiet["session"], quiet["title"]) == ("2", "default", "Fix the build")
    assert (quiet["busy"], working["busy"]) == (False, True)
    assert quiet["revision"] == wire.REVISION


def test_work_nobody_saw_shows_in_the_status_without_being_used_up_by_looking(lane_core):
    core = lane_core()
    core.daemon.inbox.put("work while nobody watches")
    deadline = time.monotonic() + 5
    while b'"turn_end"' not in core.paths.log.read_bytes() and time.monotonic() < deadline:
        time.sleep(0.02)

    assert core.daemon.status()["unseen_work"] is True
    assert core.daemon.status()["unseen_work"] is True


def waiting_for_an_idle_lane(core: FakeCoreDaemon, seconds: float = 0.6) -> bool:
    time.sleep(seconds)
    return core.daemon._stop_asked


def test_an_extra_lane_with_nothing_to_do_ends_by_itself_so_sessions_do_not_pile_up(lane_core, monkeypatch):
    monkeypatch.setattr(daemon_module, "_IDLE_LANE_SECONDS", 0.3)
    core = lane_core("2")

    assert waiting_for_an_idle_lane(core) is True


@pytest.mark.parametrize("why", ["somebody is attached", "it is working", "a result nobody saw", "a stopped task"])
def test_an_extra_lane_stays_while_there_is_a_reason(lane_core, monkeypatch, why):
    monkeypatch.setattr(daemon_module, "_IDLE_LANE_SECONDS", 0.3)
    core = lane_core("2")
    if why == "somebody is attached":
        core.client()
    elif why == "it is working":
        core.agent.control._state = "running"
    elif why == "a result nobody saw":
        core.log._unseen_turns = 1
    else:
        core.agent._peek_checkpoint = lambda: {"goal": "unfinished"}

    assert waiting_for_an_idle_lane(core) is False


def test_the_first_lane_never_ends_by_itself(lane_core, monkeypatch):
    monkeypatch.setattr(daemon_module, "_IDLE_LANE_SECONDS", 0.3)

    assert waiting_for_an_idle_lane(lane_core("")) is False


# --- which lane an interface belongs to --------------------------------------------------------------------------


class ScriptUI:
    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.asked: list[tuple[str, list[dict]]] = []

    def choose_option(self, title, options, current_value="", help_text=""):
        self.asked.append((title, options))
        return self.answers.pop(0)


def a_proxy(**options) -> Proxy:
    return Proxy(
        Target("me@box", None, "/srv/app"), stdin=io.BytesIO(), stdout=io.BytesIO(), stderr=io.StringIO(), language="en",
        prepare_machine=lambda *a, **k: True, **options,
    )


def lane(name: str = "", *, busy=False, unseen=False, attached=0, title="", session="s") -> dict:
    return {"lane": name, "busy": busy, "unseen_work": unseen, "attached": attached, "title": title, "session": session}


def test_with_nothing_busy_the_first_lane_is_used_and_gets_a_new_session():
    proxy, ui = a_proxy(), ScriptUI()

    proxy._pick_lane(ui, [lane()])

    assert (proxy.target.lane, proxy._fresh_session_due, ui.asked) == ("", True, [])


def test_a_free_extra_lane_is_taken_before_the_busy_first_one_is_disturbed(monkeypatch):
    proxy, ui = a_proxy(), ScriptUI("@new")

    proxy._pick_lane(ui, [lane(busy=True, title="Fix the build"), lane("2")])

    assert proxy.target.lane == "2" and proxy._fresh_session_due is True
    assert "already" in ui.asked[0][0]
    assert [option["value"] for option in ui.asked[0][1]] == ["@first", "@new"]
    assert "working now" in ui.asked[0][1][0]["label"] and "Fix the build" in ui.asked[0][1][0]["label"]


def test_when_every_lane_is_taken_another_daemon_is_started_and_its_session_is_already_new(monkeypatch):
    monkeypatch.setattr(proxy_module, "call_op", lambda target, operation: (0, {"ok": True, "lane": "3"}, ""))
    proxy, ui = a_proxy(), ScriptUI("@new")

    proxy._pick_lane(ui, [lane(busy=True), lane("2", unseen=True)])

    assert proxy.target.lane == "3" and proxy._fresh_session_due is False


def test_joining_a_session_that_is_working_asks_for_no_new_one():
    proxy, ui = a_proxy(), ScriptUI("2")

    proxy._pick_lane(ui, [lane(busy=True), lane("2", unseen=True, title="Done while away")])

    assert proxy.target.lane == "2" and proxy._fresh_session_due is False
    assert {option["value"] for option in ui.asked[0][1]} == {"@first", "2", "@new"}


def test_a_session_open_in_another_window_is_offered_too_and_leaving_the_dialog_starts_a_new_one(monkeypatch):
    monkeypatch.setattr(proxy_module, "call_op", lambda target, operation: (0, {"ok": True, "lane": "2"}, ""))
    proxy, ui = a_proxy(), ScriptUI(None)

    proxy._pick_lane(ui, [lane(attached=1)])

    assert "another window" in ui.asked[0][1][0]["label"]
    assert proxy.target.lane == "2"


def test_a_session_the_person_asked_to_continue_is_joined_where_it_is_running():
    proxy, ui = a_proxy(open_session="chat-1"), ScriptUI()

    proxy._pick_lane(ui, [lane(busy=True, session="chat-9"), lane("2", busy=True, session="chat-1")])

    assert proxy.target.lane == "2" and proxy._open_session is None and ui.asked == []


def test_a_session_that_is_not_running_is_opened_beside_the_busy_ones_not_in_them(monkeypatch):
    monkeypatch.setattr(proxy_module, "call_op", lambda target, operation: (0, {"ok": True, "lane": "2"}, ""))
    proxy, ui = a_proxy(open_session="chat-1"), ScriptUI()

    proxy._pick_lane(ui, [lane(busy=True, session="chat-9")])

    assert proxy.target.lane == "2" and proxy._open_session == "chat-1" and ui.asked == []


def test_when_another_daemon_cannot_be_started_the_person_is_told_and_works_in_the_first_lane(monkeypatch):
    monkeypatch.setattr(proxy_module, "call_op", lambda target, operation: (1, {"ok": False, "error": "At most 8 sessions"}, ""))
    proxy, ui = a_proxy(), ScriptUI("@new")
    shown: list[tuple[str, str]] = []
    proxy._notice = lambda text, level="info": shown.append((level, text))

    proxy._pick_lane(ui, [lane(busy=True)])

    assert proxy.target.lane == "" and "At most 8 sessions" in shown[0][1] and shown[0][0] == "error"


def test_a_connection_that_went_quiet_without_being_closed_stops_counting_as_an_open_window(lane_core, monkeypatch):
    monkeypatch.setattr(daemon_module, "_SILENT_SECONDS", 0.3)
    core = lane_core()
    ghost = core.client("ghost")
    ghost.wait_for(is_type(wire.ATTACHED))
    assert core.daemon.status()["attached"] == 1

    ghost.wait_closed(timeout=3)

    assert core.daemon.status()["attached"] == 0


def test_a_connection_that_keeps_pinging_stays(lane_core, monkeypatch):
    monkeypatch.setattr(daemon_module, "_SILENT_SECONDS", 0.4)
    core = lane_core()
    alive = core.client("alive")
    alive.wait_for(is_type(wire.ATTACHED))

    for _ in range(8):
        alive.ping()
        time.sleep(0.1)

    assert core.daemon.status()["attached"] == 1 and not alive.closed


def test_a_daemon_of_an_older_kvist_that_says_nothing_about_lanes_is_a_free_first_lane():
    proxy, ui = a_proxy(), ScriptUI()

    proxy._pick_lane(ui, [{"pid": 1, "attached": 0}])

    assert (proxy.target.lane, proxy._fresh_session_due, ui.asked) == ("", True, [])
