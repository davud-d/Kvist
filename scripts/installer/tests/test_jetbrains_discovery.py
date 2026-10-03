"""Find fresh Linux IDEs before any external plugin has created their data directory."""

from __future__ import annotations

import importlib
import sys
import zipfile
from pathlib import Path

import pytest

INSTALLER_DIR = Path(__file__).resolve().parent.parent
if str(INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(INSTALLER_DIR))

common = importlib.import_module("_common")
jetbrains = importlib.import_module("install_jetbrains")


@pytest.fixture
def linux_home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(common, "IS_WINDOWS", False)
    monkeypatch.setattr(common, "IS_MACOS", False)
    monkeypatch.setattr(jetbrains, "IS_LINUX", True)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    return tmp_path


@pytest.mark.parametrize("version", ["2025.3", "2026.1", "2026.2", "2027.1"])
def test_config_only_pycharm_is_discovered_and_installed_into_data(linux_home, version, monkeypatch):
    name = f"PyCharmCE{version}"
    settings = linux_home / ".config" / "JetBrains" / name
    settings.mkdir(parents=True)
    destination = linux_home / ".local" / "share" / "JetBrains" / name

    found = common.discover_jetbrains_ides()

    assert found == [(f"PyCharm Community {version}", destination)]
    assert not destination.exists()  # Discovery itself must not create anything.
    archive = linux_home / "plugin.zip"
    with zipfile.ZipFile(archive, "w") as handle:
        handle.writestr("kvist-intellij/lib/kvist.jar", b"plugin")
    monkeypatch.setattr(jetbrains, "log", lambda *_: None)
    jetbrains.install_into(found[0][1], archive, "kvist-intellij")

    assert (destination / "kvist-intellij" / "lib" / "kvist.jar").read_bytes() == b"plugin"
    assert not (settings / "kvist-intellij").exists()
    assert not (destination / "plugins").exists()


def test_config_discovery_respects_xdg_paths(linux_home, monkeypatch):
    config = linux_home / "custom-settings"
    data = linux_home / "custom-data"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    monkeypatch.setenv("XDG_DATA_HOME", str(data))
    (config / "JetBrains" / "PyCharmCE2026.2").mkdir(parents=True)

    assert common.discover_jetbrains_ides() == [
        ("PyCharm Community 2026.2", data / "JetBrains" / "PyCharmCE2026.2"),
    ]


def test_existing_data_destination_is_not_duplicated_or_redirected(linux_home, monkeypatch):
    settings = linux_home / ".config" / "JetBrains" / "PyCharmCE2026.2"
    settings.mkdir(parents=True)
    existing = linux_home / ".local" / "share" / "JetBrains" / settings.name
    existing.mkdir(parents=True)
    monkeypatch.setenv("XDG_DATA_HOME", str(linux_home / "new-data-root"))

    assert common.discover_jetbrains_ides() == [("PyCharm Community 2026.2", existing)]


def test_settings_discovery_ignores_non_ide_names(linux_home):
    root = linux_home / ".config" / "JetBrains"
    for name in ("Toolbox", "PyCharm", "PyCharmCEabc", "NotAnIDE2026.2"):
        (root / name).mkdir(parents=True)

    assert common.discover_jetbrains_ides() == []
