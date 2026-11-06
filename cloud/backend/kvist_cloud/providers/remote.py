# -*- coding: utf-8 -*-
"""The customer's own machine (the Remote plan): there is nothing for us to create, wake or stop.

Its runner dials the gateway by itself whenever the owner runs it; until then the machine is simply offline.
"""
from __future__ import annotations

from .base import MachineSpec, ProviderState


class RemoteProvider:
    name = "remote"

    async def create(self, spec: MachineSpec) -> str:
        return f"remote-{spec.machine_id}"

    async def wake(self, ref: str) -> None:
        """Nothing to start from here; the runner connecting is what brings it up."""

    async def sleep(self, ref: str) -> None:
        """Nothing to stop: the owner's computer is the owner's."""

    async def delete(self, ref: str) -> None:
        """Nothing is left on our side but the row."""

    async def status(self, ref: str) -> str:
        return ProviderState.STOPPED
