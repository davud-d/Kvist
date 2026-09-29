# -*- coding: utf-8 -*-
"""Apply the core suite's autouse isolation (home, session titles, write gate) here too."""
from __future__ import annotations

from kernel.tests.conftest import (  # noqa: F401 - pytest collects these fixtures by name
    _disable_session_titles,
    _disable_write_approval_gate,
    _isolate_home,
)
