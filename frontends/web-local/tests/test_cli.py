from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from kvist_web import cli


class _RunningDescriptor:
    def health(self):
        return True

    def read(self):
        return {"host": "127.0.0.1", "port": 9671, "pid": 42}


def test_repeat_launch_opens_existing_instance_unless_no_open(monkeypatch, capsys):
    opened = []
    monkeypatch.setattr(cli, "InstanceDescriptor", lambda: _RunningDescriptor())
    monkeypatch.setattr(cli.webbrowser, "open", opened.append)

    cli.main([])
    cli.main(["--no-open"])

    assert opened == ["http://127.0.0.1:9671"]
    assert capsys.readouterr().out.count("already running") == 2


def test_non_loopback_bind_requires_explicit_flag_and_token(monkeypatch):
    class _StoppedDescriptor:
        def health(self):
            return False

    monkeypatch.setattr(cli, "InstanceDescriptor", lambda: _StoppedDescriptor())
    with pytest.raises(SystemExit, match="non-loopback bind requires"):
        cli.main(["--no-open", "--host", "0.0.0.0"])


@dataclass
class _Result:
    action: str
    message: str
    target: str


class _Installer:
    def __init__(self):
        self.actions = []

    def install(self):
        self.actions.append("install")
        return _Result("installed", "installed", "/native/service")

    def uninstall(self):
        self.actions.append("uninstall")
        return _Result("uninstalled", "uninstalled", "/native/service")


def test_cli_routes_install_and_uninstall_to_native_backend(monkeypatch):
    installer = _Installer()
    monkeypatch.setattr(cli, "InstanceDescriptor", lambda: object())
    monkeypatch.setattr(cli, "installer_for", lambda _command: installer)

    cli.main(["--install-system"])
    cli.main(["--uninstall-system"])

    assert installer.actions == ["install", "uninstall"]


def test_source_autostart_uses_the_checked_out_core_entrypoint(monkeypatch):
    command = cli.service_command()

    entrypoint = Path(cli.__file__).resolve().parents[3] / "kernel" / "__main__.py"
    assert command == [cli.sys.executable, str(entrypoint), "--web-controller"]


def test_installed_autostart_uses_its_own_python_module(monkeypatch):
    monkeypatch.setattr(Path, "is_file", lambda _self: False)

    assert cli.service_command() == [cli.sys.executable, "-m", "kvist_web.cli"]
