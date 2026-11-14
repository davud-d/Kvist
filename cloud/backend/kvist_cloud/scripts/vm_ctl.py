#!/usr/bin/python3 -I
# -*- coding: utf-8 -*-
"""Root-side helper of the libvirt provider: one JSON request in on stdin, one JSON answer out on stdout.

systemd runs it per connection of /run/kvist-vm.sock (deploy/systemd/kvist-vm.socket), which only the server's
user can open. The server itself stays unprivileged and hardened; this is the one door to the hypervisor, so it
trusts nothing in the request: every field is validated, and the only things it can touch are libvirt domains
named ``kvist-<number>`` and their two files.

    {"op": "create", "machine_id": 7, "vcpu": 2, "memory_gb": 4, "disk_gb": 20,
     "gateway_url": "wss://...", "token": "..."}
    {"op": "start" | "stop" | "delete" | "state", "machine_id": 7}

Answer: {"ok": true, "state": "running" | "stopped" | "missing"} or {"ok": false, "error": "..."}.
Standard library only: it runs under the system python3 with -I.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
IMAGES_DIR = Path("/var/lib/libvirt/images")
MAX_REQUEST_BYTES = 8192
CREATE_TIMEOUT_SECONDS = 1200
SHUTDOWN_WAIT_SECONDS = 60
VIRSH_TIMEOUT_SECONDS = 60
# The same ceilings as setup_vm.sh: this is a second line, in case the server is ever wrong.
MAX_VCPU, MAX_MEMORY_GB, MAX_DISK_GB = 4, 8, 150
OPS = ("create", "start", "stop", "delete", "state")


class Refused(Exception):
    """A request this helper will not carry out; the text goes back to the server."""


def number(request: dict, key: str, ceiling: int | None = None) -> int:
    value = request.get(key)
    # bool is an int in Python; "true" must not pass for 1.
    if not isinstance(value, int) or isinstance(value, bool) or value < 1 or (ceiling and value > ceiling):
        raise Refused(f"bad {key}")
    return value


def domain_of(request: dict) -> str:
    return f"kvist-{number(request, 'machine_id')}"


def virsh(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["virsh", *args], capture_output=True, text=True, timeout=VIRSH_TIMEOUT_SECONDS)
    if check and result.returncode != 0:
        raise Refused(f"virsh {args[0]} failed: {result.stderr.strip()[-300:]}")
    return result


def state_of(domain: str) -> str:
    listed = virsh("list", "--all", "--name").stdout.split()
    if domain not in listed:
        return "missing"
    shown = virsh("domstate", domain).stdout.strip()
    return "stopped" if shown in ("shut off", "crashed") else "running"


def create(request: dict) -> str:
    domain = domain_of(request)
    gateway = request.get("gateway_url")
    token = request.get("token")
    if not isinstance(gateway, str) or not gateway.startswith("wss://") or len(gateway) > 300:
        raise Refused("bad gateway_url")
    if not isinstance(token, str) or not 16 <= len(token) <= 200 or not token.isascii() or not token.isprintable():
        raise Refused("bad token")
    if state_of(domain) != "missing":
        raise Refused(f"{domain} already exists")
    args = [str(HERE / "setup_vm.sh"), "en", str(number(request, "machine_id")),
            str(number(request, "vcpu", MAX_VCPU)), str(number(request, "memory_gb", MAX_MEMORY_GB)),
            str(number(request, "disk_gb", MAX_DISK_GB)), gateway]
    # The token travels in the environment, never in argv (setup_vm.sh's own rule).
    env = {"PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C.UTF-8", "KVIST_MACHINE_TOKEN": token}
    for name in ("WHEELS_DIR", "SKEL_DIR"):
        if name in os.environ:
            env[name] = os.environ[name]
    result = subprocess.run(args, capture_output=True, text=True, timeout=CREATE_TIMEOUT_SECONDS, env=env)
    if result.returncode != 0:
        raise Refused(f"setup_vm.sh failed ({result.returncode}): {(result.stderr or result.stdout).strip()[-300:]}")
    return "running"


def start(request: dict) -> str:
    domain = domain_of(request)
    if state_of(domain) == "missing":
        raise Refused(f"{domain} does not exist")
    if state_of(domain) == "stopped":
        virsh("start", domain)
    return "running"


def stop(request: dict) -> str:
    domain = domain_of(request)
    if state_of(domain) != "running":
        return state_of(domain)
    virsh("shutdown", domain, check=False)
    deadline = time.monotonic() + SHUTDOWN_WAIT_SECONDS
    while time.monotonic() < deadline:
        if state_of(domain) != "running":
            return "stopped"
        time.sleep(2)
    # The guest ignored the request: its disk survives a hard stop, the runner restarts its tasks on wake.
    virsh("destroy", domain)
    return "stopped"


def delete(request: dict) -> str:
    domain = domain_of(request)
    if state_of(domain) != "missing":
        virsh("destroy", domain, check=False)
        # --nvram is needed for a UEFI guest and refused for a BIOS one: try the full form, then the plain one.
        if virsh("undefine", domain, "--nvram", "--managed-save", "--snapshots-metadata", check=False).returncode != 0:
            virsh("undefine", domain)
    for path in (IMAGES_DIR / f"{domain}.qcow2", IMAGES_DIR / f"{domain}-seed.iso"):
        path.unlink(missing_ok=True)
    return "missing"


def state(request: dict) -> str:
    return state_of(domain_of(request))


HANDLERS = {"create": create, "start": start, "stop": stop, "delete": delete, "state": state}


def handle(line: str) -> dict:
    try:
        request = json.loads(line)
    except json.JSONDecodeError:
        return {"ok": False, "error": "not json"}
    if not isinstance(request, dict) or request.get("op") not in OPS:
        return {"ok": False, "error": "unknown op"}
    try:
        return {"ok": True, "state": HANDLERS[request["op"]](request)}
    except Refused as error:
        return {"ok": False, "error": str(error)}
    except (OSError, subprocess.SubprocessError) as error:
        return {"ok": False, "error": f"{type(error).__name__}: {str(error)[-200:]}"}


def main() -> int:
    line = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
    if len(line) > MAX_REQUEST_BYTES:
        answer = {"ok": False, "error": "request too large"}
    else:
        answer = handle(line.decode("utf-8", errors="replace"))
    sys.stdout.write(json.dumps(answer) + "\n")
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
