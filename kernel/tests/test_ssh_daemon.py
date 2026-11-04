# -*- coding: utf-8 -*-
"""The daemon: who may write, what a frontend that arrives late is shown, and what survives a break."""
from __future__ import annotations

import json
import threading
import time

import pytest

from kernel.ssh import daemon as daemon_module
from kernel.ssh import wire
from kernel.tests.ssh_support import FakeCoreDaemon, echo

pytestmark = pytest.mark.skipif(daemon_module.os.name != "posix", reason="the remote core is POSIX-only")


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setattr(daemon_module, "_REAP_SECONDS", 0.05)
    made: list[FakeCoreDaemon] = []

    def make(handler=echo, **options) -> FakeCoreDaemon:
        made.append(FakeCoreDaemon(tmp_path, handler, **options))
        return made[-1]

    yield make
    for item in made:
        item.close()


def is_type(name: str):
    return lambda line: line.get("type") == name


def test_a_fresh_frontend_gets_its_handshake_first_then_the_role(core):
    daemon = core()
    client = daemon.client()

    control = client.wait_for(is_type("control"))
    # Where the daemon notes how far it has read depends on timing; the order of the rest does not.
    types = [name for name in client.types() if name != "remote_offset"]

    assert types[:4] == ["remote_attached", "hello", "manifest", "control"]
    assert control["role"] == "driver"


def test_a_goal_from_the_driver_runs_and_its_events_come_back(core):
    daemon = core()
    client = daemon.client()
    client.wait_for(is_type("control"))

    client.send("write a poem")

    assert client.wait_for(is_type("final"))["message"] == "done write a poem"
    assert daemon.agent.goals == ["write a poem"]
    assert client.wait_for(is_type("remote_offset"))["offset"] > 0


def test_a_second_frontend_only_watches_and_cannot_start_a_task(core):
    daemon = core()
    driver = daemon.client("driver")
    driver.wait_for(is_type("control"))
    watcher = daemon.client("watcher")

    control = watcher.wait_for(is_type("control"))
    watcher.send("a different task")
    watcher.send({"type": "cancel"})
    watcher.wait_for(lambda line: line.get("type") == "control" and watcher.types().count("control") >= 2)

    assert control["role"] == "observer"
    assert "take over" in control["notice"]
    assert daemon.agent.goals == []
    assert not daemon.agent.cancelled.is_set()


def test_a_late_frontend_sees_what_already_happened(core):
    daemon = core()
    driver = daemon.client("driver")
    driver.wait_for(is_type("control"))
    driver.send("first")
    driver.wait_for(is_type("final"))

    late = daemon.client("late")

    assert late.wait_for(is_type("final"))["message"] == "done first"
    assert late.types().index("hello") < late.types().index("final")


def test_takeover_hands_the_session_over_and_the_old_driver_is_told(core):
    daemon = core()
    first = daemon.client("first")
    first.wait_for(is_type("control"))
    second = daemon.client("second")
    second.wait_for(is_type("control"))

    second.send({"type": wire.TAKEOVER})
    second.wait_for(lambda line: line.get("type") == "control" and line["role"] == "driver")
    first.wait_for(lambda line: line.get("type") == "control" and line["role"] == "observer" and first.types().count("control") >= 2)
    second.send("now it is mine")
    second.wait_for(is_type("final"))
    first.send("and this is refused")
    time.sleep(0.2)

    assert daemon.agent.goals == ["now it is mine"]


def test_a_silent_driver_loses_the_lease_and_the_next_frontend_may_drive(core):
    daemon = core(lease_seconds=0.3)
    gone = daemon.client("gone")
    gone.wait_for(is_type("control"))

    gone.wait_closed()
    next_one = daemon.client("next")

    assert next_one.wait_for(is_type("control"))["role"] == "driver"


def test_pings_keep_the_lease(core):
    daemon = core(lease_seconds=0.4)
    driver = daemon.client("driver")
    driver.wait_for(is_type("control"))

    for _ in range(6):
        driver.ping()
        time.sleep(0.15)
    driver.send("still here")

    assert driver.wait_for(is_type("final"))["message"] == "done still here"


def test_continuing_from_an_offset_repeats_nothing(core):
    daemon = core()
    first = daemon.client("first")
    first.wait_for(is_type("control"))
    first.send("one")
    first.wait_for(is_type("turn_end"))
    time.sleep(0.3)
    marker = [line for line in first.lines if line.get("type") == "remote_offset"][-1]
    first.send("two")
    first.wait_for(lambda line: line.get("type") == "final" and line["message"] == "done two")
    first.close()

    again = daemon.client("first", start=marker["offset"], segment=marker["segment"])
    again.wait_for(lambda line: line.get("type") == "final")

    assert [line["message"] for line in again.lines if line.get("type") == "final"] == ["done two"]
    assert "hello" not in again.types()


