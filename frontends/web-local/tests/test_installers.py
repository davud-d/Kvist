from __future__ import annotations

import plistlib
import shlex

from kvist_web.installers.linux import LinuxInstaller
from kvist_web.installers.macos import MacOSInstaller
from kvist_web.installers.windows import WindowsInstaller


def test_linux_install_reinstall_and_uninstall(tmp_path):
    commands = []
    installer = LinuxInstaller(["/bin/kvist-web"], home=tmp_path, runner=commands.append)
    first = installer.install()
    content = installer.path.read_text()
    second = installer.install()
    assert first.target == second.target
    assert installer.path.read_text() == content
    assert "Restart=on-failure" in content and "--no-open" in content
    assert f"--workspace {shlex.quote(str(tmp_path))}" in content
    assert f"WorkingDirectory={shlex.quote(str(tmp_path))}" in content
    installer.uninstall()
    assert not installer.path.exists()


def test_macos_install_reinstall_and_uninstall(tmp_path, monkeypatch):
    monkeypatch.setattr("os.getuid", lambda: 501, raising=False)
    commands = []
    installer = MacOSInstaller(["/usr/local/bin/kvist-web"], home=tmp_path, runner=commands.append)
    installer.install()
    payload = plistlib.loads(installer.path.read_bytes())
    installer.install()
    assert payload["RunAtLoad"] is True
    assert "--no-open" in payload["ProgramArguments"]
    assert payload["ProgramArguments"][-2:] == ["--workspace", str(tmp_path)]
    assert payload["WorkingDirectory"] == str(tmp_path)
    assert commands[-1] == ["launchctl", "bootstrap", "gui/501", str(installer.path)]
    installer.uninstall()
    assert not installer.path.exists()


def test_windows_install_reinstall_and_uninstall(tmp_path, monkeypatch):
    commands = []
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    installer = WindowsInstaller(
        [r"C:\Program Files\Kvist\kvist-web.exe"], home=tmp_path, runner=commands.append
    )
    installer.install()
    first = installer.path.read_bytes()
    installer.install()
    assert installer.path.read_bytes() == first
    assert any(command[0] == "schtasks.exe" and "/Create" in command for command in commands)
    text = installer.path.read_text(encoding="utf-16")
    assert "--workspace" in text and str(tmp_path) in text
    assert f"<WorkingDirectory>{tmp_path}</WorkingDirectory>" in text
    installer.uninstall()
    assert not installer.path.exists()
