# -*- coding: utf-8 -*-
from __future__ import annotations

import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
# kvist.spec leaves out the same dev-only entry scripts.
_NON_APP_MODULES = {"run_task", "package_script", "install", "make_mascot_frames"}


def test_pyproject_lists_every_top_level_module():
    """setuptools has no discovery for py-modules; a module missing here imports
    fine from a checkout and fails only in the installed package."""
    declared = set(tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["setuptools"]["py-modules"])
    on_disk = {path.stem for path in _ROOT.glob("*.py")} - _NON_APP_MODULES

    assert declared == on_disk
