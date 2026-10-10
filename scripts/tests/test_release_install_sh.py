# -*- coding: utf-8 -*-
"""scripts/release/install.sh with `uname` faked, so the macOS branches run anywhere with bash."""
from __future__ import annotations

import json
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


def _assets(directory: Path, binary: str, drop: tuple[str, ...] = ()) -> Path:
    directory.mkdir(exist_ok=True)
    (directory / binary).write_bytes(b"core:" + binary.encode())
    with zipfile.ZipFile(directory / "kvist-jetbrains.zip", "w") as archive:
        archive.writestr("kvist-intellij/lib/kvist.jar", b"jar")
    (directory / "kvist-vscode.vsix").write_bytes(b"vsix")
    for name in drop:
        (directory / name).unlink()
    return directory


def _run(tmp_path: Path, system: str, machine: str, binary: str, shell: str = "/bin/zsh", extra_env=None,
         args: tuple[str, ...] = (), drop: tuple[str, ...] = ()):
    """Run the installer in a scratch home (reused by a second call); returns (result, home)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    # Only the tools the script uses, so an editor installed on this machine is not found.
    for tool in ("sh", "env", "basename", "dirname", "mktemp", "install", "cat", "mv", "rm",
                 "mkdir", "chmod", "sort", "unzip", "cp"):
        if not (shims / tool).exists():
            (shims / tool).symlink_to(shutil.which(tool))
    real_uname = shutil.which("uname")
    shim = shims / "uname"
    shim.unlink(missing_ok=True)
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
        [shutil.which("bash"), str(SCRIPT), "--from", str(_assets(tmp_path / "assets", binary, drop)), *args],
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


def test_plan_lists_what_each_component_would_touch_and_changes_nothing(tmp_path):
    ide = tmp_path / "home" / ".local" / "share" / "JetBrains" / "PyCharm2025.3"
    ide.mkdir(parents=True)

    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", shell="/bin/zsh", args=("--plan",))

    assert result.returncode == 0, result.stdout + result.stderr
    plan = json.loads(result.stdout)
    components = {item["id"]: item for item in plan["components"]}
    assert plan["os"] == "Linux" and plan["shell"] == "zsh"
    assert components["cli"]["targets"][0] == str(home / ".local" / "share" / "kvist" / "kvist-core")
    assert components["jetbrains"] == {
        "id": "jetbrains", "available": True, "targets": [str(ide / "kvist-intellij")],
    }
    assert components["vscode"]["available"] is False
    assert components["path"]["available"] is True
    assert components["path"]["targets"][0].startswith(str(home / ".zshrc"))
    assert not (home / ".local").joinpath("share", "kvist").exists()


def test_only_installs_just_the_chosen_components_and_reports_json_events(tmp_path):
    ide = tmp_path / "home" / ".local" / "share" / "JetBrains" / "PyCharm2025.3"
    ide.mkdir(parents=True)

    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", shell="/bin/bash",
                        args=("--only", "cli,path", "--events"))

    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert [e for e in events if e["event"] == "component"] == [
        {"event": "component", "id": "cli", "status": "start"},
        {"event": "component", "id": "cli", "status": "done"},
        {"event": "component", "id": "path", "status": "start"},
        {"event": "component", "id": "path", "status": "done"},
    ]
    assert events[-1] == {"event": "done"}
    assert (home / ".local" / "share" / "kvist" / "kvist-core").is_file()
    assert not (ide / "kvist-intellij").exists()
    assert str(home / ".local" / "bin") in (home / ".bashrc").read_text()


def test_the_path_line_is_added_once(tmp_path):
    for _run_number in range(2):
        result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", shell="/bin/bash",
                            args=("--only", "path"))
        assert result.returncode == 0, result.stdout + result.stderr

    assert (home / ".bashrc").read_text().count(str(home / ".local" / "bin")) == 1


def test_a_missing_asset_stops_a_selected_install_before_anything_is_written(tmp_path):
    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--only", "vscode"),
                        drop=("kvist-vscode.vsix",))

    assert result.returncode != 0
    assert "kvist-vscode.vsix not found" in result.stderr
    assert not (home / ".local").exists()


def test_node_is_installed_beside_the_binary_from_the_one_the_bootstrap_prepared(tmp_path):
    node_home = tmp_path / "prepared-node"
    (node_home / "bin").mkdir(parents=True)
    (node_home / "bin" / "node").write_bytes(b"node")

    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--only", "node"),
                        extra_env={"KVIST_NODE_HOME": str(node_home)})

    assert result.returncode == 0, result.stdout + result.stderr
    assert (home / ".local" / "share" / "kvist" / "node" / "bin" / "node").read_bytes() == b"node"
    assert not (home / ".local" / "bin").exists()


def test_plan_names_the_node_download_and_where_it_goes(tmp_path):
    result, home = _run(tmp_path, "Darwin", "arm64", "kvist-macos-arm64", args=("--plan",))

    plan = json.loads(result.stdout)
    node = next(item for item in plan["components"] if item["id"] == "node")
    assert node["targets"] == [
        "https://nodejs.org/dist/v22.23.3/node-v22.23.3-darwin-arm64.tar.gz",
        str(home / ".local" / "share" / "kvist" / "node"),
    ]
