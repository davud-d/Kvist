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
         args: tuple[str, ...] = (), drop: tuple[str, ...] = (), stub_tools: tuple[str, ...] = (), scripts: dict[str, str] | None = None,
         offline_assets: bool = True):
    """Run the installer in a scratch home (reused by a second call); returns (result, home)."""
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    shims = tmp_path / "shims"
    shims.mkdir(exist_ok=True)
    # Only the tools the script uses, so an editor installed on this machine is not found.
    for tool in ("sh", "env", "basename", "dirname", "mktemp", "install", "cat", "mv", "rm",
                 "mkdir", "chmod", "sort", "unzip", "cp", "sha256sum", "wc", "sleep"):
        if shutil.which(tool) and not (shims / tool).exists():
            (shims / tool).symlink_to(shutil.which(tool))
    for tool in stub_tools:
        stub = shims / tool
        stub.write_text("#!/bin/sh\nexit 0\n")
        stub.chmod(0o755)
    for tool, body in (scripts or {}).items():
        script = shims / tool
        script.write_text(body)
        script.chmod(0o755)
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
        [shutil.which("bash"), str(SCRIPT),
         *(("--from", str(_assets(tmp_path / "assets", binary, drop))) if offline_assets else ()), *args],
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
    # "start" comes first while the asset is fetched, then again when it is installed.
    assert [e for e in events if e["event"] == "component"] == [
        {"event": "component", "id": "cli", "status": "start"},
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


def test_plan_offers_build_tools_with_the_package_managers_own_commands(tmp_path):
    result, _home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--plan",), stub_tools=("apt-get",))

    plan = json.loads(result.stdout)
    tools = {item["id"]: item for item in plan["components"]}
    # No sudo and not root here, so the commands are shown as the person would type them.
    assert plan["privilege"] == "none"
    assert tools["python"]["available"] is True
    assert tools["python"]["targets"] == ["sudo apt-get update", "sudo apt-get install -y python3 python3-venv python3-pip"]
    assert tools["libpython"]["targets"][-1] == "sudo apt-get install -y python3-dev"
    assert tools["binutils"]["targets"][-1] == "sudo apt-get install -y binutils"


def test_build_tools_are_unsupported_without_a_known_package_manager(tmp_path):
    result, _home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--plan",))

    tools = {item["id"]: item for item in json.loads(result.stdout)["components"]}
    assert all(tools[name]["available"] is False and tools[name]["reason"] == "unsupported" for name in ("python", "libpython", "binutils"))


def test_build_tools_without_administrator_rights_are_reported_and_the_run_goes_on(tmp_path):
    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", shell="/bin/bash",
                        args=("--only", "binutils,path", "--events"), stub_tools=("apt-get",))

    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert {"event": "component", "id": "binutils", "status": "failed"} in events
    warning = next(e for e in events if e["event"] == "warning")
    assert "run it yourself: sudo apt-get update && sudo apt-get install -y binutils" in warning["message"]
    # The component after the failed one still ran.
    assert {"event": "component", "id": "path", "status": "done"} in events
    assert (home / ".bashrc").is_file()


def test_plan_offers_the_pinned_ffmpeg_for_each_supported_platform(tmp_path):
    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--plan",))
    ffmpeg = next(item for item in json.loads(result.stdout)["components"] if item["id"] == "ffmpeg")
    assert ffmpeg["available"] is True
    assert ffmpeg["targets"] == [
        "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/ffmpeg-linux-x64.gz",
        str(home / ".local" / "share" / "kvist" / "ffmpeg"),
    ]

    (tmp_path / "mac").mkdir()
    mac, _home = _run(tmp_path / "mac", "Darwin", "arm64", "kvist-macos-arm64", args=("--plan",))
    ffmpeg = next(item for item in json.loads(mac.stdout)["components"] if item["id"] == "ffmpeg")
    assert ffmpeg["available"] is True
    assert ffmpeg["targets"][0].endswith("/ffmpeg-darwin-arm64.gz")


