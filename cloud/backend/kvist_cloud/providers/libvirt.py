# -*- coding: utf-8 -*-
"""Real hosting: a machine is a KVM virtual machine on this server, made by ``scripts/setup_vm.sh``.

The server runs unprivileged, so it never touches libvirt itself. It sends one JSON line per operation to
``/run/kvist-vm.sock``, where systemd starts ``scripts/vm_ctl.py`` as root (see deploy/systemd/kvist-vm.socket).
The runner inside the VM only dials out to the gateway with the token given at create; nothing connects in.
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from .base import MachineSpec, ProviderError, ProviderState

log = logging.getLogger("kvist_cloud.providers.libvirt")
SOCKET_PATH = Path("/run/kvist-vm.sock")
# First boot downloads the image once and installs the guest's packages; the rest are quick.
CREATE_TIMEOUT_SECONDS = 1300.0
CALL_TIMEOUT_SECONDS = 120.0
MAX_ANSWER_BYTES = 4096
STATES = {"running": ProviderState.RUNNING, "stopped": ProviderState.STOPPED, "missing": ProviderState.MISSING}


def reference(machine_id: int) -> str:
    """The domain name, also the provider reference stored in the database."""
    return f"kvist-{machine_id}"


def machine_id_of(ref: str) -> int:
    prefix, _, number = ref.partition("-")
    if prefix != "kvist" or not number.isdecimal():
        raise ProviderError(f"bad machine reference: {ref!r}")
    return int(number)


class LibvirtProvider:
    name = "libvirt"

    def __init__(self, socket_path: Path = SOCKET_PATH) -> None:
        self.socket_path = socket_path

    async def _call(self, request: dict, timeout: float = CALL_TIMEOUT_SECONDS) -> str:
        """One request, one answer. The text of an error never includes the request, which may carry a token."""
        try:
            return await asyncio.wait_for(self._exchange(request), timeout)
        except TimeoutError as error:
            raise ProviderError(f"{request['op']} timed out") from error
        except OSError as error:
            raise ProviderError(f"cannot reach the VM helper: {error.strerror or type(error).__name__}") from error

    async def _exchange(self, request: dict) -> str:
        reader, writer = await asyncio.open_unix_connection(str(self.socket_path))
        try:
            writer.write(json.dumps(request).encode("utf-8") + b"\n")
            await writer.drain()
            line = await reader.readline()
        finally:
            writer.close()
        try:
            answer = json.loads(line[:MAX_ANSWER_BYTES])
        except json.JSONDecodeError as error:
            raise ProviderError("the VM helper gave no answer") from error
        if not isinstance(answer, dict) or not answer.get("ok"):
            raise ProviderError(str(answer.get("error", "refused")) if isinstance(answer, dict) else "refused")
        return str(answer.get("state", ""))

    async def create(self, spec: MachineSpec) -> str:
        await self._call({"op": "create", "machine_id": spec.machine_id, "vcpu": spec.vcpu,
                          "memory_gb": spec.memory_gb, "disk_gb": spec.disk_gb, "gateway_url": spec.gateway_url,
                          "token": spec.token}, CREATE_TIMEOUT_SECONDS)
        log.info("machine %s created", spec.machine_id)
        return reference(spec.machine_id)

    async def wake(self, ref: str) -> None:
        await self._call({"op": "start", "machine_id": machine_id_of(ref)})

    async def sleep(self, ref: str) -> None:
        await self._call({"op": "stop", "machine_id": machine_id_of(ref)})

    async def delete(self, ref: str) -> None:
        await self._call({"op": "delete", "machine_id": machine_id_of(ref)})

    async def status(self, ref: str) -> str:
        state = await self._call({"op": "state", "machine_id": machine_id_of(ref)})
        if state not in STATES:
            raise ProviderError(f"unknown state {state!r}")
        return STATES[state]
