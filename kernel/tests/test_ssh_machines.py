# -*- coding: utf-8 -*-
"""The saved machines, which of them a launch uses, and the sessions remembered on them."""
from __future__ import annotations

import json
from argparse import Namespace

import pytest

from kernel import main
from kernel.ssh import entry, machines
from kernel.ssh.machines import Machine, SessionRecord
from kernel.ssh.paths import RemoteError
from kernel.ssh.tracker import SessionTracker


def args(**fields) -> Namespace:
    base = {
        "machine": None, "local": False, "remote": None, "remote_workspace": None, "remote_key": None,
        "open_session": None, "frontend": "tui",
    }
    return Namespace(**{**base, **fields})


@pytest.fixture(autouse=True)
def at_a_terminal(monkeypatch):
    monkeypatch.setattr(entry, "_has_terminal", lambda: True)


WORK = Machine("work", "me@10.0.0.5", 2222, "/keys/work", "/srv/app", ("/srv/app", "/srv/other"))
HOME = Machine("home", "pi")


def test_nothing_is_created_by_looking(tmp_path):
    assert machines.list_machines() == []
    assert machines.main_machine() is None
    assert not machines.machines_path().exists()


def test_a_machine_is_saved_and_comes_back_as_it_was():
    machines.save_machine(WORK)

    assert machines.get_machine("work") == WORK
    assert [machine.name for machine in machines.list_machines()] == ["work"]
    assert (machines.machines_path().stat().st_mode & 0o777) == 0o600


def test_only_the_known_fields_are_ever_written_and_never_a_password():
    machines.save_machine(WORK)
    machines.record_session("work", "/srv/app", "chat-1", "Fix it")

    stored = json.loads(machines.machines_path().read_text())

    assert set(stored["machines"]["work"]) == {"host", "port", "key", "folder", "folders"}
    assert set(stored) == {"machines", "sessions"}
    assert "password" not in machines.machines_path().read_text().lower()


@pytest.mark.parametrize("name", ["", "Work", "a b", "-x", "x" * 33, "../x"])
def test_a_name_that_could_be_a_path_or_a_flag_is_refused(name):
    with pytest.raises(RemoteError):
        machines.save_machine(Machine(name, "me@box"))


def test_the_main_machine_is_one_of_them_and_deleting_it_clears_the_choice():
    machines.save_machine(WORK)
    machines.save_machine(HOME)

    machines.set_main("work")
    assert machines.main_machine() == WORK
    machines.delete_machine("work")

    assert machines.main_machine() is None
    assert [machine.name for machine in machines.list_machines()] == ["home"]


def test_a_saved_file_with_nonsense_in_it_is_read_as_far_as_it_makes_sense():
    machines.save_machine(WORK)
    path = machines.machines_path()
    data = json.loads(path.read_text())
    data["machines"]["broken"] = {"host": 5}
    data["machines"]["alsobroken"] = "text"
    data["machines"]["work"]["port"] = "22"
    path.write_text(json.dumps(data))

    found = machines.list_machines()

    assert [machine.name for machine in found] == ["work"]
    assert found[0].port is None


def test_folders_worked_in_come_first_and_are_not_repeated_or_endless():
    machines.save_machine(Machine("work", "me@box"))
    for number in range(15):
        machines.remember_folder("work", f"/srv/p{number}")
    machines.remember_folder("work", "/srv/p3")

    folders = machines.get_machine("work").folders

    assert folders[0] == "/srv/p3"
    assert len(folders) == machines.MAX_FOLDERS
    assert len(set(folders)) == len(folders)


def test_the_standard_folder_stands_in_when_none_was_chosen():
    assert Machine("a", "me@box").work_folder == machines.STANDARD_FOLDER
    assert Machine("a", "me@box", folder="/x").work_folder == "/x"


def test_sessions_are_listed_newest_first_once_each_and_keep_their_title():
    machines.save_machine(WORK)
    machines.record_session("work", "/srv/app", "chat-1", "First")
    machines.record_session("work", "/srv/app", "chat-2", "Second")
    machines.record_session("work", "/srv/app", "chat-1")

    records = machines.recent_sessions()

    assert [(record.session, record.title) for record in records] == [("chat-1", "First"), ("chat-2", "Second")]
    assert isinstance(records[0], SessionRecord)


def test_the_list_of_sessions_does_not_grow_without_end():
    for number in range(machines.MAX_RECENT_SESSIONS + 20):
        machines.record_session("work", "/srv/app", f"chat-{number}", "t")

    assert len(machines.recent_sessions()) == machines.MAX_RECENT_SESSIONS


def test_deleting_a_machine_forgets_its_sessions():
    machines.save_machine(WORK)
    machines.record_session("work", "/srv/app", "chat-1", "t")
    machines.record_session("other", "/x", "chat-9", "t")

    machines.delete_machine("work")

    assert [record.machine for record in machines.recent_sessions()] == ["other"]


# --- which machine a launch uses ---------------------------------------------------------------------


def test_a_named_machine_becomes_the_remote_flags():
    machines.save_machine(WORK)
    given = args(machine="work")

    entry.resolve_machine(given)

    assert (given.remote, given.remote_key, given.remote_workspace) == ("me@10.0.0.5:2222", "/keys/work", "/srv/app")


def test_a_folder_given_on_the_command_line_beats_the_machines_own():
    machines.save_machine(WORK)
    given = args(machine="work", remote_workspace="/tmp/elsewhere")

    entry.resolve_machine(given)

    assert given.remote_workspace == "/tmp/elsewhere"


def test_a_machine_that_is_not_saved_is_an_error_that_says_how_to_see_them():
    with pytest.raises(RemoteError, match="--machines"):
        entry.resolve_machine(args(machine="nope"))


