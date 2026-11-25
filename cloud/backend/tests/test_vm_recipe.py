# -*- coding: utf-8 -*-
"""The guest recipe (setup_vm.sh) has to give every bundled Linux skill what its manifest asks for."""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
RECIPE = (ROOT / "cloud" / "backend" / "kvist_cloud" / "scripts" / "setup_vm.sh").read_text(encoding="utf-8")
BUNDLED = ROOT / "kernel" / "skills_bundled"

# The Debian 12 package that ships each program a manifest names; a new program has to be added here on purpose.
PACKAGE_OF = {"Xvfb": "xvfb", "i3": "i3-wm", "jwm": "jwm", "feh": "feh", "xclip": "xclip", "chromium": "chromium"}


def linux_manifests() -> list[dict]:
    manifests = [json.loads(path.read_text(encoding="utf-8")) for path in sorted(BUNDLED.glob("*/manifest.json"))]
    return [manifest for manifest in manifests if "linux" in manifest.get("platforms", [])]


def test_every_skill_program_comes_from_a_package_of_the_image():
    packages = set(re.findall(r"^  - ([a-z0-9][a-z0-9.+-]*)$", RECIPE, flags=re.MULTILINE))
    for manifest in linux_manifests():
        for program in manifest.get("dependencies", {}).get("system", []):
            assert program in PACKAGE_OF, f"{manifest['id']} needs {program}: say which package provides it"
            package = PACKAGE_OF[program]
            assert package in packages, f"{manifest['id']} needs {program}: {package} is not installed"


@pytest.mark.parametrize("manifest", [m for m in linux_manifests() if m.get("dependencies", {}).get("python")],
                         ids=lambda manifest: manifest["id"])
def test_every_skill_with_python_pins_gets_its_environment_in_the_image(manifest):
    assert f"--skill-op install-deps --skill {manifest['id']} --yes" in RECIPE


def test_sync_updates_running_vms_through_the_guest_agent_and_skips_stopped_ones(tmp_path):
    import os
    import subprocess

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "agent.log"
    (bin_dir / "virsh").write_text(
        "#!/bin/bash\n"
        'case "$1" in\n'
        "  list) printf 'kvist-1\\nkvist-2\\n\\n' ;;\n"
        '  domstate) [ "$2" = kvist-1 ] && echo running || echo "shut off" ;;\n'
        "  qemu-agent-command)\n"
        '    if [[ "$3" == *guest-exec-status* ]]; then\n'
        '      echo \'{"return":{"exited":true,"exitcode":0,"out-data":""}}\'\n'
        '    else echo "$3" >> "$FAKE_LOG"; echo \'{"return":{"pid":42}}\'; fi ;;\n'
        "esac\n",
        encoding="utf-8",
    )
    (bin_dir / "virsh").chmod(0o755)
    root_check = '[ "$(id -u)" -eq 0 ] || { echo "Run as root" >&2; exit 1; }'
    script = tmp_path / "setup_vm.sh"
    # The sync mode needs root; the copy only drops that check so the flow can run unprivileged.
    script.write_text(RECIPE.replace(root_check, "true", 1), encoding="utf-8")
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "FAKE_LOG": str(log)}

    result = subprocess.run(["bash", str(script), "sync"], capture_output=True, text=True, env=env, timeout=60)

    assert result.returncode == 0, result.stderr
    assert "kvist-2: stopped, skipped" in result.stdout
    requests = log.read_text(encoding="utf-8").splitlines()
    assert len(requests) == 1
    command = json.loads(requests[0])["arguments"]["arg"][1]
    assert "apt-get" in command and " feh " in command and " python3-tk " in command
    assert "--skill code_nav --yes" in command
