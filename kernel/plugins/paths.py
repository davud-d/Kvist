# -*- coding: utf-8 -*-
"""Where installed skills, their grants and their per-workspace data live."""
from __future__ import annotations

from pathlib import Path

from kernel.user_config import get_user_config_path

SKILLS_SUBDIR = ("plugins", "skill")
WORKSPACE_STATE_DIR = ".kvist"


def config_dir() -> Path:
    """The user's Kvist configuration directory (``KVIST_CONFIG_DIR`` or ``~/.kvist``)."""
    return get_user_config_path().parent


def plugins_root() -> Path:
    return config_dir() / "plugins"


def skills_root() -> Path:
    return config_dir().joinpath(*SKILLS_SUBDIR)


def grants_path() -> Path:
    return plugins_root() / "grants.json"


def workspace_state(workspace: Path) -> Path:
    return Path(workspace) / WORKSPACE_STATE_DIR


def workspace_skill_dir(workspace: Path, skill_id: str) -> Path:
    """A skill's own data for one workspace; never shared between workspaces."""
    return workspace_state(workspace) / "skills" / skill_id
