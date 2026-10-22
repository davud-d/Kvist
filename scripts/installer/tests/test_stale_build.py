# -*- coding: utf-8 -*-
"""An installer must not install a plugin build that is older than the code it is built from."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

import _common
import install_jetbrains
import install_vscode


def _touch(path: Path, when: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x", encoding="utf-8")
    os.utime(path, (when, when))
    return path


def test_a_build_is_older_when_any_source_changed_after_it(tmp_path):
    source = _touch(tmp_path / "src" / "a.py", 100)
    artifact = _touch(tmp_path / "out.vsix", 200)
    assert not _common.is_older_than_sources(artifact, [tmp_path / "src"])
    os.utime(source, (300, 300))
    assert _common.is_older_than_sources(artifact, [tmp_path / "src"])


def test_tests_and_build_output_do_not_make_a_build_stale(tmp_path):
    _touch(tmp_path / "src" / "a.py", 100)
    artifact = _touch(tmp_path / "out.vsix", 200)
    for noise in ("tests/test_a.py", "node_modules/x.js", "__pycache__/a.pyc", "build/out.js", "a.log"):
        _touch(tmp_path / "src" / noise, 900)
    assert not _common.is_older_than_sources(artifact, [tmp_path / "src"])


def test_a_single_file_source_counts(tmp_path):
    spec = _touch(tmp_path / "kvist.spec", 500)
    artifact = _touch(tmp_path / "out.vsix", 200)
    assert _common.is_older_than_sources(artifact, [spec])


@pytest.mark.parametrize("module, finder, builder, name", [
    (install_vscode, "find_extension_vsix", "build_extension_vsix", "x.vsix"),
    (install_jetbrains, "find_plugin_zip", "build_plugin_zip", "x.zip"),
])
def test_an_installer_rebuilds_a_stale_build_when_nothing_is_installed(monkeypatch, tmp_path, module, finder, builder, name):
    source = _touch(tmp_path / "src" / "a.py", 100)
    artifact = _touch(tmp_path / name, 200)
    rebuilt = tmp_path / "rebuilt"
    monkeypatch.setattr(module, "BUILD_SOURCES", [tmp_path / "src"])
    monkeypatch.setattr(module, finder, lambda: artifact)
    monkeypatch.setattr(module, builder, lambda status=None: rebuilt)
    for installed in ("extension_already_installed", "plugin_already_installed"):
        if hasattr(module, installed):
            monkeypatch.setattr(module, installed, lambda *_: False)
    resolve = getattr(module, "resolve_extension_vsix", None) or module.resolve_plugin_zip
    args = argparse.Namespace(extension_vsix=None, plugin_zip=None, build=False)

    assert resolve(args, []) == artifact
    os.utime(source, (300, 300))
    assert resolve(args, []) == rebuilt
