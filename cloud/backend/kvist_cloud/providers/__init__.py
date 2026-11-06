# -*- coding: utf-8 -*-
"""Machine providers; MACHINE_PROVIDER picks one."""
from __future__ import annotations

from urllib.parse import urlparse

from ..config import LOOPBACK_HOSTS, Settings
from .base import MachineProvider, MachineSpec, ProviderError, ProviderState
from .local import LocalProvider
from .remote import RemoteProvider
from .sprites import SpritesProvider

__all__ = ["MachineProvider", "MachineSpec", "ProviderError", "ProviderState", "RemoteProvider", "make_provider"]


def make_provider(settings: Settings) -> MachineProvider:
    if settings.machine_provider == "local":
        if urlparse(settings.public_url).hostname not in LOOPBACK_HOSTS:
            raise ProviderError("The local provider has no tenant isolation and is restricted to loopback PUBLIC_URL")
        return LocalProvider(
            settings.data_dir / "machines",
            settings.runner_command,
            settings.kvist_command,
            settings.heartbeat_seconds,
        )
    if settings.machine_provider == "sprites":
        return SpritesProvider()
    raise ProviderError(f"unknown MACHINE_PROVIDER: {settings.machine_provider}")
