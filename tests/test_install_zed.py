# -*- coding: utf-8 -*-
"""The Zed installer writes one settings member and nothing else.

These exercise ``installer/install_zed.py``: where it looks for Zed's settings,
what it refuses to invent, that it never competes with ``install_cli.py`` for the
binary, and that the file it leaves behind keeps the user's comments, line
endings and permissions.
"""
from __future__ import annotations

import importlib
import json
import os
import stat
import sys
import threading
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent / "installer"
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

import install_zed
import jsonc_settings
from install_cli import CORE_NAME


@pytest.fixture
def env(monkeypatch, tmp_path):
    """A machine with no Zed settings and no ambient config locations."""
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(install_zed, "IS_WINDOWS", False)
    return tmp_path


@pytest.fixture
def lib_dir(tmp_path):
    """A directory that already holds the standalone core binary."""
    directory = tmp_path / "lib"
    directory.mkdir()
    (directory / CORE_NAME).write_text("#!/bin/sh\n", encoding="utf-8")
    return directory


def entry_of(text: str, name: str = "Kvist") -> dict:
    root = jsonc_settings._root_object(text)
    servers = jsonc_settings._find_member(text, *root, "agent_servers")
    member = jsonc_settings._find_member(text, servers.value_start, servers.value_end - 1, name)
    return json.loads(text[member.value_start : member.value_end])


# --- Where the settings file lives -------------------------------------------


def test_an_explicit_settings_path_wins_over_every_candidate(env):
    settings = env / "elsewhere" / "settings.json"
    (env / ".config" / "zed").mkdir(parents=True)
    (env / ".config" / "zed" / "settings.json").write_text("{}", encoding="utf-8")

    assert install_zed.discover_settings_path(settings) == settings


def test_an_existing_candidate_is_used(env, monkeypatch):
    xdg = env / "xdg"
    existing = xdg / "zed" / "settings.json"
    existing.parent.mkdir(parents=True)
    existing.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert install_zed.discover_settings_path(None) == existing


def test_without_any_candidate_the_documented_location_is_created(env):
    path = install_zed.discover_settings_path(None)

    assert path == env / ".config" / "zed" / "settings.json"


def test_xdg_config_home_beats_the_default_when_nothing_exists_yet(env, monkeypatch):
    xdg = env / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    assert install_zed.discover_settings_path(None) == xdg / "zed" / "settings.json"


def test_several_existing_candidates_warn_and_take_the_first(env, monkeypatch):
    xdg = env / "xdg"
    first = xdg / "zed" / "settings.json"
    first.parent.mkdir(parents=True)
    first.write_text("{}", encoding="utf-8")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    second = env / ".config" / "zed" / "settings.json"
    second.parent.mkdir(parents=True)
    second.write_text("{}", encoding="utf-8")
    warnings: list[str] = []
    monkeypatch.setattr(install_zed, "warn", warnings.append)

    assert install_zed.discover_settings_path(None) == first
    assert len(warnings) == 1
    assert "more than one place" in warnings[0]


def test_windows_without_a_settings_file_reports_the_candidates_instead_of_inventing_one(
    env, monkeypatch
):
    monkeypatch.setattr(install_zed, "IS_WINDOWS", True)
    monkeypatch.setenv("APPDATA", str(env / "AppData" / "Roaming"))

    with pytest.raises(SystemExit) as exit_info:
        install_zed.discover_settings_path(None)

    message = str(exit_info.value)
    assert "does not document where Windows keeps it" in message
    assert "--settings" in message
    assert "AppData" in message.replace("\\", "/")


# --- The binary is install_cli's job, never this installer's ------------------


def test_a_missing_binary_is_refused_with_a_pointer_at_the_cli_installer(env):
    lib = env / "empty"
    lib.mkdir()

    with pytest.raises(SystemExit) as exit_info:
        install_zed.resolve_command(lib)

    message = str(exit_info.value)
    assert str(lib / CORE_NAME) in message
    assert "install_cli.py first" in message
    assert "never builds or copies" in message


