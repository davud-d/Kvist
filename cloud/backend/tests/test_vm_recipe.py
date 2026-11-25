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
