"""Remote bridge lifecycle and the fixed SSH stdin/stdout proxy."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from kernel.version import kvist_version

from .bridge.client import (
    bridge_status,
    call_local_bridge,
    is_bridge_alive,
    probe_local_bridge,
    request_idle_shutdown,
)
from .bridge.service import bridge_home, descriptor_path, read_descriptor, serve
from .protocol import BRIDGE_PROTOCOL_VERSION


def _serve_command() -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, "--web-bridge", "serve"]
    return [sys.executable, "-m", "kvist_web.bridge_cli", "serve"]


_TERMINAL_PROCESS_STATES = frozenset({"idle", "finished", "failed", "stopped"})


def ensure(
    *,
    expected_protocol: int = BRIDGE_PROTOCOL_VERSION,
    expected_version: str | None = None,
) -> dict:
    """Start a bridge or reconcile an idle incompatible one safely.

    A detached bridge can own a real task after the local controller vanished.
    It is therefore never killed merely because a package was upgraded. A
    protocol-compatible, version-mismatched bridge stays available while work
    is active; an incompatible protocol produces a clear safe failure instead.
    """
    expected_version = expected_version or kvist_version()
    existing = _existing_bridge()
    if existing is not None:
        descriptor, probe = existing
        protocol = _advertised_protocol(descriptor, probe)
        if protocol != expected_protocol:
            raise RuntimeError(
                "Remote bridge protocol differs from this client and is still "
                "responding. No task was stopped and no replacement bridge was "
                "started; reconnect with a compatible Kvist version."
            )
        try:
            status = bridge_status()
        except Exception as error:
            raise RuntimeError(
                "The responding remote bridge could not complete current-protocol "
                "status negotiation. No task was stopped and no replacement bridge "
                "was started."
            ) from error
        if status.get("protocol_version") != expected_protocol:
            raise RuntimeError(
                "Remote bridge protocol differs from this client. No task was "
                "stopped and no replacement bridge was started."
            )
        version_matches = status.get("kvist_version") == expected_version
        if version_matches:
            return _public_descriptor(descriptor, reused=True, status=status)

        active = _active_processes(status.get("processes", []))
        if active:
            return _public_descriptor(
                descriptor,
                reused=True,
                status=status,
                version_deferred=True,
                active_processes=len(active),
            )

        # Only an idle bridge is asked to exit. The bridge itself closes the
        # send gate before accepting this request, preventing a task from
        # starting in the check-to-restart window.
        try:
            request_idle_shutdown()
        except Exception as error:
            raise RuntimeError(
                "An idle remote bridge is incompatible, but it cannot be safely "
                "restarted by this version. No task was stopped."
            ) from error
        _wait_for_bridge_stop()
    return _start_bridge()


def _existing_bridge() -> tuple[dict, dict] | None:
    """Return a responding descriptor, or prove it is safe to replace.

    A current ``BridgeRequest`` cannot ping a legacy protocol. Treat a raw
    response (including an error response) as proof that the descriptor still
    has an owner. If the endpoint is gone but its recorded PID might be alive,
    fail closed rather than overwriting the only reconnect path to a task.
    """
    path = descriptor_path()
    if not path.exists():
        return None
    try:
        descriptor = read_descriptor()
    except RuntimeError as error:
        if not path.exists():
            return None
        raise RuntimeError(
            "An existing remote bridge descriptor cannot be read safely. No "
            "replacement bridge was started."
        ) from error
    try:
        return descriptor, probe_local_bridge(descriptor)
    except Exception as error:
        if _descriptor_pid_may_be_alive(descriptor):
            raise RuntimeError(
                "An existing remote bridge descriptor may still belong to a live "
                "process, but it cannot be negotiated safely. No task was stopped "
                "and no replacement bridge was started."
            ) from error
        return None


def _advertised_protocol(descriptor: dict, response: dict) -> int:
    descriptor_protocol = descriptor.get("protocol_version")
    response_protocol = response.get("protocol_version", descriptor_protocol)
    if type(descriptor_protocol) is not int or type(response_protocol) is not int:
        raise RuntimeError(
            "Remote bridge did not report a valid protocol version. No replacement "
            "bridge was started."
        )
    if response_protocol != descriptor_protocol:
        raise RuntimeError(
            "Remote bridge descriptor and endpoint disagree about protocol version. "
            "No replacement bridge was started."
        )
    return response_protocol


def _descriptor_pid_may_be_alive(descriptor: dict) -> bool:
    """Use PID liveness only as a conservative last-resort ownership signal."""
    pid = descriptor.get("pid")
    if type(pid) is not int or pid <= 0:
        return True
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # On Windows and under restrictive service managers a live process can
        # be unprobeable. Err toward preserving its descriptor.
        return True
    return True


def _start_bridge() -> dict:
    home = bridge_home()
    home.mkdir(parents=True, exist_ok=True)
    log = (home / "bridge.log").open("ab", buffering=0)
    kwargs = {
        "stdin": subprocess.DEVNULL,
        "stdout": log,
        "stderr": subprocess.STDOUT,
        "cwd": str(Path.home()),
    }
    if not getattr(sys, "frozen", False):
        environment = os.environ.copy()
        package_root = str(Path(__file__).resolve().parent.parent)
        environment["PYTHONPATH"] = os.pathsep.join(
            filter(None, [package_root, environment.get("PYTHONPATH", "")])
        )
        kwargs["env"] = environment
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "DETACHED_PROCESS", 0)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        )
    else:
        kwargs["start_new_session"] = True
    process = subprocess.Popen(_serve_command(), **kwargs)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            descriptor = read_descriptor()
        except RuntimeError:
            descriptor = None
        if descriptor is not None and descriptor.get("pid") == process.pid and is_bridge_alive():
            return _public_descriptor(descriptor, reused=False)
        time.sleep(0.1)
    raise RuntimeError(f"Remote bridge did not start; inspect {home / 'bridge.log'}")


def _wait_for_bridge_stop() -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not is_bridge_alive():
            return
        time.sleep(0.05)
    raise RuntimeError(
        "The idle remote bridge did not stop in time. No task was stopped; "
        "retry after inspecting the remote bridge log."
    )


def _public_descriptor(
    descriptor: dict,
    *,
    reused: bool,
    status: dict | None = None,
    version_deferred: bool = False,
    active_processes: int = 0,
) -> dict:
    status = status or {}
    return {
        "ok": True,
        "pid": descriptor.get("pid"),
        "protocol_version": status.get("protocol_version", descriptor.get("protocol_version")),
        "kvist_version": status.get("kvist_version", descriptor.get("kvist_version")),
        "reused": reused,
        "version_deferred": version_deferred,
        "active_processes": active_processes,
    }


def _active_processes(value) -> list[dict]:
    if not isinstance(value, list):
        raise RuntimeError("Remote bridge returned an invalid process snapshot.")
    return [
        item
        for item in value
        if isinstance(item, dict)
        and str(item.get("state", "")) not in _TERMINAL_PROCESS_STATES
    ]


def rpc() -> None:
    payload = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    result = call_local_bridge(payload)
    print(json.dumps(result, ensure_ascii=False, separators=(",", ":")))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Kvist remote process bridge")
    parser.add_argument("--version", action="version", version=kvist_version())
    parser.add_argument("--expected-protocol", type=int)
    parser.add_argument("--expected-version")
    parser.add_argument("command", choices=("ensure", "serve", "rpc", "status"))
    args = parser.parse_args(argv)
    if args.command == "serve":
        serve()
    elif args.command == "rpc":
        rpc()
    elif args.command == "ensure":
        print(
            json.dumps(
                ensure(
                    expected_protocol=(
                        BRIDGE_PROTOCOL_VERSION
                        if args.expected_protocol is None
                        else args.expected_protocol
                    ),
                    expected_version=args.expected_version,
                ),
                separators=(",", ":"),
            )
        )
    else:
        payload = _public_descriptor(read_descriptor(), reused=True)
        payload["alive"] = is_bridge_alive()
        print(json.dumps(payload, separators=(",", ":")))


if __name__ == "__main__":
    main()
