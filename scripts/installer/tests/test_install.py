# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

install = importlib.import_module("install_cli")
_common = importlib.import_module("_common")


def test_remove_path_deletes_regular_dir_and_file(tmp_path):
    directory = tmp_path / "d"
    (directory / "sub").mkdir(parents=True)
    (directory / "sub" / "f").write_text("x")
    file = tmp_path / "f"
    file.write_text("x")

    assert _common.remove_path(directory) is True
    assert _common.remove_path(file) is True
    assert not directory.exists() and not file.exists()


def test_remove_path_is_noop_when_absent(tmp_path):
    assert _common.remove_path(tmp_path / "missing") is False


def test_remove_path_unlinks_symlinked_dir_without_touching_target(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "keep").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    assert _common.remove_path(link) is True
    assert not link.exists()
    assert (real / "keep").read_text() == "x"


def test_remove_path_clears_broken_symlink(tmp_path):
    link = tmp_path / "dangling"
    link.symlink_to(tmp_path / "nowhere")

    assert _common.remove_path(link) is True
    assert not link.is_symlink()


def _reload_as(monkeypatch: pytest.MonkeyPatch, system: str):
    monkeypatch.setattr(install.platform, "system", lambda: system)
    return importlib.reload(install)


@pytest.fixture(autouse=True)
def _restore_module():
    yield
    importlib.reload(install)


def test_windows_wrapper_forwards_workspace_and_exit_code(monkeypatch, tmp_path):
    mod = _reload_as(monkeypatch, "Windows")
    monkeypatch.setattr(mod, "DEFAULT_LIB_DIR", tmp_path / "lib")
    binary = tmp_path / "kvist.exe"
    binary.write_bytes(b"MZ")

    wrapper = mod.install(binary, tmp_path / "lib", tmp_path / "bin")

    assert wrapper.name == "kvist.cmd"
    raw = wrapper.read_bytes()
    assert b"\r\n" in raw
    assert raw.count(b"\n") == raw.count(b"\r\n")  # every LF is part of a CRLF
    text = wrapper.read_text()
    assert '--workspace "%CD%" %*' in text
    assert "exit /b %ERRORLEVEL%" in text
    assert str(tmp_path / "lib" / "kvist-core.exe") in text


def test_unix_wrapper_execs_core(monkeypatch, tmp_path):
    mod = _reload_as(monkeypatch, "Linux")
    binary = tmp_path / "kvist"
    binary.write_bytes(b"\x7fELF")

    wrapper = mod.install(binary, tmp_path / "lib", tmp_path / "bin")

    assert wrapper.name == "kvist"
    text = wrapper.read_text()
    assert text.startswith("#!/usr/bin/env bash")
    assert 'exec "$CORE" --workspace "$PWD" "$@"' in text
    assert b"\r\n" not in wrapper.read_bytes()


def test_install_reports_locked_core(monkeypatch, tmp_path):
    mod = _reload_as(monkeypatch, "Windows")
    binary = tmp_path / "kvist.exe"
    binary.write_bytes(b"MZ")

    def deny(*_args, **_kwargs):
        raise PermissionError(13, "in use")

    monkeypatch.setattr(mod.shutil, "copy2", deny)

    with pytest.raises(SystemExit, match="Close every running kvist"):
        mod.install(binary, tmp_path / "lib", tmp_path / "bin")


# --- the Ink terminal UI is built into the binary when Node.js is available ---


def _bundle_paths(monkeypatch, tmp_path):
    bundle = tmp_path / "dist-ui" / "kvist-tui.mjs"
    monkeypatch.setattr(install, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(install, "UI_DIR", tmp_path)
    monkeypatch.setattr(install, "UI_BUNDLE", bundle)
    steps: list[str] = []
    monkeypatch.setattr(install, "show_step", lambda status, text: steps.append(text))
    return bundle, steps


def test_ui_build_installs_packages_then_bundles(monkeypatch, tmp_path):
    bundle, steps = _bundle_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/npm")
    commands: list[list[str]] = []

    def fake_run(command, *, cwd=None):
        commands.append(command[1:])
        if command[1:] == ["run", "build"]:
            bundle.parent.mkdir()
            bundle.write_text("// bundle")

    monkeypatch.setattr(install, "run_logged", fake_run)
    assert install.build_terminal_ui() is True
    assert commands == [["install"], ["run", "build"]]  # no node_modules yet
    assert install.STEPS["build_ui"] in steps


def test_ui_build_skips_npm_install_when_packages_exist(monkeypatch, tmp_path):
    bundle, _ = _bundle_paths(monkeypatch, tmp_path)
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/npm")
    commands: list[list[str]] = []
    monkeypatch.setattr(install, "run_logged", lambda command, *, cwd=None: commands.append(command[1:]))
    install.build_terminal_ui()
    assert commands == [["run", "build"]]


def test_ui_build_without_npm_warns_and_the_install_goes_on(monkeypatch, tmp_path):
    _, steps = _bundle_paths(monkeypatch, tmp_path)
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    assert install.build_terminal_ui() is False
    assert steps == [install.STEPS["ui_no_npm"]]


def test_ui_build_without_npm_keeps_an_existing_bundle_quietly(monkeypatch, tmp_path):
    bundle, steps = _bundle_paths(monkeypatch, tmp_path)
    bundle.parent.mkdir()
    bundle.write_text("// bundle")
    monkeypatch.setattr(install.shutil, "which", lambda name: None)
    assert install.build_terminal_ui() is True
    assert steps == []


def test_a_failed_ui_build_warns_instead_of_failing_the_install(monkeypatch, tmp_path):
    _, steps = _bundle_paths(monkeypatch, tmp_path)
    (tmp_path / "node_modules").mkdir()
    monkeypatch.setattr(install.shutil, "which", lambda name: "/usr/bin/npm")

    def failing(command, *, cwd=None):
        raise install.InstallCommandError("command failed (exit 1): npm run build")

    monkeypatch.setattr(install, "run_logged", failing)
    assert install.build_terminal_ui() is False
    assert steps[-1] == install.STEPS["ui_failed"]


def test_the_spec_packs_the_bundle_only_when_it_exists(tmp_path):
    spec = Path(__file__).resolve().parents[3] / "kvist.spec"
    text = spec.read_text(encoding="utf-8")
    assert "dist-ui" in text and "kvist-tui.mjs" in text
    assert "os.path.isfile(_UI_BUNDLE)" in text
