"""Negative and defensive tests for all installer entry points."""

from __future__ import annotations

import importlib
import os
import stat
import sys
import zipfile
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent / "installer"
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

install = importlib.import_module("install_cli")
gui = importlib.import_module("install_gui")
jetbrains = importlib.import_module("install_jetbrains")
common = importlib.import_module("_common")

@pytest.fixture(autouse=True)
def _fresh_installer_modules():
    """Undo any platform monkeypatching a sibling test file left on the shared
    installer modules (test_install.py reloads `install` as Windows)."""
    importlib.reload(install)
    yield
    # A test that drove an installer's main() left quiet mode switched on and
    # its log handle closed; the next test's log() would write to that closed
    # file. Nothing resets this in a real run, where each installer is a process.
    common._log_file = None
    common._log_path = None
    common._last_step_shown_at = None


def _zip(path: Path, *members: tuple[str, bytes]) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        for name, data in members:
            archive.writestr(name, data)
    return path


def test_safe_extract_rejects_parent_traversal_without_writing(tmp_path):
    archive = _zip(tmp_path / "plugin.zip", ("../outside.txt", b"bad"))
    dest = tmp_path / "plugins"
    dest.mkdir()

    with pytest.raises(SystemExit, match="path-traversal"):
        common.safe_extract_zip(archive, dest)

    assert not (tmp_path / "outside.txt").exists()


def test_safe_extract_rejects_absolute_member_without_writing(tmp_path):
    archive = _zip(tmp_path / "plugin.zip", ("/outside.txt", b"bad"))
    dest = tmp_path / "plugins"
    dest.mkdir()

    with pytest.raises(SystemExit, match="path-traversal"):
        common.safe_extract_zip(archive, dest)

    assert not (tmp_path / "outside.txt").exists()


def test_safe_extract_rejects_symlink_member(tmp_path):
    archive = tmp_path / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        info = zipfile.ZipInfo("kvist-intellij/link")
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        handle.writestr(info, "../../outside")

    with pytest.raises(SystemExit, match="symlink"):
        common.safe_extract_zip(archive, tmp_path / "plugins")


def test_malformed_plugin_zip_is_reported_as_controlled_error(tmp_path):
    broken = tmp_path / "broken.zip"
    broken.write_bytes(b"not a zip")

    with pytest.raises(SystemExit, match="ZIP"):
        jetbrains._zip_layout(broken)


def test_untrusted_zip_root_cannot_delete_outside_plugin_tree(tmp_path):
    config = tmp_path / "ide"
    plugins = config / "plugins"
    plugins.mkdir(parents=True)
    victim = config / "keep.txt"
    victim.write_text("must survive")
    archive = _zip(tmp_path / "plugin.zip", ("../keep.txt/payload", b"bad"))

    with pytest.raises(SystemExit):
        jetbrains.install_into(config, archive, "..")

    assert victim.exists()


def test_failed_plugin_replacement_preserves_previous_install(tmp_path, monkeypatch):
    config = tmp_path / "ide"
    plugins = config / "plugins"
    old = plugins / "kvist-intellij"
    old.mkdir(parents=True)
    (old / "old.txt").write_text("keep")
    archive = _zip(tmp_path / "plugin.zip", ("kvist-intellij/new.txt", b"new"))
    monkeypatch.setattr(jetbrains, "safe_extract_zip", lambda *_: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        jetbrains.install_into(config, archive, "kvist-intellij")

    assert (old / "old.txt").read_text() == "keep"


def test_install_rejects_missing_binary_without_creating_directories(tmp_path):
    lib_dir = tmp_path / "lib"
    bin_dir = tmp_path / "bin"

    with pytest.raises(SystemExit, match="binary not found"):
        install.install(tmp_path / "missing", lib_dir, bin_dir)

    assert not lib_dir.exists()
    assert not bin_dir.exists()


def test_failed_core_install_is_cleaned_up(tmp_path, monkeypatch):
    binary = tmp_path / "kvist"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(Path, "write_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read only")))

    with pytest.raises(OSError, match="read only"):
        install.install(binary, tmp_path / "lib", tmp_path / "bin")

    assert not (tmp_path / "lib" / install.CORE_NAME).exists()
    assert not (tmp_path / "bin" / install.WRAPPER_NAME).exists()


def test_unix_wrapper_escapes_adversarial_core_path(tmp_path):
    binary = tmp_path / "core"
    binary.write_bytes(b"binary")
    lib_dir = tmp_path / 'lib"; touch PWNED; #'
    wrapper = install.install(binary, lib_dir, tmp_path / "bin")
    text = wrapper.read_text()

    assert "CORE='" in text
    assert not (tmp_path / "PWNED").exists()


def test_binary_and_wrapper_are_not_group_or_world_writable(tmp_path):
    binary = tmp_path / "kvist"
    binary.write_bytes(b"binary")
    wrapper = install.install(binary, tmp_path / "lib", tmp_path / "bin")

    assert wrapper.stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH) == 0
    assert (tmp_path / "lib" / install.CORE_NAME).stat().st_mode & (stat.S_IWGRP | stat.S_IWOTH) == 0


