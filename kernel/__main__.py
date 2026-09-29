# -*- coding: utf-8 -*-
"""Entry point: ``python -m kernel`` or ``python kernel/__main__.py``.

Run as a file, Python puts ``kernel/`` itself first on the import path, which
would make every core module importable a second time under its bare name and
let ``kernel/parser.py`` or ``kernel/ui.py`` shadow unrelated modules, so the
repository root takes its place. ``kernel.main`` runs as ``__main__`` because it
rejects bad arguments and serves ``--help`` before importing provider SDKs.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

if not __package__ and not getattr(sys, "frozen", False):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

runpy.run_module("kernel.main", run_name="__main__", alter_sys=True)