def test_starting_a_frontend_goes_to_the_main_machine_and_local_overrides_it():
    machines.save_machine(WORK)
    machines.set_main("work")
    plain = args()
    forced_local = args(local=True)

    entry.resolve_machine(plain)
    entry.resolve_machine(forced_local)

    assert plain.remote == "me@10.0.0.5:2222"
    assert forced_local.remote is None and forced_local.machine is None


def test_the_main_machine_is_not_used_for_anything_but_starting_a_frontend(monkeypatch):
    machines.save_machine(WORK)
    machines.set_main("work")
    monkeypatch.setattr("kernel.config.user_config.get_saved_frontend", lambda: "terminal")
    for given in (args(session_op="list"), args(config_op="get-config"), args(frontend="stdio-multi"), args(frontend=None)):
        entry.resolve_machine(given)
        assert given.remote is None


def test_an_address_given_by_hand_is_left_alone_even_with_a_main_machine():
    machines.save_machine(WORK)
    machines.set_main("work")
    given = args(remote="me@elsewhere", remote_workspace="/x")

    entry.resolve_machine(given)

    assert given.remote == "me@elsewhere"


def test_the_core_command_for_a_saved_machine_names_it_so_the_proxy_can_record_its_sessions():
    machines.save_machine(WORK)
    given = args(machine="work")
    entry.resolve_machine(given)
    given.open_session = "chat-7"

    command = main._core_command(given)

    assert command[-6:] == ["--machine", "work", "--remote-workspace", "/srv/app", "--open-session", "chat-7"]
    assert "--remote-key" not in command


def test_the_core_command_for_an_address_given_by_hand_carries_the_key():
    given = args(remote="me@box:22", remote_workspace="/x", remote_key="/k")

    command = main._core_command(given)

    assert command[-6:] == ["--remote", "me@box:22", "--remote-workspace", "/x", "--remote-key", "/k"]


# --- sessions learned from the events that pass ---------------------------------------------------------


class Book:
    def __init__(self) -> None:
        self.records: list[tuple] = []

    def __call__(self, *record) -> None:
        self.records.append(record)


def feed(tracker: SessionTracker, *events: dict) -> None:
    for event in events:
        tracker.see(event["type"], json.dumps(event).encode())


def test_a_session_is_noted_when_it_had_a_turn_and_when_it_got_its_title():
    book = Book()
    tracker = SessionTracker("work", "/srv/app", record=book)

    feed(tracker, {"type": "hello", "session": "chat-1"}, {"type": "turn_end"}, {"type": "session_title", "session": "chat-1", "title": "Fix it"})

    assert book.records == [("work", "/srv/app", "chat-1", ""), ("work", "/srv/app", "chat-1", "Fix it")]


def test_a_title_that_lands_after_leaving_the_session_is_not_taken_for_a_switch():
    book = Book()
    tracker = SessionTracker("work", "/srv/app", record=book)

    feed(tracker, {"type": "hello", "session": "chat-2"}, {"type": "session_title", "session": "chat-1", "title": "Old"})

    assert tracker.session == "chat-2" and book.records == []


def test_switching_sessions_forgets_the_title_of_the_one_left():
    tracker = SessionTracker("work", "/srv/app", record=Book())

    feed(tracker, {"type": "session", "session": "a", "title": "Alpha"}, {"type": "session", "session": "b"})

    assert (tracker.session, tracker.title) == ("b", "")


def test_nothing_is_noted_for_a_core_that_is_not_a_saved_machine():
    book = Book()
    tracker = SessionTracker(None, "/x", record=book)

    feed(tracker, {"type": "hello", "session": "chat-1"}, {"type": "turn_end"})

    assert book.records == []


def test_a_machines_file_that_cannot_be_written_is_said_once_and_does_not_stop_the_proxy():
    said: list[str] = []

    def refuse(*record):
        raise OSError("disk full")

    tracker = SessionTracker("work", "/x", record=refuse, report=said.append)

    feed(tracker, {"type": "hello", "session": "chat-1"}, {"type": "turn_end"})

    assert said and "disk full" in said[0]


def test_without_a_terminal_the_main_machine_is_not_used(monkeypatch):
    machines.save_machine(WORK)
    machines.set_main("work")
    monkeypatch.setattr(entry, "_has_terminal", lambda: False)
    given = args()

    entry.resolve_machine(given)

    assert given.remote is None


# --- the launcher reading what a frontend asked for -------------------------------------------------------


def test_a_frontend_asking_to_open_a_saved_machine_and_session_is_followed():
    machines.save_machine(WORK)
    given = args()

    main._use_remote_choice("@machine\twork\t/srv/other\tchat-3", given)

    assert (given.machine, given.remote, given.remote_key) == ("work", "me@10.0.0.5:2222", "/keys/work")
    assert (given.remote_workspace, given.open_session) == ("/srv/other", "chat-3")


def test_a_frontend_asking_to_come_back_or_to_open_an_address_is_followed():
    back = args(machine="work", remote="me@box", remote_workspace="/x")
    by_hand = args()

    main._use_remote_choice("@local", back)
    main._use_remote_choice("me@h:2222\t/srv/my app", by_hand)

    assert back.remote is None and back.local is True
    assert (by_hand.remote, by_hand.remote_workspace, by_hand.machine) == ("me@h:2222", "/srv/my app", None)


def test_a_machine_that_was_forgotten_meanwhile_leaves_the_launch_on_this_computer(capsys):
    given = args()

    main._use_remote_choice("@machine\tgone\t/x\t", given)

    assert given.remote is None
    assert "gone" in capsys.readouterr().err
