# -*- coding: utf-8 -*-
"""Kvist core: the agent, its action loop, executor, providers and sessions.

The classic terminal and the Zed ACP frontend are flat Python modules that live
beside the other frontends; the core imports them by module name, so their
directories are appended to the import path here. A PyInstaller build bundles
them as top-level modules, and a wheel installs them at the same relative paths.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _frontend in ("frontends/tui/classic", "frontends/zed"):
    _path = str(_REPO_ROOT / _frontend)
    if _path not in sys.path:
        sys.path.append(_path)
