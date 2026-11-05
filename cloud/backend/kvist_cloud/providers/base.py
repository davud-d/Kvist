# -*- coding: utf-8 -*-
"""What a hosting provider has to do. Connecting real hosting means one class with these five methods."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


class ProviderError(RuntimeError):
    pass


class ProviderState:
    RUNNING = "running"
    STOPPED = "stopped"
    MISSING = "missing"


@dataclass(frozen=True, slots=True)
class MachineSpec:
    machine_id: int
    vcpu: int
    memory_gb: int
    disk_gb: int
    gateway_url: str
    # The runner's credential for the gateway; given once, at create, and stored hashed by the server.
    token: str = field(repr=False)


class MachineProvider(Protocol):
    name: str

    async def create(self, spec: MachineSpec) -> str:
        """Create the machine with the runner installed; returns the provider's reference."""

    async def wake(self, ref: str) -> None:
        """Start the machine. The runner dials the gateway by itself once it is up."""

    async def sleep(self, ref: str) -> None:
        """Stop the machine, keeping its disk."""

    async def delete(self, ref: str) -> None:
        """Destroy the machine and its disk."""

    async def status(self, ref: str) -> str:
        """One of ProviderState."""
