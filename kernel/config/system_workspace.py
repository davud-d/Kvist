# -*- coding: utf-8 -*-
"""The system folder: Kvist's own workspace for requests about the person's computer, not about a project.

It is the folder Kvist opens when started without one (from the home directory, a filesystem root or a
desktop launcher). Kvist recognises it by its location alone, so nothing written inside it can turn an
ordinary project into one, and nothing written elsewhere can claim its permissions (sudo).
"""
from __future__ import annotations

from pathlib import Path

from kernel.config.user_config import get_user_config_path

SYSTEM_WORKSPACE_DIRNAME = "workspace"


def system_workspace_path() -> Path:
    """``~/.kvist/workspace``, or ``workspace`` beside the config KVIST_CONFIG_DIR points at."""
    return get_user_config_path().parent / SYSTEM_WORKSPACE_DIRNAME


def is_system_workspace(workspace: str | Path) -> bool:
    try:
        return Path(workspace).resolve() == system_workspace_path().resolve()
    except (OSError, RuntimeError):
        return False
