# -*- coding: utf-8 -*-
"""Machine providers; MACHINE_PROVIDER picks one."""
from __future__ import annotations

from ..config import Settings
from .base import MachineProvider, MachineSpec, ProviderError, ProviderState
from .local import LocalProvider
from .sprites import SpritesProvider

__all__ = ["MachineProvider", "MachineSpec", "ProviderError", "ProviderState", "make_provider"]


def make_provider(settings: Settings) -> MachineProvider:
    if settings.machine_provider == "local":
        return LocalProvider(
            settings.data_dir / "machines",
            settings.runner_command,
            settings.kvist_command,
            settings.heartbeat_seconds,
        )
    if settings.machine_provider == "sprites":
        return SpritesProvider()
    raise ProviderError(f"unknown MACHINE_PROVIDER: {settings.machine_provider}")
