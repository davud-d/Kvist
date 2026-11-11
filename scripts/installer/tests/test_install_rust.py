# -*- coding: utf-8 -*-
"""The Rust installer (for Kvist Glass and the voice screen) and their builds inside the CLI installer."""
from __future__ import annotations

import importlib
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

rust = importlib.import_module("install_rust")
cli = importlib.import_module("install_cli")


def _only_tools(monkeypatch, *names):
    monkeypatch.setattr(rust.shutil, "which", lambda name: f"/usr/bin/{name}" if name in names else None)


@pytest.mark.parametrize(("windows", "name"), [(False, "cargo"), (True, "cargo.exe")])
def test_cargo_is_found_where_rustup_puts_it_before_it_is_on_path(monkeypatch, tmp_path, windows, name):
    _only_tools(monkeypatch)
    monkeypatch.setattr(rust, "IS_WINDOWS", windows)
    cargo = tmp_path / "bin" / name
    cargo.parent.mkdir()
    cargo.write_text("")
    cargo.chmod(0o755)
    monkeypatch.setenv("CARGO_HOME", str(tmp_path))
    assert rust.find_cargo() == str(cargo)
    monkeypatch.setenv("CARGO_HOME", str(tmp_path / "empty"))
    assert rust.find_cargo() is None


@pytest.mark.parametrize(
    ("manager", "expected"),
    [
        ("apt-get", [["apt-get", "update"], ["apt-get", "install", "-y", "build-essential"]]),
        ("dnf", [["dnf", "install", "-y", "gcc"]]),
        ("pacman", [["pacman", "-S", "--needed", "--noconfirm", "gcc"]]),
    ],
)
def test_the_compiler_comes_from_the_systems_package_manager(monkeypatch, manager, expected):
    _only_tools(monkeypatch, manager)
    assert rust.compiler_install_commands() == expected


def test_without_a_package_manager_there_is_no_command(monkeypatch):
    _only_tools(monkeypatch)
    assert rust.compiler_install_commands() is None


# os.geteuid exists on POSIX only; the sudo decision itself is plain logic and is checked everywhere.
def test_a_password_for_sudo_stops_with_the_commands_to_run(monkeypatch):
    monkeypatch.setattr(rust.os, "geteuid", lambda: 1000, raising=False)
    _only_tools(monkeypatch, "sudo")
    monkeypatch.setattr(rust, "probe_command", lambda command: subprocess.CompletedProcess(command, 1))
    with pytest.raises(SystemExit, match="sudo dnf install -y gcc"):
        rust._as_root([["dnf", "install", "-y", "gcc"]])


def test_passwordless_sudo_runs_the_commands_as_root(monkeypatch):
    monkeypatch.setattr(rust.os, "geteuid", lambda: 1000, raising=False)
    _only_tools(monkeypatch, "sudo")
    monkeypatch.setattr(rust, "probe_command", lambda command: subprocess.CompletedProcess(command, 0))
    assert rust._as_root([["dnf", "install", "-y", "gcc"]]) == [["sudo", "-n", "dnf", "install", "-y", "gcc"]]


def test_an_installed_rust_is_left_alone(monkeypatch):
    monkeypatch.setattr(rust, "find_cargo", lambda: "/home/u/.cargo/bin/cargo")
    monkeypatch.setattr(rust.urllib.request, "urlopen", lambda *a, **k: pytest.fail("must not download"))
    assert rust.install_rust() == "/home/u/.cargo/bin/cargo"


def test_the_cli_build_includes_glass_when_cargo_is_there(monkeypatch, tmp_path):
    built = tmp_path / "kvist-glass"
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "GLASS_BINARY", built)
    monkeypatch.setattr(cli, "find_cargo", lambda: "/usr/bin/cargo")
    commands = []

    def build(command, cwd=None):
        commands.append((command, cwd))
        built.write_text("")

    monkeypatch.setattr(cli, "run_logged", build)
    assert cli.build_glass() is True
    assert commands == [(["/usr/bin/cargo", "build", "--release", "--locked"], cli.GLASS_DIR)]


def test_the_cli_build_goes_on_without_rust_and_says_so(monkeypatch, tmp_path):
    steps = []
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "GLASS_BINARY", tmp_path / "missing")
    monkeypatch.setattr(cli, "find_cargo", lambda: None)
    monkeypatch.setattr(cli, "show_step", lambda status, text: steps.append(text))
    assert cli.build_glass() is False
    assert steps == [cli.STEPS["glass_no_cargo"]]


def test_windows_builds_no_glass(monkeypatch):
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "find_cargo", lambda: pytest.fail("must not look for cargo"))
    assert cli.build_glass() is False


def test_the_cli_build_includes_the_voice_screen_when_cargo_is_there(monkeypatch, tmp_path):
    built = tmp_path / "kvist-voice-screen"
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "VOICE_SCREEN_BINARY", built)
    monkeypatch.setattr(cli, "find_cargo", lambda: "/usr/bin/cargo")
    commands = []

    def build(command, cwd=None):
        commands.append((command, cwd))
        built.write_text("")

    monkeypatch.setattr(cli, "run_logged", build)
    assert cli.build_voice_screen() is True
    assert commands == [(["/usr/bin/cargo", "build", "--release", "--locked"], cli.VOICE_SCREEN_DIR)]


