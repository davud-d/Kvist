# -*- coding: utf-8 -*-
"""The installer stage that builds the pixel desktop app and puts it into the application menu.

These exercise ``scripts/installer/install_app.py`` on all three platforms by describing the
machine as a ``Layout`` (a scratch home), so the macOS and Windows branches run anywhere. Only
what needs the real system is replaced: cargo, ``pkg-config``, ``powershell`` and ``xattr``.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

import install as front_door
import install_app
from install_app import APP_NAME, Layout


def _layout(tmp_path: Path, system: str) -> Layout:
    home = tmp_path / "home"
    return Layout(
        system=system,
        home=home,
        data_home=home / ".local" / "share",
        local_app_data=home / "AppData" / "Local",
        app_data=home / "AppData" / "Roaming",
        desktop=home / "Desktop",
    )


@pytest.fixture
def built(tmp_path):
    binary = tmp_path / "kvist-tauri"
    binary.write_bytes(b"app-binary")
    return binary


# ------------------------------------------------------------------- the front door


def test_the_app_is_a_component_of_the_front_door_that_waits_for_rust():
    app = next(component for component in front_door.COMPONENTS if component.key == "app")
    assert app.script == "install_app.py" and app.after == ("rust",)
    assert (front_door.INSTALLER_DIR / app.script).is_file()


def test_the_developer_installer_builds_and_never_downloads():
    source = (INSTALLER_DIR / "install_app.py").read_text(encoding="utf-8")
    assert "urllib" not in source and "RELEASE_URL" not in source
    assert "cargo" in source or "find_cargo" in source


# ------------------------------------------------------------------- building


def test_the_build_needs_rust_and_names_the_installer_that_brings_it(monkeypatch):
    monkeypatch.setattr(install_app, "find_cargo", lambda: None)

    with pytest.raises(SystemExit, match="Rust.*install_rust.py"):
        install_app.build_from_source("Darwin")


def test_cargo_builds_a_locked_release_in_the_tauri_folder(tmp_path, monkeypatch):
    seen = []
    (tmp_path / "kvist-tauri").write_bytes(b"built")
    monkeypatch.setattr(install_app, "find_cargo", lambda: "/cargo")
    monkeypatch.setattr(install_app, "BUILT_DIR", tmp_path)
    monkeypatch.setattr(install_app, "run_logged", lambda command, cwd=None: seen.append((command, cwd)))

    built = install_app.build_from_source("Darwin")

    assert seen == [(["/cargo", "build", "--release", "--locked"], install_app.TAURI_DIR)]
    assert built == tmp_path / "kvist-tauri"


def test_windows_builds_the_exe(tmp_path, monkeypatch):
    (tmp_path / "kvist-tauri.exe").write_bytes(b"built")
    monkeypatch.setattr(install_app, "find_cargo", lambda: "cargo.exe")
    monkeypatch.setattr(install_app, "BUILT_DIR", tmp_path)
    monkeypatch.setattr(install_app, "run_logged", lambda *_, **__: None)

    assert install_app.build_from_source("Windows").name == "kvist-tauri.exe"


def test_a_build_that_leaves_no_binary_is_reported(tmp_path, monkeypatch):
    monkeypatch.setattr(install_app, "find_cargo", lambda: "/cargo")
    monkeypatch.setattr(install_app, "BUILT_DIR", tmp_path)
    monkeypatch.setattr(install_app, "run_logged", lambda *_, **__: None)

    with pytest.raises(SystemExit, match="was not produced"):
        install_app.build_from_source("Darwin")


def test_linux_checks_the_web_view_libraries_before_a_long_build(monkeypatch):
    ran = []
    monkeypatch.setattr(install_app, "find_cargo", lambda: "/cargo")
    monkeypatch.setattr(install_app, "probe_command", lambda command: subprocess.CompletedProcess(command, 1, "", ""))
    monkeypatch.setattr(install_app.shutil, "which", lambda name: "/usr/bin/apt-get" if name == "apt-get" else None)
    monkeypatch.setattr(install_app, "run_logged", lambda *a, **k: ran.append(a))

    with pytest.raises(SystemExit) as stopped:
        install_app.build_from_source("Linux")

    assert "libwebkit2gtk-4.1-dev" in str(stopped.value) and "sudo apt-get install" in str(stopped.value)
    assert ran == [], "cargo must not start when the libraries are missing"


def test_linux_with_the_libraries_goes_on_to_cargo(tmp_path, monkeypatch):
    (tmp_path / "kvist-tauri").write_bytes(b"built")
    seen = []
    monkeypatch.setattr(install_app, "find_cargo", lambda: "/cargo")
    monkeypatch.setattr(install_app, "probe_command", lambda command: subprocess.CompletedProcess(command, 0, "", ""))
    monkeypatch.setattr(install_app, "BUILT_DIR", tmp_path)
    monkeypatch.setattr(install_app, "run_logged", lambda command, cwd=None: seen.append(command))

    install_app.build_from_source("Linux")

    assert seen[0][:2] == ["/cargo", "build"]


# ------------------------------------------------------------------- Linux


def test_linux_puts_the_app_in_the_launcher_menu(tmp_path, built):
    layout = _layout(tmp_path, "Linux")

    message = install_app.install(layout, built)

    assert layout.binary == layout.home / ".local/share/kvist/app/kvist-app"
    assert layout.binary.read_bytes() == b"app-binary"
    if os.name == "posix":
        assert layout.binary.stat().st_mode & stat.S_IXUSR
    assert (layout.icon_dir / "kvist-app.png").stat().st_size > 1000, "the checkout's icon is copied"
    entry = layout.desktop_entry.read_text()
    assert entry.startswith("[Desktop Entry]\nType=Application\n")
    assert f"Name={APP_NAME}\n" in entry and "Terminal=false\n" in entry
    assert f'Exec="{layout.binary}"\n' in entry and "Icon=kvist-app\n" in entry
    assert "Categories=Development;" in entry
    assert "[Desktop Action safe]\nName=Safe mode (if the window is empty)\n" in entry
    assert f'Exec="{layout.binary}" --safe\n' in entry
    assert str(layout.binary) in message


def test_linux_menu_entry_lands_where_xdg_data_home_says(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.setattr(install_app.platform, "system", lambda: "Linux")

    layout = install_app.layout_from_environment()

    assert layout.desktop_entry == tmp_path / "xdg" / "applications" / "kvist-app.desktop"
    assert layout.binary == tmp_path / "xdg" / "kvist" / "app" / "kvist-app"


def test_installing_again_replaces_the_binary_and_leaves_no_temporary_file(tmp_path, built):
    layout = _layout(tmp_path, "Linux")
    install_app.install(layout, built)
    built.write_bytes(b"newer")

    install_app.install(layout, built)

    assert layout.binary.read_bytes() == b"newer"
    assert sorted(path.name for path in layout.app_dir.iterdir()) == ["kvist-app"]


def test_a_failing_cache_refresh_does_not_fail_the_install(tmp_path, built, monkeypatch):
    layout = _layout(tmp_path, "Linux")
    monkeypatch.setattr(install_app.shutil, "which", lambda name: f"/usr/bin/{name}")

    def refuse(command, **_):
        raise install_app.InstallCommandError(command, 1, "")

    monkeypatch.setattr(install_app, "run_logged", refuse)

    install_app.install(layout, built)

    assert layout.desktop_entry.is_file()


def test_linux_uninstall_removes_exactly_what_install_made(tmp_path, built):
    layout = _layout(tmp_path, "Linux")
    neighbour = layout.data_home / "kvist" / "kvist-core"
    neighbour.parent.mkdir(parents=True)
    neighbour.write_bytes(b"core")
    install_app.install(layout, built)

    removed = install_app.uninstall(layout)

    assert layout.desktop_entry in removed and layout.app_dir in removed
    assert not layout.app_dir.exists() and not layout.desktop_entry.exists()
    assert neighbour.read_bytes() == b"core", "the agent installed beside it stays"
    assert install_app.uninstall(layout) == []


# ------------------------------------------------------------------- macOS


def test_macos_gets_an_app_bundle_in_applications(tmp_path, built, monkeypatch):
    layout = _layout(tmp_path, "Darwin")
    monkeypatch.setattr(install_app.shutil, "which", lambda name: None)

    message = install_app.install(layout, built)

    bundle = layout.home / "Applications" / "Kvist Pixel.app" / "Contents"
    assert layout.mac_bundle == bundle.parent
    assert (bundle / "MacOS" / "kvist-app").read_bytes() == b"app-binary"
    if os.name == "posix":
        assert (bundle / "MacOS" / "kvist-app").stat().st_mode & stat.S_IXUSR
    assert (bundle / "Resources" / "kvist-app.icns").stat().st_size > 1000
    plist = (bundle / "Info.plist").read_text()
    assert "<key>CFBundleExecutable</key><string>kvist-app</string>" in plist
    assert "<key>CFBundleIconFile</key><string>kvist-app</string>" in plist
    assert "<key>CFBundlePackageType</key><string>APPL</string>" in plist
    assert "Launchpad" in message


def test_macos_clears_the_quarantine_mark_of_the_whole_bundle(tmp_path, built, monkeypatch):
    layout = _layout(tmp_path, "Darwin")
    seen = []
    monkeypatch.setattr(install_app.shutil, "which", lambda name: "/usr/bin/xattr")
    monkeypatch.setattr(install_app, "run_logged", lambda command, **_: seen.append(command))

    install_app.install(layout, built)

    assert seen == [["xattr", "-dr", "com.apple.quarantine", str(layout.mac_bundle)]]


def test_macos_reinstall_replaces_the_old_bundle_whole(tmp_path, built, monkeypatch):
    layout = _layout(tmp_path, "Darwin")
    monkeypatch.setattr(install_app.shutil, "which", lambda name: None)
    install_app.install(layout, built)
    stale = layout.mac_bundle / "Contents" / "stale"
    stale.write_bytes(b"x")

    install_app.install(layout, built)

    assert not stale.exists()
    assert install_app.uninstall(layout) == [layout.mac_bundle]


# ------------------------------------------------------------------- Windows


def test_windows_installs_under_local_app_data_with_two_shortcuts(tmp_path, built, monkeypatch):
    layout = _layout(tmp_path, "Windows")
    scripts = []
    monkeypatch.setattr(install_app, "run_logged", lambda command, **_: scripts.append(command))

    message = install_app.install(layout, built)

    assert layout.binary == layout.local_app_data / "Kvist" / "app" / "kvist-app.exe"
    assert layout.binary.read_bytes() == b"app-binary"
    assert [command[:4] for command in scripts] == [["powershell", "-NoProfile", "-NonInteractive", "-Command"]] * 2
    menu, desktop = (command[-1] for command in scripts)
    assert f"CreateShortcut('{layout.start_menu / 'Kvist Pixel.lnk'}')" in menu
    assert f"CreateShortcut('{layout.desktop / 'Kvist Pixel.lnk'}')" in desktop
    assert f"$s.TargetPath = '{layout.binary}'" in menu and f"IconLocation = '{layout.binary},0'" in menu
    assert layout.start_menu == layout.app_data / "Microsoft/Windows/Start Menu/Programs"
    assert "Start menu" in message


def test_windows_replaces_a_running_exe_by_renaming_it_away(tmp_path, built, monkeypatch):
    layout = _layout(tmp_path, "Windows")
    monkeypatch.setattr(install_app, "run_logged", lambda *_, **__: None)
    install_app.install(layout, built)
    built.write_bytes(b"newer")

    install_app.install(layout, built)

    assert layout.binary.read_bytes() == b"newer"
    assert not layout.binary.with_name("kvist-app.exe.old").exists()


def test_windows_uninstall_takes_the_shortcuts_with_it(tmp_path, built, monkeypatch):
    layout = _layout(tmp_path, "Windows")
    monkeypatch.setattr(install_app, "run_logged", lambda *_, **__: None)
    install_app.install(layout, built)
    for link in (layout.start_menu / "Kvist Pixel.lnk", layout.desktop / "Kvist Pixel.lnk"):
        link.parent.mkdir(parents=True, exist_ok=True)
        link.write_bytes(b"lnk")

    removed = install_app.uninstall(layout)

    assert {path.name for path in removed} == {"app", "Kvist Pixel.lnk"}
    assert not layout.app_dir.exists()


# ------------------------------------------------------------------- the whole command


def test_the_command_builds_then_installs(tmp_path, built, monkeypatch, capsys):
    layout = _layout(tmp_path, "Linux")
    monkeypatch.setattr(install_app, "layout_from_environment", lambda: layout)
    monkeypatch.setattr(install_app, "build_from_source", lambda system: built if system == "Linux" else None)

    install_app.main([])

    assert layout.binary.read_bytes() == b"app-binary"
    assert layout.desktop_entry.is_file()
    assert "Look for Kvist Pixel" in capsys.readouterr().out


def test_a_failed_build_stops_the_command_before_anything_is_installed(tmp_path, monkeypatch):
    layout = _layout(tmp_path, "Linux")
    monkeypatch.setattr(install_app, "layout_from_environment", lambda: layout)

    def fail(system):
        raise install_app.InstallCommandError(["cargo", "build"], 101, "")

    monkeypatch.setattr(install_app, "build_from_source", fail)
    monkeypatch.setattr(install_app, "report_command_failure", lambda error: None)

    with pytest.raises(SystemExit) as stopped:
        install_app.main([])

    assert stopped.value.code == 1
    assert not layout.app_dir.exists() and not layout.desktop_entry.exists()


def test_uninstall_through_the_command(tmp_path, built, monkeypatch, capsys):
    layout = _layout(tmp_path, "Linux")
    monkeypatch.setattr(install_app, "layout_from_environment", lambda: layout)
    install_app.install(layout, built)

    install_app.main(["--uninstall"])

    assert "Removed" in capsys.readouterr().out and not layout.app_dir.exists()


def test_the_checkout_has_the_files_the_menu_entries_need():
    icons = install_app.TAURI_DIR / "icons"
    for name in ("128x128@2x.png", "icon.icns", "icon.ico"):
        assert (icons / name).is_file(), name
    assert (install_app.TAURI_DIR / "Cargo.lock").is_file(), "--locked needs the lock file"
