# -*- coding: utf-8 -*-
"""``/machines`` and ``/remote`` as the core runs them: dialogs, then a request to start the frontend again elsewhere."""
from __future__ import annotations

import json

from kernel.ssh import commands, machines
from kernel.ssh.machines import Machine
from kernel.tests.test_ssh_wizard import ScriptUI, ssh_world  # noqa: F401  (the fixture that fakes ssh)

WORK = Machine("work", "me@box", 2200, "/keys/work", "/srv/work", ("/srv/old",))


def switch_of(outcome) -> str:
    return outcome.effects["relaunch"]["switch"]


def test_choosing_this_computer_asks_the_frontend_to_start_again_here():
    machines.save_machine(WORK)

    outcome = commands.machines_command(ScriptUI("@local"))

    assert outcome.ok and switch_of(outcome) == "@local"
    assert outcome.effects["relaunch"]["machine"] is None


def test_opening_a_machine_names_machine_folder_and_session_and_leaves_the_checking_to_the_interface_that_opens():
    machines.save_machine(WORK)
    machines.record_session("work", "/srv/old", "chat-7", "Fix it")

    outcome = commands.machines_command(ScriptUI("@recent", "0"))

    assert switch_of(outcome) == "@machine\twork\t/srv/old\tchat-7"
    assert outcome.effects["relaunch"]["session"] == "chat-7"
    assert outcome.effects["relaunch"]["host"] == "me@box:2200"


def test_connecting_to_a_machine_asks_which_folder_and_never_the_network():
    machines.save_machine(WORK)

    outcome = commands.machines_command(ScriptUI("work", "connect", "/srv/work"))

    assert switch_of(outcome) == "@machine\twork\t/srv/work\t"


def test_backing_out_of_the_dialog_is_not_an_answer():
    machines.save_machine(WORK)

    assert commands.machines_command(ScriptUI(None)) is None


def test_the_relaunch_is_plain_data_the_frontend_can_read():
    machines.save_machine(WORK)

    outcome = commands.machines_command(ScriptUI("@local"))

    assert json.loads(json.dumps(outcome.effects)) == outcome.effects


def test_remote_off_comes_back_and_a_server_by_hand_is_named_for_the_interface_to_open():
    off = commands.remote_command(ScriptUI(), ["off"])
    by_hand = commands.remote_command(ScriptUI(), ["me@box:2222", "/srv/my", "app"])

    assert switch_of(off) == "@local"
    assert switch_of(by_hand) == "me@box:2222\t/srv/my app"
    assert by_hand.effects["relaunch"]["folder"] == "/srv/my app"


def test_remote_without_a_folder_or_with_an_option_for_a_host_is_refused():
    assert commands.remote_command(ScriptUI(), []).ok is False
    assert commands.remote_command(ScriptUI(), ["me@box"]).ok is False
    refused = commands.remote_command(ScriptUI(), ["-oProxyCommand=x", "/srv"])
    assert refused.ok is False and refused.effects == {}


def test_a_tab_or_a_newline_in_a_folder_cannot_break_the_launchers_line():
    outcome = commands.remote_command(ScriptUI(), ["me@box", "/srv/a\tb\nc"])

    assert "\n" not in switch_of(outcome) and switch_of(outcome).count("\t") == 1