def test_ffmpeg_that_does_not_match_the_pinned_checksum_is_refused_and_nothing_is_written(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "ffmpeg-linux-x64.gz").write_bytes(b"not the pinned file")

    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--only", "ffmpeg", "--events"))

    # ffmpeg only plays a preview: refusing it is a warning, not the end of the install.
    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert "does not match the pinned SHA-256" in next(e for e in events if e["event"] == "warning")["message"]
    assert {"event": "component", "id": "ffmpeg", "status": "failed"} in events
    assert not (home / ".local").exists()


# A curl that answers a probe (-w) with a speed by server and "downloads" a file that is not the
# pinned one, logging which server it was asked for.
_FAKE_CURL = r"""#!/bin/sh
url=""; out=""; probe=0
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out="$2"; shift ;;
    -w) probe=1; shift ;;
    -r|--connect-timeout|--max-time|--retry|--speed-limit|--speed-time) shift ;;
    -*) ;;
    *) url="$1" ;;
  esac
  shift
done
if [ "$probe" = 1 ]; then
  case "$url" in
    *github.com*) echo "206 100000" ;;
    *npmmirror*) echo "206 9000000" ;;
    *) echo "000 0" ;;
  esac
  exit 0
fi
echo "$url" >> "$KVIST_FAKE_LOG"
printf junk > "$out"
"""


def test_the_fastest_server_is_tried_first_and_a_file_that_does_not_match_goes_to_the_next(tmp_path):
    log = tmp_path / "downloads.log"
    result, _home = _run(
        tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--only", "ffmpeg", "--events"),
        scripts={"curl": _FAKE_CURL}, extra_env={"KVIST_FAKE_LOG": str(log)}, offline_assets=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert log.read_text().split() == [
        "https://cdn.npmmirror.com/binaries/ffmpeg-static/b6.0/ffmpeg-linux-x64.gz",
        "https://github.com/eugeneware/ffmpeg-static/releases/download/b6.0/ffmpeg-linux-x64.gz",
    ]
    events = [json.loads(line) for line in result.stdout.splitlines()]
    messages = [e["message"] for e in events if "message" in e]
    assert any("Downloading ffmpeg 6.0 from cdn.npmmirror.com (9.0 MB/s)" in m for m in messages)
    assert any("did not arrive intact; trying the next server" in m for m in messages)
    assert {"event": "component", "id": "ffmpeg", "status": "failed"} in events


def test_plan_offers_rust_with_a_compiler_package_and_rustup(tmp_path):
    result, _home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--plan",), stub_tools=("apt-get",))

    rust = next(item for item in json.loads(result.stdout)["components"] if item["id"] == "rust")
    if rust["reason"] == "present":
        pytest.skip("this machine already has cargo")
    assert rust["available"] is True
    assert rust["targets"][-1] == "curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh -s -- -y --profile minimal"


def test_rust_is_present_when_cargo_is_in_the_cargo_home(tmp_path):
    cargo_home = tmp_path / "cargo"
    (cargo_home / "bin").mkdir(parents=True)
    cargo = cargo_home / "bin" / "cargo"
    cargo.write_text("#!/bin/sh\n")
    cargo.chmod(0o755)

    result, _home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--plan",), extra_env={"CARGO_HOME": str(cargo_home)})

    rust = next(item for item in json.loads(result.stdout)["components"] if item["id"] == "rust")
    assert (rust["available"], rust["reason"]) == (False, "present")


def test_plan_offers_the_pinned_bun_beside_the_private_tools(tmp_path):
    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--plan",))
    bun = next(item for item in json.loads(result.stdout)["components"] if item["id"] == "bun")
    assert bun["available"] is True
    assert bun["targets"] == [
        "https://github.com/oven-sh/bun/releases/download/bun-v1.3.14/bun-linux-x64.zip",
        str(home / ".local" / "share" / "kvist" / "bun"),
    ]


def test_bun_that_does_not_match_the_pinned_checksum_is_refused_and_nothing_is_written(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "bun-linux-x64.zip").write_bytes(b"not the pinned file")

    result, home = _run(tmp_path, "Linux", "x86_64", "kvist-linux-x64", args=("--only", "bun", "--events"))

    assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in result.stdout.splitlines()]
    assert "does not match the pinned SHA-256" in next(e for e in events if e["event"] == "warning")["message"]
    assert {"event": "component", "id": "bun", "status": "failed"} in events
    assert not (home / ".local").exists()
