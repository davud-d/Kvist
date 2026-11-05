# -*- coding: utf-8 -*-
"""Real hosting, not written yet. Implementing these five methods is all it takes to go live.

The runner on the machine needs only an outbound connection to ``spec.gateway_url`` and the token
from ``spec.token`` (pass it as a secret, never in a command line). Install the runner and Kvist in
the image, start ``kvist-runner --config <file>`` at boot, and size the machine from ``spec``.
"""
from __future__ import annotations

from .base import MachineSpec


class SpritesProvider:
    name = "sprites"

    async def create(self, spec: MachineSpec) -> str:
        raise NotImplementedError("TODO: create a machine from the runner image; return its id")

    async def wake(self, ref: str) -> None:
        raise NotImplementedError("TODO: start the machine; the runner dials the gateway itself")

    async def sleep(self, ref: str) -> None:
        raise NotImplementedError("TODO: stop the machine, keep its disk")

    async def delete(self, ref: str) -> None:
        raise NotImplementedError("TODO: destroy the machine and its disk")

    async def status(self, ref: str) -> str:
        raise NotImplementedError("TODO: map the provider's state to ProviderState")