def test_a_failed_voice_screen_build_is_reported_and_does_not_stop_the_install(monkeypatch, tmp_path):
    steps = []
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "VOICE_SCREEN_BINARY", tmp_path / "missing")
    monkeypatch.setattr(cli, "find_cargo", lambda: "/usr/bin/cargo")
    monkeypatch.setattr(cli, "show_step", lambda status, text: steps.append(text))

    def fail(command, cwd=None):
        raise cli.InstallCommandError("cargo failed")

    monkeypatch.setattr(cli, "run_logged", fail)
    assert cli.build_voice_screen() is False
    assert steps == [cli.STEPS["build_voice"], cli.STEPS["voice_failed"]]


def test_the_cli_build_goes_on_without_rust_for_the_voice_screen_too(monkeypatch, tmp_path):
    steps = []
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "VOICE_SCREEN_BINARY", tmp_path / "missing")
    monkeypatch.setattr(cli, "find_cargo", lambda: None)
    monkeypatch.setattr(cli, "show_step", lambda status, text: steps.append(text))
    assert cli.build_voice_screen() is False
    assert steps == [cli.STEPS["voice_no_cargo"]]


def test_windows_builds_no_voice_screen(monkeypatch):
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "find_cargo", lambda: pytest.fail("must not look for cargo"))
    assert cli.build_voice_screen() is False


def test_the_cli_build_includes_the_session_program_when_cargo_is_there(monkeypatch, tmp_path):
    built = tmp_path / "kvist-session"
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "SESSION_PROGRAM_BINARY", built)
    monkeypatch.setattr(cli, "find_cargo", lambda: "/usr/bin/cargo")
    commands = []

    def build(command, cwd=None):
        commands.append((command, cwd))
        built.write_text("")

    monkeypatch.setattr(cli, "run_logged", build)
    assert cli.build_session_program() is True
    assert commands == [(["/usr/bin/cargo", "build", "--release", "--locked"], cli.SESSION_PROGRAM_DIR)]
    assert (cli.SESSION_PROGRAM_DIR / "Cargo.lock").is_file(), "--locked needs the lock file"


def test_the_cli_build_goes_on_without_rust_for_the_session_program_too(monkeypatch, tmp_path):
    steps = []
    monkeypatch.setattr(cli, "IS_WINDOWS", False)
    monkeypatch.setattr(cli, "SESSION_PROGRAM_BINARY", tmp_path / "missing")
    monkeypatch.setattr(cli, "find_cargo", lambda: None)
    monkeypatch.setattr(cli, "show_step", lambda status, text: steps.append(text))
    assert cli.build_session_program() is False
    assert steps == [cli.STEPS["session_no_cargo"]]


def test_the_session_program_is_built_on_windows_too(monkeypatch, tmp_path):
    built = tmp_path / "kvist-session.exe"
    monkeypatch.setattr(cli, "IS_WINDOWS", True)
    monkeypatch.setattr(cli, "SESSION_PROGRAM_BINARY", built)
    monkeypatch.setattr(cli, "find_cargo", lambda: "cargo.exe")
    monkeypatch.setattr(cli, "run_logged", lambda command, cwd=None: built.write_text(""))
    assert cli.build_session_program() is True


def test_the_core_looks_for_the_session_program_where_the_installer_builds_it():
    from kernel.session import native

    assert native._BUILD == cli.SESSION_PROGRAM_BINARY


def test_the_cli_build_compiles_the_opencode_ui_with_bun(monkeypatch, tmp_path):
    packages = tmp_path / "node_modules" / "@opentui"
    built = tmp_path / "dist" / "kvist-opencode"
    monkeypatch.setattr(cli, "OPENCODE_PACKAGES", packages)
    monkeypatch.setattr(cli, "OPENCODE_BINARY", built)
    monkeypatch.setattr(cli, "find_bun", lambda: "/opt/bun")
    commands = []

    def run(command, cwd=None):
        commands.append((command, cwd))
        if command[1] == "install":
            packages.mkdir(parents=True)
        else:
            built.parent.mkdir()
            built.write_text("")

    monkeypatch.setattr(cli, "run_logged", run)
    assert cli.build_opencode() is True
    assert commands == [
        (["/opt/bun", "install", "--frozen-lockfile"], cli.OPENCODE_DIR),
        (["/opt/bun", "build.ts", str(built)], cli.OPENCODE_DIR),
    ]


def test_the_cli_build_goes_on_without_bun_and_says_so(monkeypatch, tmp_path):
    steps = []
    monkeypatch.setattr(cli, "OPENCODE_BINARY", tmp_path / "missing")
    monkeypatch.setattr(cli, "find_bun", lambda: None)
    monkeypatch.setattr(cli, "show_step", lambda status, text: steps.append(text))
    assert cli.build_opencode() is False
    assert steps == [cli.STEPS["opencode_no_bun"]]


def test_the_session_program_is_installed_beside_the_core(monkeypatch, tmp_path):
    program = tmp_path / "built" / "kvist-session"
    program.parent.mkdir()
    program.write_bytes(b"new program")
    lib = tmp_path / "lib"
    lib.mkdir()
    assert cli.install_session_program(lib, program) is True
    installed = lib / cli.SESSION_NAME
    assert installed.read_bytes() == b"new program"
    assert not [item for item in lib.iterdir() if item != installed], "no temporary file is left behind"
    program.write_bytes(b"newer program")
    assert cli.install_session_program(lib, program) is True
    assert installed.read_bytes() == b"newer program"


def test_a_session_program_of_an_earlier_install_is_not_kept_without_a_new_one(tmp_path):
    lib = tmp_path / "lib"
    lib.mkdir()
    (lib / cli.SESSION_NAME).write_bytes(b"old program")
    assert cli.install_session_program(lib, tmp_path / "not-built") is False
    assert not (lib / cli.SESSION_NAME).exists()
    assert cli.install_session_program(lib, tmp_path / "not-built") is False