def test_reinstall_failure_preserves_previous_core_and_wrapper(tmp_path, monkeypatch):
    binary = tmp_path / "kvist"
    binary.write_bytes(b"new")
    lib_dir = tmp_path / "lib"
    bin_dir = tmp_path / "bin"
    first_wrapper = install.install(binary, lib_dir, bin_dir)
    old_wrapper = first_wrapper.read_text()
    old_core = (lib_dir / install.CORE_NAME).read_bytes()
    monkeypatch.setattr(Path, "write_text", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("read only")))

    with pytest.raises(OSError, match="read only"):
        install.install(binary, lib_dir, bin_dir)

    assert (lib_dir / install.CORE_NAME).read_bytes() == old_core
    assert first_wrapper.read_text() == old_wrapper


def test_cli_rejects_lib_dir_that_is_a_file(tmp_path, monkeypatch):
    binary = tmp_path / "kvist"
    binary.write_bytes(b"binary")
    lib_file = tmp_path / "lib"
    lib_file.write_text("not a directory")
    monkeypatch.setattr(sys, "argv", ["install_cli.py", str(binary), "--lib-dir", str(lib_file)])

    with pytest.raises(SystemExit):
        install.main()


def test_cli_does_not_claim_success_after_jetbrains_install_failure(tmp_path, monkeypatch, capsys):
    config = tmp_path / "PyCharm2025.1"
    config.mkdir()
    zip_path = _zip(tmp_path / "plugin.zip", ("kvist-intellij/plugin.xml", b"x"))
    monkeypatch.setattr(jetbrains, "discover_jetbrains_ides", lambda: [("PyCharm", config)])
    monkeypatch.setattr(jetbrains, "install_into", lambda *_: (_ for _ in ()).throw(OSError("disk full")))
    monkeypatch.setattr(sys, "argv", ["install_jetbrains.py", "--plugin-zip", str(zip_path)])

    with pytest.raises(SystemExit, match="plugin installation failed"):
        jetbrains.main()

    assert "Done." not in capsys.readouterr().out


def test_linux_gui_failure_does_not_destroy_previous_payload(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "kvist-gui").write_bytes(b"new")
    old = tmp_path / "installed"
    old.mkdir()
    (old / "old.txt").write_text("keep")
    monkeypatch.setattr(gui, "INSTALL_ROOT", old)
    monkeypatch.setattr(gui, "_find_linux_payload", lambda: ("dir", payload))
    monkeypatch.setattr(gui.shutil, "copytree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        gui.install_linux()

    assert (old / "old.txt").read_text() == "keep"


def test_linux_gui_keeps_appimage_after_successful_install(tmp_path, monkeypatch):
    payload = tmp_path / "Kvist-1.2.0.AppImage"
    payload.write_bytes(b"appimage")
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "icon.svg").write_text("svg")
    for size in gui.LINUX_ICON_SIZES:
        (assets / f"icon-{size}.png").write_bytes(b"icon")

    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(gui, "INSTALL_ROOT", home / ".local" / "share" / "kvist-gui")
    monkeypatch.setattr(gui, "ASSETS_DIR", assets)
    monkeypatch.setattr(gui, "_find_linux_payload", lambda: ("appimage", payload))

    gui.install_linux()

    installed = gui.INSTALL_ROOT / "Kvist.AppImage"
    assert installed.read_bytes() == b"appimage"
    assert installed.stat().st_mode & stat.S_IXUSR
    assert (home / ".local" / "share" / "applications" / "kvist.desktop").is_file()


