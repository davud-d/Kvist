# -*- coding: utf-8 -*-
"""Entry point: ``python -m kernel`` or ``python kernel/__main__.py``.

Run as a file, Python puts ``kernel/`` itself first on the import path, which
would make every core module importable a second time under its bare name and
let ``kernel/protocol/parser.py`` or ``kernel/protocol/ui.py`` shadow unrelated modules, so the
repository root takes its place. ``kernel.main`` runs as ``__main__`` because it
rejects bad arguments and serves ``--help`` before importing provider SDKs.
"""
from __future__ import annotations

import runpy
import sys
from pathlib import Path

if not __package__ and not getattr(sys, "frozen", False):
    sys.path[0] = str(Path(__file__).resolve().parent.parent)

_web_modules = Path(__file__).resolve().parent.parent / "frontends" / "web-local"
if _web_modules.is_dir() and str(_web_modules) not in sys.path:
    sys.path.insert(1, str(_web_modules))

# The status bar keeps these running for as long as it lives; they must not import the core.
if "--tray-op" in sys.argv and sys.argv[sys.argv.index("--tray-op") + 1 :][:1] in (["bar-stream"], ["toggle"]):
    from kernel.tray.tray_bar_client import main as _tray_bar_main

    raise SystemExit(_tray_bar_main(sys.argv))

if "--web-controller" in sys.argv:
    sys.argv.remove("--web-controller")
    from kvist_web.cli import main as _web_main

    _web_main()
    raise SystemExit

if "--web-bridge" in sys.argv:
    sys.argv.remove("--web-bridge")
    from kvist_web.bridge_cli import main as _bridge_main

    _bridge_main()
    raise SystemExit

runpy.run_module("kernel.main", run_name="__main__", alter_sys=True)
