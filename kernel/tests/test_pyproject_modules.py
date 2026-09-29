# -*- coding: utf-8 -*-
from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
# The Python and data the installed app reads at runtime; tests stay out.
_RUNTIME_ROOTS = ("kernel", "frontends/tui/classic", "frontends/zed")


def _declared_packages() -> set[str]:
    pyproject = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    return set(pyproject["tool"]["setuptools"]["packages"])


def _runtime_directories() -> set[str]:
    tracked = subprocess.run(
        ["git", "ls-files", *_RUNTIME_ROOTS],
        cwd=_ROOT, capture_output=True, text=True, check=True,
    ).stdout.split()
    parents = {Path(name).parent for name in tracked}
    return {".".join(parent.parts) for parent in parents if "tests" not in parent.parts}


def test_pyproject_lists_every_runtime_directory():
    """setuptools installs only the listed directories; one missing here works
    from a checkout and fails only in the installed package."""
    assert _runtime_directories() <= _declared_packages()


def test_pyproject_ships_no_test_directory():
    assert not any("tests" in name.split(".") for name in _declared_packages())
