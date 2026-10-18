# -*- coding: utf-8 -*-
"""Makes the CUDA libraries that pip installs loadable for CTranslate2.

CTranslate2 wants libcublas.so.12 and libcudnn.so.9. A system with CUDA 13 does
not have them, and pip's nvidia-cublas-cu12 / nvidia-cudnn-cu12 put them in
site-packages/nvidia/*/lib, where the loader does not look. Loading them by path
first, globally, lets the later lookup by name find them. LD_LIBRARY_PATH cannot
do this from inside a running process.
"""
from __future__ import annotations

import ctypes
import glob
import logging
import os
import site

LIBRARIES = (
    "nvidia/cublas/lib/libcublasLt.so.12",
    "nvidia/cublas/lib/libcublas.so.12",
    "nvidia/cudnn/lib/libcudnn.so.9",
)


def preload() -> list[str]:
    """Load the libraries found in site-packages; return the paths loaded."""
    roots = site.getsitepackages() + [site.getusersitepackages()]
    loaded: list[str] = []
    for relative in LIBRARIES:
        for root in roots:
            for path in glob.glob(os.path.join(root, relative)):
                try:
                    ctypes.CDLL(path, mode=ctypes.RTLD_GLOBAL)
                except OSError as error:
                    logging.warning("Could not load %s: %s", path, error)
                else:
                    loaded.append(path)
    return loaded
