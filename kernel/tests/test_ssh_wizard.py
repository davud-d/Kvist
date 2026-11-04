# -*- coding: utf-8 -*-
"""The machines dialog, driven by a script instead of a person."""
from __future__ import annotations

import json

import pytest

from kernel.ssh import access, machines, machines_wizard as wizard
from kernel.ssh.access import Endpoint, Login, ScannedKey
from kernel.ssh.machines import Machine
from kernel.ssh.paths import RemoteError

ACCEPT = object()  # take what the dialog already filled in


class ScriptUI:
    """Answers the dialog's questions in order, and checks that each answer was on offer."""

    def __init__(self, *answers) -> None:
        self.answers = list(answers)
        self.errors: list[str] = []
        self.shown: list[tuple[str, list[str]]] = []

    def choose_option(self, title, options, current_value="", help_text=""):
        answer = self.answers.pop(0)
        values = [option["value"] for option in options]
        self.shown.append((title, values))
        assert answer is None or answer in values, f"{answer!r} is not offered in {title!r}: {values}"
        return answer

    def prompt_text(self, title, current_value="", help_text=""):
        answer = self.answers.pop(0)
        return current_value if answer is ACCEPT else answer

    def prompt_secret(self, title, current_value="", help_text=""):
        return self.answers.pop(0)

    def print_error(self, message):
        self.errors.append(message)


@pytest.fixture(autouse=True)
def ssh_world(monkeypatch):
    """No real ssh: the host key is unknown until trusted, every login works unless a test says otherwise."""
    state = {"known": False, "login": Login(True, "", ""), "installed": [], "trusted": []}
    monkeypatch.setattr(access, "tools_missing", lambda: [])
    monkeypatch.setattr(access, "resolve_endpoint", lambda host, port, run=None: Endpoint(host.rsplit("@", 1)[-1], port or 22))
    monkeypatch.setattr(access, "host_is_known", lambda endpoint, run=None: state["known"])
    monkeypatch.setattr(access, "scan_host_key", lambda endpoint, run=None: ScannedKey(("line",), "SHA256:fp", "ssh-ed25519"))
    monkeypatch.setattr(access, "trust_host_key", lambda scanned: state["trusted"].append(scanned) or state.update(known=True))
    monkeypatch.setattr(access, "check_login", lambda target, run=None: state["login"])
    monkeypatch.setattr(access, "usable_key_file", lambda path: None if path.endswith("id") else "is not a file")

    def install(name, target, password, run=None):
        state["installed"].append((name, password))
        return f"/keys/{name}"

    monkeypatch.setattr(access, "install_key_with_password", install)
    return state


def test_a_machine_is_added_by_its_address_and_the_first_one_may_become_the_main(ssh_world):
    ui = ScriptUI("me@10.0.0.5", "", ACCEPT, "as_is", "yes", "", "yes")

    machine = wizard.add_or_edit_machine(ui, "en")

    assert machine == Machine("10-0-0-5", "me@10.0.0.5")
    assert machines.get_machine("10-0-0-5") == machine
    assert machines.main_machine() == machine
    assert ssh_world["trusted"]


def test_a_port_in_the_address_is_not_asked_for_again(ssh_world):
    ui = ScriptUI("me@box:2200", "mybox", "as_is", "yes", "/srv/app", "no")

    machine = wizard.add_or_edit_machine(ui, "en")

    assert (machine.host, machine.port, machine.name, machine.folder) == ("me@box", 2200, "mybox", "/srv/app")
    assert machines.main_machine() is None


def test_a_password_is_used_once_for_a_key_and_only_the_key_is_saved(ssh_world):
    ui = ScriptUI("me@box", "", "work", "password", "yes", "hunter2", "", "no")

    machine = wizard.add_or_edit_machine(ui, "en")

    assert ssh_world["installed"] == [("work", "hunter2")]
    assert machine.key == "/keys/work"
    assert "hunter2" not in machines.machines_path().read_text()


def test_a_key_file_that_cannot_be_used_is_asked_for_again(ssh_world):
    ui = ScriptUI("me@box", "", "work", "key", "yes", "/nope", "/home/me/.ssh/id", "", "no")

    machine = wizard.add_or_edit_machine(ui, "en")

    assert machine.key == "/home/me/.ssh/id"
    assert ui.errors == ["is not a file"]


def test_a_host_key_the_person_does_not_trust_stops_everything_and_saves_nothing(ssh_world):
    ui = ScriptUI("me@box", "", "work", "as_is", "no")

    assert wizard.add_or_edit_machine(ui, "en") is None
    assert machines.list_machines() == []
    assert ssh_world["trusted"] == []


def test_a_login_that_fails_can_be_kept_anyway_for_a_machine_that_is_just_down(ssh_world):
    ssh_world["login"] = Login(False, "unreachable", "Connection refused")
    ui = ScriptUI("me@box", "", "work", "as_is", "yes", "save", "", "no")

    machine = wizard.add_or_edit_machine(ui, "en")

    assert machine is not None and machines.get_machine("work") is not None


def test_a_login_that_fails_can_also_be_given_up_on(ssh_world):
    ssh_world["login"] = Login(False, "auth", "Permission denied")
    ui = ScriptUI("me@box", "", "work", "as_is", "yes", "cancel")

    assert wizard.add_or_edit_machine(ui, "en") is None
    assert machines.list_machines() == []


