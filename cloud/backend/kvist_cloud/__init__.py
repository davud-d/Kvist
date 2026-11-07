# -*- coding: utf-8 -*-
"""Kvist Cloud control server."""
from __future__ import annotations

import sys
from pathlib import Path

# The core is one directory up from the cloud. What the two share (the provider list, the ChatGPT sign-in's
# addresses) is read from it, not copied, so it has to be importable before any module here asks for it.
_REPO_ROOT = str(Path(__file__).resolve().parents[3])
if _REPO_ROOT not in sys.path:
    sys.path.append(_REPO_ROOT)