def test_a_log_from_another_segment_is_refused_not_mixed_in(core):
    daemon = core()

    client = daemon.client("stale", start=10, segment=1)

    reply = client.wait_for(is_type("remote_attached"))
    assert reply == {"type": "remote_attached", "ok": False, "reason": "segment"}


def test_an_answered_dialog_is_not_shown_again_to_a_later_frontend(core):
    def asks(agent, ui, goal):
        answer = ui.prompt_text("which one?")
        ui.print_final(f"you said {answer}")
        return answer

    daemon = core(asks)
    driver = daemon.client("driver")
    driver.wait_for(is_type("control"))
    driver.send("start")
    request = driver.wait_for(is_type("input_request"))

    during = daemon.client("during")
    during.wait_for(is_type("input_request"))
    driver.send(json.dumps({"request_id": request["request_id"], "value": "blue"}))
    driver.wait_for(is_type("final"))
    after = daemon.client("after")
    after.wait_for(is_type("final"))

    assert "input_request" not in after.types()


def test_whoever_takes_over_gets_the_dialogs_that_are_still_open(core):
    def asks(agent, ui, goal):
        answer = ui.prompt_text("which one?")
        ui.print_final(f"you said {answer}")
        return answer

    daemon = core(asks)
    driver = daemon.client("driver")
    driver.wait_for(is_type("control"))
    driver.send("start")
    request = driver.wait_for(is_type("input_request"))
    watcher = daemon.client("watcher")
    watcher.wait_for(is_type("input_request"))

    watcher.send({"type": wire.TAKEOVER})
    again = watcher.wait_for(lambda line: line.get("type") == "input_request" and watcher.types().count("input_request") >= 2)
    watcher.send(json.dumps({"request_id": request["request_id"], "value": "green"}))

    assert again["request_id"] == request["request_id"]
    assert watcher.wait_for(is_type("final"))["message"] == "you said green"


def test_the_stop_request_ends_the_core_even_in_the_middle_of_a_turn(core):
    release = threading.Event()

    def stuck(agent, ui, goal):
        agent.cancelled.wait(5.0)
        return "stopped"

    daemon = core(stuck)
    driver = daemon.client("driver")
    driver.wait_for(is_type("control"))
    driver.send("work forever")
    time.sleep(0.1)

    answer = daemon.client("op", op="stop").wait_for(is_type("remote_stopped"))
    daemon.thread.join(timeout=5.0)

    assert answer["type"] == "remote_stopped"
    assert not daemon.thread.is_alive()
    release.set()


def test_the_same_frontend_reconnecting_replaces_its_old_connection_and_keeps_the_lease(core):
    daemon = core()
    old = daemon.client("same")
    old.wait_for(is_type("control"))

    new = daemon.client("same")
    old.wait_closed()
    new.wait_for(is_type("control"))
    new.send("after reconnect")

    assert new.wait_for(is_type("final"))["message"] == "done after reconnect"
    assert new.wait_for(is_type("control"))["role"] == "driver"


def test_status_answers_without_attaching(core):
    daemon = core()

    answer = daemon.client("probe", op="status").wait_for(is_type("remote_status"))

    assert answer["protocol_version"] == 1
    assert answer["attached"] == 0


def wait_for_a_turn_to_end(daemon, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while b'"turn_end"' not in daemon.paths.log.read_bytes():
        if time.monotonic() > deadline:
            raise TimeoutError("the turn did not end")
        time.sleep(0.02)


def test_work_done_while_nobody_was_attached_is_reported_to_the_next_frontend_only_once(core):
    daemon = core()
    daemon.daemon.inbox.put("work while nobody watches")
    wait_for_a_turn_to_end(daemon)

    first = daemon.client("first")
    second = daemon.client("second")

    assert first.wait_for(is_type(wire.ATTACHED))["unseen_work"] is True
    assert second.wait_for(is_type(wire.ATTACHED))["unseen_work"] is False


def test_a_turn_that_ended_in_front_of_a_frontend_is_not_unseen_work(core):
    daemon = core()
    watcher = daemon.client("watcher")
    watcher.wait_for(is_type("control"))
    watcher.send("work in front of someone")
    watcher.wait_for(is_type("turn_end"))
    watcher.close()
    time.sleep(0.2)

    assert daemon.client("later").wait_for(is_type(wire.ATTACHED))["unseen_work"] is False


def test_a_reconnect_that_continues_from_an_offset_never_claims_missed_work(core):
    daemon = core()
    daemon.daemon.inbox.put("work while nobody watches")
    wait_for_a_turn_to_end(daemon)

    resumed = daemon.client("returning", start=0, segment=daemon.log.segment)

    assert resumed.wait_for(is_type(wire.ATTACHED))["unseen_work"] is False
