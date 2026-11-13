# -*- coding: utf-8 -*-
"""Deferred imports for heavy third-party packages.

``openai`` and ``httpx`` take ~0.5 s to import and are only needed once a request
goes out, but the prompt, ``--help`` and every ``--*-op`` command load the modules
that mention them. ``lazy_import`` binds the name at once and runs the real import
on first attribute access.
"""
from __future__ import annotations

import importlib.util
import sys
from types import ModuleType


def lazy_import(name: str) -> ModuleType:
    loaded = sys.modules.get(name)
    if loaded is not None:
        return loaded
    spec = importlib.util.find_spec(name)
    if spec is None or spec.loader is None:
        raise ModuleNotFoundError(f"No module named {name!r}", name=name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    importlib.util.LazyLoader(spec.loader).exec_module(module)
    return module