def test_linux_gui_failure_preserves_previous_unpacked_payload(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "kvist-gui").write_bytes(b"new")
    install_root = tmp_path / "installed"
    previous = install_root / "linux-unpacked"
    previous.mkdir(parents=True)
    (previous / "old.txt").write_text("keep")
    monkeypatch.setattr(gui, "INSTALL_ROOT", install_root)
    monkeypatch.setattr(gui, "_find_linux_payload", lambda: ("dir", payload))
    monkeypatch.setattr(gui.shutil, "copytree", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")))

    with pytest.raises(OSError, match="disk full"):
        gui.install_linux()

    assert (previous / "old.txt").read_text() == "keep"


def test_windows_gui_prefers_setup_executable(tmp_path, monkeypatch):
    setup = tmp_path / "Kvist Setup 1.2.0.exe"
    setup.write_bytes(b"setup")
    monkeypatch.setattr(gui, "GUI_DIST", tmp_path)

    assert gui._find_windows_payload() == ("setup", setup)


def test_windows_gui_falls_back_to_unpacked_directory(tmp_path, monkeypatch):
    unpacked = tmp_path / "win-unpacked"
    unpacked.mkdir()
    monkeypatch.setattr(gui, "GUI_DIST", tmp_path)

    assert gui._find_windows_payload() == ("dir", unpacked)


def test_empty_and_multi_root_plugin_archives_are_rejected(tmp_path):
    empty = tmp_path / "empty.zip"
    with zipfile.ZipFile(empty, "w"):
        pass
    with pytest.raises(SystemExit, match="ZIP layout"):
        jetbrains._zip_layout(empty)

    multi = _zip(tmp_path / "multi.zip", ("one/file", b"1"), ("two/file", b"2"))
    with pytest.raises(SystemExit, match="ZIP layout"):
        jetbrains._zip_layout(multi)


def test_safe_extract_rejects_nested_traversal_and_does_not_partially_extract(tmp_path):
    archive = _zip(
        tmp_path / "plugin.zip",
        ("kvist-intellij/ok.txt", b"ok"),
        ("kvist-intellij/../../outside.txt", b"bad"),
    )
    dest = tmp_path / "plugins"
    dest.mkdir()

    with pytest.raises(SystemExit, match="path-traversal"):
        common.safe_extract_zip(archive, dest)

    assert not (dest / "kvist-intellij" / "ok.txt").exists()
    assert not (tmp_path / "outside.txt").exists()


def test_install_rejects_directory_as_binary(tmp_path):
    binary = tmp_path / "binary-dir"
    binary.mkdir()

    with pytest.raises(SystemExit, match="binary not found"):
        install.install(binary, tmp_path / "lib", tmp_path / "bin")


def test_install_does_not_leave_core_when_copy_is_interrupted(tmp_path, monkeypatch):
    binary = tmp_path / "kvist"
    binary.write_bytes(b"binary")
    monkeypatch.setattr(install.shutil, "copy2", lambda *_: (_ for _ in ()).throw(KeyboardInterrupt))

    with pytest.raises(KeyboardInterrupt):
        install.install(binary, tmp_path / "lib", tmp_path / "bin")

    assert not (tmp_path / "lib" / install.CORE_NAME).exists()


def test_build_stops_when_dependency_installation_fails(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(install, "VENV_DIR", tmp_path / "venv")
    monkeypatch.setattr(install, "find_base_python", lambda: "/usr/bin/python3")

    def fail(command, *_args, **_kwargs):
        calls.append(command)
        raise SystemExit("pip failed")

    monkeypatch.setattr(install, "run_logged", fail)

    with pytest.raises(SystemExit, match="pip failed"):
        install.ensure_build_venv()

    assert len(calls) == 1


def test_jetbrains_discovery_ignores_malformed_product_names(tmp_path, monkeypatch):
    root = tmp_path / "JetBrains"
    root.mkdir()
    for name in ("PyCharm", "PyCharmabc", "PyCharm2025.1", "NotAnIDE", "PyCharm0"):
        (root / name).mkdir()
    monkeypatch.setattr(common, "jetbrains_config_roots", lambda: [root])

    found = common.discover_jetbrains_ides()

    assert [(label, path.name) for label, path in found] == [
        ("PyCharm 0", "PyCharm0"),
        ("PyCharm 2025.1", "PyCharm2025.1"),
    ]


def test_unexpected_environment_paths_are_treated_as_literal_paths(monkeypatch, tmp_path):
    hostile = tmp_path / "$(touch PWNED)"
    monkeypatch.setenv("PATH", str(hostile))

    assert install.path_contains(hostile)
    assert not (tmp_path / "PWNED").exists()


def test_linux_gui_failure_cleans_payload_and_launcher(tmp_path, monkeypatch):
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "kvist-gui").write_bytes(b"new")
    install_root = tmp_path / "installed"
    home = tmp_path / "home"
    monkeypatch.setattr(Path, "home", lambda: home)
    monkeypatch.setattr(gui, "INSTALL_ROOT", install_root)
    monkeypatch.setattr(gui, "ASSETS_DIR", tmp_path / "assets")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "icon.svg").write_text("svg")
    monkeypatch.setattr(gui, "_find_linux_payload", lambda: ("dir", payload))
    monkeypatch.setattr(gui.shutil, "copy2", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("icon failure")))

    with pytest.raises(OSError, match="icon failure"):
        gui.install_linux()

    assert not install_root.exists()
    assert not (Path.home() / ".local" / "share" / "applications" / "kvist.desktop").exists()


def test_path_contains_handles_empty_and_duplicate_entries(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join(["", str(tmp_path), str(tmp_path)]))
    assert install.path_contains(tmp_path)
    assert not install.path_contains(tmp_path / "missing")


def test_venv_recreation_does_not_silently_ignore_rmtree_failure(monkeypatch, tmp_path):
    python_path = install.venv_python(tmp_path)
    python_path.parent.mkdir(parents=True)
    python_path.write_bytes(b"python")
    monkeypatch.setattr(install, "VENV_DIR", tmp_path)
    monkeypatch.setattr(install, "_has_shared_libpython", lambda _: False)
    monkeypatch.setattr(install.shutil, "rmtree", lambda *_: (_ for _ in ()).throw(PermissionError("locked")))

    with pytest.raises(PermissionError, match="locked"):
        install.ensure_build_venv()
