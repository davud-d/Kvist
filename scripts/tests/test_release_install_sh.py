# -*- coding: utf-8 -*-
"""scripts/release/install.sh with `uname` faked, so the macOS branches run anywhere with bash."""
from __future__ import annotations

import shutil
import stat
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "release" / "install.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not shutil.which("unzip"),
    reason="needs bash and unzip",
)


def _assets(directory: Path, binary: str) -> Path:
    directory.mkdir()
    (directory / binary).write_bytes(b"core:" + binary.encode())
    with zipfile.ZipFile(directory / "kvist-jetbrains.zip", "w") as archive:
        archive.writestr("kvist-intellij/lib/kvist.jar", b"jar")
    (directory / "kvist-vscode.vsix").write_bytes(b"vsix")
    return directory


def _run(tmp_path: Path, system: str, machine: str, binary: str, shell: str = "/bin/zsh", extra_env=None):
    """Run the installer in a scratch home; returns (result, home)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shims = tmp_path / "shims"
    shims.mkdir()
    # Only the tools the script uses, so an editor installed on this machine is not found.
    for tool in ("sh", "env", "basename", "dirname", "mktemp", "install", "cat", "mv", "rm",
                 "mkdir", "chmod", "sort", "unzip"):
        (shims / tool).symlink_to(shutil.which(tool))
    real_uname = shutil.which("uname")
    shim = shims / "uname"
    shim.write_text(
        f'#!/bin/sh\ncase "$1" in -s) echo {system};; -m) echo {machine};; *) exec {real_uname} "$@";; esac\n'
    )
    shim.chmod(shim.stat().st_mode | stat.S_IXUSR)
    env = {
        "PATH": str(shims),
        "HOME": str(home),
        "SHELL": shell,
        "TMPDIR": str(tmp_path),
    }
    env.update(extra_env or {})
    result = subprocess.run(
        [shutil.which("bash"), str(SCRIPT), "--from", str(_assets(tmp_path / "assets", binary))],
        env=env, capture_output=True, text=True, timeout=60, check=False,
    )
    return result, home


def test_macos_apple_silicon_installs_the_arm64_binary_and_nests_the_jetbrains_plugin(tmp_path):
    ide = tmp_path / "home" / "Library" / "Application Support" / "JetBrains" / "PyCharm2025.3"
    ide.mkdir(parents=True)
    # The editor's launcher lives inside its app bundle: `code` is not on PATH.
    code = tmp_path / "home" / "Applications" / "Visual Studio Code.app" / "Contents" / "Resources" / "app" / "bin" / "code"
    code.parent.mkdir(parents=True)
    code.write_text(f'#!/bin/sh\necho "$@" > "{tmp_path}/code-args"\n')
    code.chmod(0o755)

    result, home = _run(tmp_path, "Darwin", "arm64", "kvist-macos-arm64")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".local" / "share" / "kvist" / "kvist-core").read_bytes() == b"core:kvist-macos-arm64"
    assert "--web-controller" in (home / ".local" / "bin" / "kvist-web").read_text()
    assert "--web-bridge" in (home / ".local" / "bin" / "kvist-web-bridge").read_text()
    assert (ide / "plugins" / "kvist-intellij" / "lib" / "kvist.jar").read_bytes() == b"jar"
    assert not (ide / "kvist-intellij").exists()
    assert "--install-extension" in (tmp_path / "code-args").read_text()
    assert "~/.zshrc" in result.stdout


def test_macos_intel_installs_the_x64_binary(tmp_path):
    result, home = _run(tmp_path, "Darwin", "x86_64", "kvist-macos-x64")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".local" / "share" / "kvist" / "kvist-core").read_bytes() == b"core:kvist-macos-x64"


def test_macos_bash_users_are_pointed_at_bash_profile(tmp_path):
    result, _home = _run(tmp_path, "Darwin", "arm64", "kvist-macos-arm64", shell="/bin/bash")

    assert "~/.bash_profile" in result.stdout


def test_linux_keeps_its_binary_and_the_flat_jetbrains_layout(tmp_path):
    ide = tmp_path / "home" / ".local" / "share" / "JetBrains" / "PyCharm2025.3"
    ide.mkdir(parents=True)

    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", shell="/bin/bash")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".local" / "share" / "kvist" / "kvist-core").read_bytes() == b"core:kvist-linux-x64"
    assert "--web-controller" in (home / ".local" / "bin" / "kvist-web").read_text()
    assert "--web-bridge" in (home / ".local" / "bin" / "kvist-web-bridge").read_text()
    assert (ide / "kvist-intellij" / "lib" / "kvist.jar").read_bytes() == b"jar"
    assert "~/.bashrc" in result.stdout


@pytest.mark.parametrize("version", ["2025.3", "2026.1", "2026.2", "2027.1"])
def test_linux_finds_a_fresh_pycharm_from_settings_only(tmp_path, version):
    name = f"PyCharmCE{version}"
    settings = tmp_path / "home" / ".config" / "JetBrains" / name
    settings.mkdir(parents=True)

    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64")

    assert result.returncode == 0, result.stdout + result.stderr
    destination = home / ".local" / "share" / "JetBrains" / name
    assert (destination / "kvist-intellij" / "lib" / "kvist.jar").read_bytes() == b"jar"
    assert not (settings / "kvist-intellij").exists()
    assert result.stdout.count("JetBrains plugin installed into") == 1


def test_linux_settings_discovery_uses_xdg_paths(tmp_path):
    config = tmp_path / "settings"
    data = tmp_path / "data"
    (config / "JetBrains" / "PyCharmCE2026.2").mkdir(parents=True)

    result, _home = _run(
        tmp_path, "Linux", "x86_64", "kvist-linux-x64",
        extra_env={"XDG_CONFIG_HOME": str(config), "XDG_DATA_HOME": str(data)},
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert (data / "JetBrains" / "PyCharmCE2026.2" / "kvist-intellij" / "lib" / "kvist.jar").read_bytes() == b"jar"
    assert not (config / "JetBrains" / "PyCharmCE2026.2" / "kvist-intellij").exists()


def test_linux_settings_and_data_do_not_install_the_same_ide_twice(tmp_path):
    name = "PyCharmCE2026.2"
    (tmp_path / "home" / ".config" / "JetBrains" / name).mkdir(parents=True)
    destination = tmp_path / "home" / ".local" / "share" / "JetBrains" / name
    destination.mkdir(parents=True)

    result, _home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64")

    assert result.returncode == 0, result.stdout + result.stderr
    assert (destination / "kvist-intellij" / "lib" / "kvist.jar").is_file()
    assert result.stdout.count("JetBrains plugin installed into") == 1


@pytest.mark.parametrize(("system", "machine"), [("Linux", "aarch64"), ("Darwin", "ppc")])
def test_an_architecture_without_a_release_binary_is_refused(tmp_path, system, machine):
    result, _home = _run(tmp_path, system, machine, "kvist-linux-x64")

    assert result.returncode != 0
    assert f"no release binary for {system} on {machine}" in result.stderr


def test_windows_is_pointed_at_the_powershell_installer(tmp_path):
    result, _home = _run(tmp_path, "MINGW64_NT-10.0", "x86_64", "kvist-linux-x64")

    assert result.returncode != 0
    assert "install.ps1" in result.stderr