def test_a_name_that_is_taken_or_not_allowed_is_asked_for_again(ssh_world):
    machines.save_machine(Machine("work", "me@old"))
    ui = ScriptUI("me@box", "", "work", "Bad Name", "other", "as_is", "yes", "", "no")

    machine = wizard.add_or_edit_machine(ui, "en")

    assert machine.name == "other"
    assert ui.errors == ["That name is taken or not allowed."] * 2


def test_an_address_ssh_could_take_for_an_option_is_refused(ssh_world):
    ui = ScriptUI("-oProxyCommand=x", None)

    assert wizard.add_or_edit_machine(ui, "en") is None
    assert "not a host name" in ui.errors[0]


def test_without_the_ssh_programs_the_dialog_says_so_instead_of_failing_later(ssh_world, monkeypatch):
    monkeypatch.setattr(access, "tools_missing", lambda: ["ssh-keyscan"])
    ui = ScriptUI()

    assert wizard.add_or_edit_machine(ui, "en") is None
    assert "ssh-keyscan" in ui.errors[0]


# --- the menus -------------------------------------------------------------------------------------


def saved(*names: str) -> None:
    for name in names:
        machines.save_machine(Machine(name, f"me@{name}", folder=f"/srv/{name}", folders=("/srv/old",)))


def test_connecting_asks_which_folder_and_offers_the_machines_own_first():
    saved("work")
    ui = ScriptUI("work", "connect", "/srv/old")

    choice = wizard.run_menu(ui, "en")

    assert choice == wizard.Choice(machines.get_machine("work"), "/srv/old")
    folder_options = ui.shown[-1][1]
    assert folder_options[0] == "/srv/work" and "/srv/old" in folder_options


def test_another_folder_can_be_typed():
    saved("work")
    ui = ScriptUI("work", "connect", "@other", "/somewhere/else")

    assert wizard.run_menu(ui, "en").folder == "/somewhere/else"


def test_this_computer_is_a_choice_too():
    saved("work")

    assert wizard.run_menu(ScriptUI("@local"), "en") == wizard.Choice(None)


def test_choosing_this_computer_is_remembered_so_the_next_start_does_not_go_back_to_the_server():
    saved("work")
    machines.set_main("work")

    wizard.run_menu(ScriptUI("@local"), "en")

    assert machines.main_machine() is None


def test_leaving_the_dialog_changes_nothing():
    saved("work")

    assert wizard.run_menu(ScriptUI(None), "en") is None


def test_the_main_machine_is_marked_and_can_be_changed_and_cleared():
    saved("work", "home")
    ui = ScriptUI("work", "main", "home", "main", "home", "unmain", None)

    wizard.run_menu(ui, "en")

    assert machines.main_machine() is None
    marked = [option for title, values in ui.shown for option in values]
    assert "work" in marked


def test_the_folder_of_a_machine_can_be_changed_without_connecting():
    saved("work")
    ui = ScriptUI("work", "folder", "/new/place", None)

    wizard.run_menu(ui, "en")

    assert machines.get_machine("work").folder == "/new/place"


def test_a_machine_is_deleted_only_after_the_person_says_yes():
    saved("work", "home")
    ui = ScriptUI("work", "delete", "no", "home", "delete", "yes", None)

    wizard.run_menu(ui, "en")

    assert [machine.name for machine in machines.list_machines()] == ["work"]


def test_recent_sessions_from_every_machine_are_listed_and_open_where_they_live():
    saved("work", "home")
    machines.record_session("work", "/srv/work", "chat-1", "Fix the build")
    machines.record_session("home", "/srv/home", "chat-2", "Garden notes")
    ui = ScriptUI("@recent", "1")

    choice = wizard.run_menu(ui, "en", now=machines.recent_sessions()[0].updated + 90)

    assert (choice.machine.name, choice.folder, choice.session) == ("work", "/srv/work", "chat-1")
    listed = ui.shown[-1][1]
    assert listed == ["0", "1"]


def test_sessions_of_a_machine_that_was_forgotten_are_not_offered():
    saved("work")
    machines.record_session("gone", "/x", "chat-1", "t")
    ui = ScriptUI("@recent", None)

    assert wizard.run_menu(ui, "en") is None
    assert "No sessions" in ui.errors[0]


def test_a_new_machine_goes_straight_on_to_work_in_the_folder_just_given(ssh_world):
    ui = ScriptUI("@add", "me@box", "", "mybox", "as_is", "yes", "/srv/standard", "no")
    machines.save_machine(Machine("existing", "me@x"))

    choice = wizard.run_menu(ui, "en")

    assert choice.machine.name == "mybox" and choice.folder == "/srv/standard"


def test_the_dialog_speaks_russian_when_asked_to():
    saved("work")
    ui = ScriptUI(None)

    wizard.run_menu(ui, "ru")

    assert ui.shown[0][0] == "Машины"


@pytest.mark.parametrize("language", ["en", "ru"])
def test_every_text_of_the_dialog_exists_in_both_languages(language):
    assert all(language in variants for variants in wizard._TEXT.values())
    json.dumps(wizard._TEXT)
