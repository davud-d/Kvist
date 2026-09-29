# -*- coding: utf-8 -*-
"""Single version-lookup point for the whole agent.

pyproject.toml is the one place the version number is written. When kvist is
installed (via pip, PyInstaller, or the CLI installer), :func:`kvist_version`
reads it back through ``importlib.metadata`` — the standard way an installed
distribution reports its own version, no re-parsing of build files needed.
Running straight from a source checkout (``python -m kernel``, the dev venv,
``run_task.py``) has no installed distribution to ask, so the same function
falls back to parsing ``pyproject.toml`` directly. Nothing else in the
codebase should hardcode a version string.
"""
from __future__ import annotations

import sys
from importlib.metadata import PackageNotFoundError, version as _installed_version
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11 is unsupported
    tomllib = None  # type: ignore[assignment]

_DISTRIBUTION_NAME = "kvist"
# A PyInstaller-frozen build has no installed distribution either; its
# bundled data root (kvist.spec ships pyproject.toml alongside the other
# datas) is sys._MEIPASS; from source it is the repository root above kernel/.
_PYPROJECT_PATH = Path(getattr(sys, "_MEIPASS", None) or Path(__file__).resolve().parent.parent) / "pyproject.toml"
_UNKNOWN_VERSION = "unknown"


def _version_from_pyproject() -> str:
    if tomllib is None:
        return _UNKNOWN_VERSION
    try:
        with _PYPROJECT_PATH.open("rb") as handle:
            data = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return _UNKNOWN_VERSION
    version = data.get("project", {}).get("version")
    return str(version) if version else _UNKNOWN_VERSION


def kvist_version() -> str:
    """The running kvist's version.

    Tries the installed distribution's metadata first; falls back to reading
    ``pyproject.toml`` directly for a source checkout that was never
    ``pip install``-ed (including a PyInstaller-frozen build, whose bundled
    ``pyproject.toml`` sits next to this module).
    """
    try:
        return _installed_version(_DISTRIBUTION_NAME)
    except PackageNotFoundError:
        return _version_from_pyproject()
