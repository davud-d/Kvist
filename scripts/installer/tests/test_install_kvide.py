# -*- coding: utf-8 -*-
"""The kvide installer wires kvide to the installed core and removes only its own files."""
from __future__ import annotations

import sys
import tomllib
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import install_kvide
from install_cli import CORE_NAME


@pytest.fixture
def lib_dir(tmp_path):
    directory = tmp_path / "lib"
    directory.mkdir()
    (directory / CORE_NAME).write_text("#!/bin/sh\n", encoding="utf-8")
    return directory


def test_install_writes_a_generators_file_kvide_can_load(lib_dir):
    config = install_kvide.install(lib_dir)

    entry = tomllib.loads(config.read_text(encoding="utf-8"))["generators"][0]
    assert entry["name"] == "Kvist"
    assert entry["command"] == [
        sys.executable,
        str(lib_dir / "kvide" / "kvist_generator.py"),
        "--core",
        str(lib_dir / CORE_NAME),
    ]
    assert (config.parent / entry["icon"]).read_text(encoding="utf-8").strip() == install_kvide.ICON_GLYPH
    assert (lib_dir / "kvide" / "kvist_generator.py").is_file()


def test_install_twice_gives_the_same_file(lib_dir):
    first = install_kvide.install(lib_dir).read_text(encoding="utf-8")

    assert install_kvide.install(lib_dir).read_text(encoding="utf-8") == first


def test_install_refuses_without_the_cli_binary(tmp_path):
    with pytest.raises(SystemExit, match="install_cli.py"):
        install_kvide.install(tmp_path)


def test_uninstall_removes_only_the_kvide_directory(lib_dir):
    install_kvide.install(lib_dir)

    assert install_kvide.uninstall(lib_dir) is True
    assert not (lib_dir / "kvide").exists()
    assert (lib_dir / CORE_NAME).is_file()
    assert install_kvide.uninstall(lib_dir) is False
