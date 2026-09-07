# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "installer"))

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
