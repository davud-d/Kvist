# -*- coding: utf-8 -*-

from __future__ import annotations

import importlib

import pytest

install = importlib.import_module("install")


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
