# -*- coding: utf-8 -*-
"""The macOS branches of the installers, run on any host by faking the platform."""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

install = importlib.import_module("install_cli")
gui = importlib.import_module("install_gui")
jetbrains = importlib.import_module("install_jetbrains")
vscode = importlib.import_module("install_vscode")
common = importlib.import_module("_common")
zed = importlib.import_module("install_zed")


@pytest.fixture
def mac_cli(monkeypatch):
    """install_cli reloaded as if platform.system() said Darwin."""
    monkeypatch.setattr(install.platform, "system", lambda: "Darwin")
    module = importlib.reload(install)
    yield module
    importlib.reload(install)


# ------------------------------------------------------------------- install_cli


def test_a_mac_uses_the_unix_launcher_and_the_unix_binary_names(mac_cli, tmp_path):
    binary = tmp_path / "kvist"
    binary.write_bytes(b"\xcf\xfa\xed\xfe")

    wrapper = mac_cli.install(binary, tmp_path / "lib", tmp_path / "bin")

    assert mac_cli.IS_MACOS and not mac_cli.IS_WINDOWS
    assert wrapper.name == "kvist"
    assert wrapper.read_text().startswith("#!/usr/bin/env bash")
    assert (tmp_path / "lib" / "kvist-core").read_bytes() == b"\xcf\xfa\xed\xfe"


