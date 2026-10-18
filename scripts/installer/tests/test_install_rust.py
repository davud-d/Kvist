# -*- coding: utf-8 -*-
"""The Rust installer (for Kvist Glass) and the Glass build inside the CLI installer."""
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


def test_cargo_is_found_where_rustup_puts_it_before_it_is_on_path(monkeypatch, tmp_path):
    _only_tools(monkeypatch)
    cargo = tmp_path / "bin" / "cargo"
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


def test_a_password_for_sudo_stops_with_the_commands_to_run(monkeypatch):
    monkeypatch.setattr(rust.os, "geteuid", lambda: 1000)
    _only_tools(monkeypatch, "sudo")
    monkeypatch.setattr(rust, "probe_command", lambda command: subprocess.CompletedProcess(command, 1))
    with pytest.raises(SystemExit, match="sudo dnf install -y gcc"):
        rust._as_root([["dnf", "install", "-y", "gcc"]])


def test_passwordless_sudo_runs_the_commands_as_root(monkeypatch):
    monkeypatch.setattr(rust.os, "geteuid", lambda: 1000)
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
