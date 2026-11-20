# -*- coding: utf-8 -*-
"""What a plain plugin may import: ``import kvist``.

The whole API of a plugin's ``plugin.py`` is two functions, ``command()`` and ``run(request)``
(see docs/PLUGINS.md); this module only adds what is awkward to write by hand
inside the sandbox the plugin runs in.
"""
from __future__ import annotations

import os
from pathlib import Path


def read(name: str) -> str:
    """The text of a file in the plugin's folder, e.g. ``kvist.read("novibecode.md")``."""
    return (Path(os.environ["KVIST_PLUGIN_DIR"]) / name).read_text(encoding="utf-8")