def test_the_quarantine_mark_is_dropped_from_the_core_on_a_mac(mac_cli, monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(mac_cli, "probe_command", lambda command, **kwargs: calls.append(command))
    binary = tmp_path / "kvist"
    binary.write_bytes(b"core")

    mac_cli.install(binary, tmp_path / "lib", tmp_path / "bin")

    assert len(calls) == 1
    assert calls[0][:3] == ["xattr", "-d", "com.apple.quarantine"]
    assert Path(calls[0][3]).parent == tmp_path / "lib"


def test_nothing_is_run_to_drop_a_quarantine_mark_off_a_mac(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(install, "probe_command", lambda command, **kwargs: calls.append(command))
    monkeypatch.setattr(install, "IS_MACOS", False)

    install.clear_quarantine(tmp_path / "kvist")

    assert calls == []


@pytest.mark.parametrize(
    ("shell", "macos", "expected"),
    [
        ("/bin/zsh", True, "echo 'export PATH=\"/home/me/.local/bin:$PATH\"' >> ~/.zshrc"),
        ("/bin/bash", True, "echo 'export PATH=\"/home/me/.local/bin:$PATH\"' >> ~/.bash_profile"),
        ("/bin/bash", False, "echo 'export PATH=\"/home/me/.local/bin:$PATH\"' >> ~/.bashrc"),
        ("/opt/homebrew/bin/fish", True, "fish_add_path /home/me/.local/bin"),
        ("", True, 'export PATH="/home/me/.local/bin:$PATH"'),
    ],
)
def test_the_path_hint_fits_the_login_shell(monkeypatch, shell, macos, expected):
    monkeypatch.setenv("SHELL", shell)
    monkeypatch.setattr(install, "IS_MACOS", macos)

    assert install.path_hint(Path("/home/me/.local/bin")) == expected


# ------------------------------------------------------------------ install_vscode


def test_vscode_is_looked_for_inside_the_app_bundles_on_a_mac(monkeypatch, tmp_path):
    monkeypatch.setattr(vscode, "IS_MACOS", True)
    monkeypatch.setattr(vscode, "IS_WINDOWS", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    commands = vscode._candidate_commands()
    bundle = Path("Contents") / "Resources" / "app" / "bin"

    assert ("VS Code", f"/Applications/Visual Studio Code.app/{bundle}/code") in commands
    assert ("VSCodium", str(tmp_path / "Applications" / "VSCodium.app" / bundle / "codium")) in commands
    # `code` on PATH is still tried first.
    assert commands[0] == ("VS Code", "code")


def test_a_bundle_launcher_that_exists_is_discovered(monkeypatch, tmp_path):
    launcher = tmp_path / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code"
    launcher.parent.mkdir(parents=True)
    launcher.write_text("#!/bin/sh\n")
    monkeypatch.setattr(vscode, "IS_MACOS", True)
    monkeypatch.setattr(vscode, "IS_WINDOWS", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(vscode.shutil, "which", lambda command: None)

    assert ("VS Code", str(launcher)) in vscode.discover_vscode_editors()


# ---------------------------------------------------------------- install_jetbrains


def test_jetbrains_ides_are_found_under_application_support(monkeypatch, tmp_path):
    root = tmp_path / "Library" / "Application Support" / "JetBrains"
    (root / "PyCharm2025.3").mkdir(parents=True)
    (root / "Toolbox").mkdir()
    monkeypatch.setattr(common, "IS_MACOS", True)
    monkeypatch.setattr(common, "IS_WINDOWS", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    found = common.discover_jetbrains_ides()

    assert [(label, path.name) for label, path in found] == [("PyCharm 2025.3", "PyCharm2025.3")]


def test_the_jetbrains_plugin_goes_into_a_plugins_folder_on_a_mac(monkeypatch, tmp_path):
    monkeypatch.setattr(jetbrains, "IS_LINUX", False)

    assert jetbrains.plugins_dir_for(tmp_path / "PyCharm2025.3") == tmp_path / "PyCharm2025.3" / "plugins"


# ---------------------------------------------------------------------- install_gui


def _fake_app(directory: Path) -> Path:
    """A Kvist.app with a framework-style symlink, which a plain copy would turn into a file."""
    app = directory / "Kvist.app"
    framework = app / "Contents" / "Frameworks" / "Electron.framework" / "Versions"
    (framework / "A").mkdir(parents=True)
    (framework / "A" / "Electron").write_bytes(b"mach-o")
    (framework / "Current").symlink_to("A")
    (app / "Contents" / "MacOS").mkdir(parents=True)
    (app / "Contents" / "MacOS" / "Kvist").write_bytes(b"main")
    return app


@pytest.mark.parametrize(
    ("machine", "folders", "expected"),
    [
        ("arm64", ["mac-arm64", "mac"], "mac-arm64"),
        ("arm64", ["mac"], "mac"),
        ("x86_64", ["mac-arm64", "mac"], "mac"),
        ("x86_64", ["mac-universal"], "mac-universal"),
    ],
)
def test_the_gui_build_for_this_cpu_is_chosen(monkeypatch, tmp_path, machine, folders, expected):
    for folder in folders:
        _fake_app(tmp_path / folder)
    monkeypatch.setattr(gui, "GUI_DIST", tmp_path)
    monkeypatch.setattr(gui.platform, "machine", lambda: machine)

    assert gui._find_macos_payload() == tmp_path / expected / "Kvist.app"


def test_a_missing_gui_build_tells_the_user_how_to_make_one(monkeypatch, tmp_path):
    monkeypatch.setattr(gui, "GUI_DIST", tmp_path)

    with pytest.raises(SystemExit, match="--build"):
        gui._find_macos_payload()


def test_the_app_is_copied_with_its_symlinks_into_applications(monkeypatch, tmp_path):
    payload = _fake_app(tmp_path / "dist" / "mac-arm64")
    monkeypatch.setattr(gui, "INSTALL_ROOT", tmp_path / "Applications" / "Kvist.app")
    monkeypatch.setattr(gui, "_find_macos_payload", lambda: payload)
    commands = []
    monkeypatch.setattr(gui, "probe_command", lambda command, **kwargs: commands.append(command))

    message = gui.install_macos()

    installed = tmp_path / "Applications" / "Kvist.app"
    assert (installed / "Contents" / "MacOS" / "Kvist").read_bytes() == b"main"
    assert (installed / "Contents" / "Frameworks" / "Electron.framework" / "Versions" / "Current").is_symlink()
    assert commands == [["xattr", "-dr", "com.apple.quarantine", str(installed)]]
    assert str(installed) in message
    assert not [p for p in (tmp_path / "Applications").iterdir() if p.name.startswith(".")]


def test_a_failed_app_copy_keeps_the_previous_install(monkeypatch, tmp_path):
    payload = _fake_app(tmp_path / "dist" / "mac")
    previous = tmp_path / "Applications" / "Kvist.app"
    previous.mkdir(parents=True)
    (previous / "old.txt").write_text("keep")
    monkeypatch.setattr(gui, "INSTALL_ROOT", previous)
    monkeypatch.setattr(gui, "_find_macos_payload", lambda: payload)
    monkeypatch.setattr(gui.shutil, "copytree", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        gui.install_macos()

    assert (previous / "old.txt").read_text() == "keep"


def test_uninstall_removes_the_app(monkeypatch, tmp_path, capsys):
    app = _fake_app(tmp_path)
    monkeypatch.setattr(gui, "INSTALL_ROOT", app)

    gui.uninstall_macos()

    assert not app.exists()
    assert "uninstalled" in capsys.readouterr().out


# ----------------------------------------------------------------------- install_zed


def test_zed_settings_are_created_under_dot_config_on_a_mac(monkeypatch, tmp_path):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setattr(zed, "IS_WINDOWS", False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert zed.discover_settings_path(None) == tmp_path / ".config" / "zed" / "settings.json"
