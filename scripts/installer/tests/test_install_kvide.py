# -*- coding: utf-8 -*-
"""The kvide installer wires kvide to the installed core and removes only its own files."""
from __future__ import annotations

import json
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


@pytest.fixture
def user_config(tmp_path):
    return tmp_path / "config" / "kvide" / "generators.toml"


def test_install_writes_a_generators_file_kvide_can_load(lib_dir, user_config):
    config, is_default = install_kvide.install(lib_dir, user_config)

    assert is_default and config == user_config
    entry = tomllib.loads(config.read_text(encoding="utf-8"))["generators"][0]
    assert entry["name"] == "Kvist"
    assert entry["color"] == install_kvide.ICON_COLOR
    assert entry["command"] == [
        sys.executable,
        str(lib_dir / "kvide" / "kvist_generator.py"),
        "--core",
        str(lib_dir / CORE_NAME),
    ]
    icon = Path(entry["icon"])
    assert icon.is_absolute() and icon.read_text(encoding="utf-8").strip() == install_kvide.ICON_GLYPH
    assert (lib_dir / "kvide" / "kvist_generator.py").is_file()


def test_install_twice_gives_the_same_file(lib_dir, user_config):
    install_kvide.install(lib_dir, user_config)
    first = user_config.read_text(encoding="utf-8")
    install_kvide.install(lib_dir, user_config)

    assert user_config.read_text(encoding="utf-8") == first


def test_a_foreign_user_file_is_kept_and_a_local_copy_is_offered(lib_dir, user_config):
    user_config.parent.mkdir(parents=True)
    user_config.write_text("# mine\n", encoding="utf-8")

    config, is_default = install_kvide.install(lib_dir, user_config)

    assert not is_default and config == lib_dir / "kvide" / "generators.toml"
    assert user_config.read_text(encoding="utf-8") == "# mine\n"
    assert config.is_file()


def test_install_refuses_without_the_cli_binary(tmp_path, user_config):
    with pytest.raises(SystemExit, match="install_cli.py"):
        install_kvide.install(tmp_path, user_config)

    assert not user_config.exists()


def test_uninstall_removes_only_our_files(lib_dir, user_config):
    install_kvide.install(lib_dir, user_config)

    assert install_kvide.uninstall(lib_dir, user_config) is True
    assert not (lib_dir / "kvide").exists() and not user_config.exists()
    assert (lib_dir / CORE_NAME).is_file()
    assert install_kvide.uninstall(lib_dir, user_config) is False


def test_uninstall_keeps_a_foreign_user_file(lib_dir, user_config):
    user_config.parent.mkdir(parents=True)
    user_config.write_text("# mine\n", encoding="utf-8")
    install_kvide.install(lib_dir, user_config)

    install_kvide.uninstall(lib_dir, user_config)

    assert user_config.read_text(encoding="utf-8") == "# mine\n"


def test_user_config_path_follows_xdg_then_home(monkeypatch, tmp_path):
    monkeypatch.setattr(install_kvide, "IS_WINDOWS", False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    assert install_kvide.user_config_path() == tmp_path / "xdg" / "kvide" / "generators.toml"

    monkeypatch.delenv("XDG_CONFIG_HOME")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    assert install_kvide.user_config_path() == tmp_path / ".config" / "kvide" / "generators.toml"


def test_install_completion_copies_the_extension_and_records_the_core(lib_dir):
    extension = install_kvide.install_completion(lib_dir)

    assert extension == lib_dir / "kvide" / "completion-extension"
    assert (extension / "extension.cjs").is_file() and (extension / "package.json").is_file()
    assert json.loads((extension / "core.json").read_text(encoding="utf-8")) == {"core": str(lib_dir / CORE_NAME)}


def test_uninstall_removes_the_completion_extension(lib_dir, user_config):
    install_kvide.install(lib_dir, user_config)
    install_kvide.install_completion(lib_dir)

    install_kvide.uninstall(lib_dir, user_config)

    assert not (lib_dir / "kvide").exists()