def test_the_entry_launches_the_standalone_core_with_acp(lib_dir):
    entry = json.loads(install_zed.agent_entry(install_zed.resolve_command(lib_dir)))

    assert entry == {
        "type": "custom",
        "command": str(lib_dir / CORE_NAME),
        "args": ["--acp"],
        "env": {"XDG_CONFIG_HOME": str(lib_dir / install_zed.PRISTINE_CONFIG_DIR_NAME)},
    }
    assert "kvist" != Path(entry["command"]).name  # not the --workspace wrapper


# --- Zed wraps the command in the user's login shell --------------------------


def test_the_entry_points_the_shell_at_a_config_that_is_not_the_users(lib_dir):
    """Zed runs the entry as ``$SHELL -c '<command> <args>'``, and that shell's
    own startup writes to the protocol's stdout. The only lever is the
    ``XDG_CONFIG_HOME`` Zed hands the shell, so the entry must set it to a
    directory that holds none of the user's startup files.
    """
    entry = json.loads(install_zed.agent_entry(install_zed.resolve_command(lib_dir)))

    home = Path.home()
    xdg_home = Path(entry["env"]["XDG_CONFIG_HOME"])
    assert xdg_home != home / ".config"
    assert xdg_home != Path(os.environ.get("XDG_CONFIG_HOME", home / ".config"))
    # Next to the binary: one launch owns one config, and it cannot be far away.
    assert xdg_home.parent == lib_dir


def test_install_creates_the_config_directory_the_entry_names(env, lib_dir):
    settings = env / "settings.json"
    settings.write_text("{}\n", encoding="utf-8")

    install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))

    entry = entry_of(settings.read_text(encoding="utf-8"))
    assert Path(entry["env"]["XDG_CONFIG_HOME"]).is_dir()


# --- Installing --------------------------------------------------------------


def test_install_registers_the_agent_and_keeps_the_users_settings(env, lib_dir):
    settings = env / ".config" / "zed" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text('{\n  "theme": "one" // keep me\n}\n', encoding="utf-8")

    assert install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME)) is True

    text = settings.read_text(encoding="utf-8")
    assert entry_of(text)["command"] == str(lib_dir / CORE_NAME)
    assert '"theme": "one"' in text
    assert "// keep me" in text


def test_the_first_change_is_backed_up_once(env, lib_dir):
    settings = env / ".config" / "zed" / "settings.json"
    settings.parent.mkdir(parents=True)
    original = '{\n  "theme": "one"\n}\n'
    settings.write_text(original, encoding="utf-8")

    install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))
    install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))

    backup = settings.with_name("settings.json.kvist-backup")
    assert backup.read_text(encoding="utf-8") == original
    assert backup.read_text(encoding="utf-8") != settings.read_text(encoding="utf-8")


def test_a_second_install_is_a_no_op(env, lib_dir):
    settings = env / "settings.json"
    settings.write_text('{\n  "vim_mode": true\n}\n', encoding="utf-8")

    assert install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME)) is True
    once = settings.read_bytes()
    backup = settings.with_name("settings.json.kvist-backup")
    assert backup.is_file()

    assert install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME)) is False
    assert settings.read_bytes() == once


def test_replacing_keeps_the_file_mode(env, lib_dir):
    settings = env / "settings.json"
    settings.write_text('{"a": 1}\n', encoding="utf-8")
    settings.chmod(0o644)

    install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))

    assert stat.S_IMODE(settings.stat().st_mode) == 0o644
    assert not list(settings.parent.glob("settings.json.*.tmp"))


def test_crlf_line_endings_survive_the_edit(env, lib_dir):
    settings = env / "settings.json"
    settings.write_bytes(b'{\r\n  "theme": "one"\r\n}\r\n')

    install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))

    text = settings.read_bytes().decode("utf-8")
    assert "\r\n" in text
    assert entry_of(text)["args"] == ["--acp"]


def test_a_missing_settings_file_is_created_with_the_entry(env, lib_dir):
    settings = env / ".config" / "zed" / "settings.json"

    install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))

    assert entry_of(settings.read_text(encoding="utf-8"))["type"] == "custom"


