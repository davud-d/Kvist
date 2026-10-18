# -*- coding: utf-8 -*-
from __future__ import annotations

import ctypes
import logging


def trim_memory() -> None:
    """Hand memory the allocator kept after loading a model back to the system.

    Loading reads each model into RAM before it goes to the GPU (or is converted
    for the CPU); those copies are freed, but glibc keeps the pages, and the
    process would stay gigabytes larger.
    """
    try:
        ctypes.CDLL("libc.so.6").malloc_trim(0)
    except (OSError, AttributeError):  # not glibc: there is nothing to trim
        logging.debug("malloc_trim is not available")