def test_an_unreadable_document_stops_the_install(env, lib_dir):
    settings = env / "settings.json"
    settings.write_text("{\n  nope\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))

    assert str(settings) in str(exit_info.value)


def test_a_non_utf8_document_stops_the_install(env, lib_dir):
    settings = env / "settings.json"
    settings.write_bytes(b'{"a": "\xff\xfe"}\n')

    with pytest.raises(SystemExit) as exit_info:
        install_zed.install(settings, "Kvist", str(lib_dir / CORE_NAME))

    assert "not UTF-8" in str(exit_info.value)


# --- Removing ----------------------------------------------------------------


def test_uninstall_removes_only_our_entry(env):
    settings = env / "settings.json"
    settings.write_text(
        '{\n  "agent_servers": {\n'
        '    "Other": {"command": "x"},\n'
        '    "Kvist": {"command": "old"}\n  }\n}\n',
        encoding="utf-8",
    )

    assert install_zed.uninstall(settings, "Kvist") is True

    text = settings.read_text(encoding="utf-8")
    assert jsonc_settings.has_agent_server(text, "Kvist") is False
    assert jsonc_settings.has_agent_server(text, "Other") is True


def test_uninstall_is_a_no_op_when_the_entry_is_absent(env):
    settings = env / "settings.json"
    settings.write_text('{\n  "vim_mode": true\n}\n', encoding="utf-8")

    assert install_zed.uninstall(settings, "Kvist") is False
    assert settings.read_text(encoding="utf-8") == '{\n  "vim_mode": true\n}\n'


def test_uninstall_of_a_missing_file_is_a_no_op(env):
    assert install_zed.uninstall(env / "absent.json", "Kvist") is False
    assert not (env / "absent.json").exists()


# --- End to end through main() -----------------------------------------------


def test_main_installs_and_reports_the_settings_path(env, lib_dir, monkeypatch, capsys):
    monkeypatch.setattr(
        sys,
        "argv",
        ["install_zed.py", "--settings", str(env / "s.json"), "--lib-dir", str(lib_dir)],
    )

    install_zed.main()

    out = capsys.readouterr().out
    assert "registered in" in out
    assert "no restart needed" in out
    assert entry_of((env / "s.json").read_text(encoding="utf-8"))["type"] == "custom"


def test_main_uninstall_is_surgical(env, lib_dir, monkeypatch, capsys):
    settings = env / "s.json"
    monkeypatch.setattr(
        sys,
        "argv",
        ["install_zed.py", "--settings", str(settings), "--lib-dir", str(lib_dir)],
    )
    install_zed.main()
    monkeypatch.setattr(
        sys,
        "argv",
        ["install_zed.py", "--settings", str(settings), "--uninstall"],
    )

    install_zed.main()

    assert jsonc_settings.has_agent_server(settings.read_text(encoding="utf-8"), "Kvist") is False
    assert "Removed" in capsys.readouterr().out


def test_main_refuses_when_the_cli_is_not_installed_yet(env, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["install_zed.py", "--settings", str(env / "s.json"), "--lib-dir", str(env / "nowhere")],
    )

    with pytest.raises(SystemExit) as exit_info:
        install_zed.main()

    assert "install_cli.py first" in str(exit_info.value)
    assert not (env / "s.json").exists()


# --- The front door runs install_cli.py first ---------------------------------


def test_the_zed_component_is_listed_after_the_cli_it_depends_on():
    front = importlib.import_module("install")
    keys = [component.key for component in front.COMPONENTS]
    zed = next(component for component in front.COMPONENTS if component.key == "zed")

    assert "cli" in zed.after
    assert keys.index("cli") < keys.index("zed")


def test_a_component_waits_for_its_dependency_before_it_starts():
    front = importlib.import_module("install")
    zed = next(component for component in front.COMPONENTS if component.key == "zed")
    progresses = {"cli": front.Progress(), "zed": front.Progress()}
    started = threading.Event()

    def run() -> None:
        front._await_dependencies(zed, progresses)
        started.set()

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert started.wait(0.3) is False  # the CLI installer is still running
        progresses["cli"].state = "done"
        worker.join(2.0)
        assert started.is_set()
    finally:
        worker.join(2.0)


def test_a_dependency_that_was_not_chosen_does_not_block():
    front = importlib.import_module("install")
    zed = next(component for component in front.COMPONENTS if component.key == "zed")

    # Only zed was chosen, so nothing else is going to install the binary; zed
    # runs and reports the missing one itself.
    assert front._await_dependencies(zed, {"zed": front.Progress()}) is None
