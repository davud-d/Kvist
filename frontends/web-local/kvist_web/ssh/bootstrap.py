"""Idempotent remote Kvist/bridge installation and version negotiation."""
from __future__ import annotations

from contextlib import contextmanager
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from kernel.version import kvist_version

from ..protocol import BRIDGE_PROTOCOL_VERSION, AuthMethod
from .transport import SSHTransport


_SAFE_VERSION = re.compile(r"^[0-9A-Za-z.+_-]+$")
_MINIMUM_PYTHON = (3, 11)


@dataclass(slots=True)
class BootstrapResult:
    os: str
    python: str
    installed: bool
    updated: bool
    bridge: dict

    def to_dict(self) -> dict:
        return {
            "os": self.os,
            "python": self.python,
            "installed": self.installed,
            "updated": self.updated,
            "bridge": self.bridge,
        }


class RemoteBootstrap:
    """Keep platform selection in one abstraction, not scattered call sites."""

    def __init__(
        self, transport: SSHTransport, *, run_setup: bool = False,
        on_log: Callable[[str, str], None] | None = None,
        setup_script: Path | None = None,
    ) -> None:
        self.transport = transport
        self.run_setup = run_setup
        self.on_log = on_log
        self.setup_script = setup_script or Path(__file__).with_name("setup_server.py")

    def _report(self, text: str) -> None:
        if self.on_log:
            self.on_log("status", text)

    def _execute_logged(self, command: str, *, timeout: float) -> str:
        if self.on_log:
            return self.transport.execute_stream(command, on_output=self.on_log, timeout=timeout)
        return self.transport.execute(command, timeout=timeout)

    def run(self) -> BootstrapResult:
        self._report("Checking remote Python…")
        facts = self._facts()
        self._report(f"Python {facts['python_version']} found on {facts['os']}.")
        if self.run_setup:
            self._run_setup(facts)
        self._report("Checking Kvist installation…")
        installed, current = self._installed_version(facts)
        wanted = kvist_version()
        updated = False
        if not installed or not self._compatible(current, wanted):
            self._install(facts, wanted)
            now_installed, now_current = self._installed_version(facts)
            if not now_installed or not self._compatible(now_current, wanted):
                raise RuntimeError(
                    "The remote artifact did not install the requested Kvist version. "
                    "The existing bridge was left untouched."
                )
            updated = True
        self._report("Starting remote bridge…")
        bridge = self._ensure_bridge(facts, wanted)
        if int(bridge.get("protocol_version", -1)) != BRIDGE_PROTOCOL_VERSION:
            raise RuntimeError(
                "Remote bridge protocol is incompatible. It was not stopped because "
                "it may own active tasks."
            )
        bridge_version = str(bridge.get("kvist_version", ""))
        if bridge_version != wanted and not bridge.get("version_deferred"):
            raise RuntimeError(
                "Remote bridge version is incompatible after bootstrap. "
                "It was not replaced automatically."
            )
        self._report("Remote bridge is ready.")
        return BootstrapResult(
            os=facts["os"],
            python=facts["python"],
            installed=installed,
            updated=updated,
            bridge=bridge,
        )

    def _run_setup(self, facts: dict[str, str]) -> None:
        if not self.setup_script.is_file():
            self._report("No custom setup script yet. Continuing standard setup.")
            return
        self._report("Uploading setup_server.py…")
        uploaded = self.transport.upload_file(self.setup_script, remote_directory=".kvist/web-bridge/setup")
        payload = ""
        try:
            payload = self._upload_setup_payload()
            self._report("Running setup_server.py on the server…")
            mode = self._setup_mode()
            command = (
                f"{quote_remote_arg(facts['python'], facts['os'])} -u "
                f"{quote_remote_arg(uploaded, facts['os'])} "
                f"--mode {quote_remote_arg(mode, facts['os'])} "
                f"--connection-json {quote_remote_arg(payload, facts['os'])}"
            )
            self._execute_logged(command, timeout=600)
            self._report("Custom setup completed.")
        finally:
            if payload:
                self._discard_uploaded_artifact(payload)
            self._discard_uploaded_artifact(uploaded)

    def _setup_mode(self) -> str:
        method = self.transport.spec.auth_method
        if method == AuthMethod.CONFIG:
            return "ssh-config"
        if method == AuthMethod.PASSWORD:
            return "password"
        if method == AuthMethod.KEY:
            return "key"
        if method == AuthMethod.AGENT:
            return "agent"
        return str(method).replace("_", "-")

    def _setup_payload(self) -> dict[str, Any]:
        spec = self.transport.spec
        mode = self._setup_mode()
        payload: dict[str, Any] = {
            "mode": mode,
            "server_id": spec.id,
            "name": spec.name,
            "host": spec.host,
            "port": spec.port,
            "username": spec.username,
            "ssh_alias": spec.ssh_alias,
            "auth_method": spec.auth_method,
        }
        if spec.auth_method == AuthMethod.PASSWORD:
            payload["password"] = self.transport.secrets.get(f"server:{spec.id}:password") or ""
        if spec.auth_method == AuthMethod.KEY:
            payload["private_key_path"] = spec.private_key_path
        return payload

    def _upload_setup_payload(self) -> str:
        data = json.dumps(self._setup_payload(), ensure_ascii=False, sort_keys=True).encode("utf-8")
        temporary: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                "wb",
                prefix="kvist-connection-",
                suffix=".json",
                delete=False,
            ) as handle:
                handle.write(data)
                temporary = Path(handle.name)
            return self.transport.upload_file(
                temporary,
                remote_directory=".kvist/web-bridge/setup",
                permissions=0o600,
            )
        finally:
            if temporary is not None:
                try:
                    temporary.unlink()
                except OSError:
                    pass

    def _facts(self) -> dict[str, str]:
        script = (
            "import json,platform,sys;"
            "print(json.dumps({'os':platform.system().lower(),'python':sys.executable,"
            "'version':list(sys.version_info[:3])}))"
        )
        last_error: Exception | None = None
        unsuitable: list[str] = []
        for executable in ("python3", "python", "py -3"):
            try:
                output = self.transport.execute(
                    f'{executable} -c "{script.replace(chr(34), chr(92) + chr(34))}"',
                    timeout=20,
                )
                value = json.loads(output.strip().splitlines()[-1])
                if not isinstance(value, dict) or not value.get("python"):
                    unsuitable.append(f"{executable}: invalid version response")
                    continue
                version = _python_version(value.get("version"))
                if version is None:
                    unsuitable.append(f"{executable}: could not determine Python version")
                    continue
                if version < _MINIMUM_PYTHON:
                    unsuitable.append(
                        f"{executable}: Python {'.'.join(str(part) for part in version)}"
                    )
                    continue
                return {
                    "os": str(value.get("os", "")),
                    "python": str(value["python"]),
                    "python_version": ".".join(str(part) for part in version),
                }
            except Exception as error:
                last_error = error
        detail = "; ".join(unsuitable)
        if last_error is not None:
            detail = "; ".join(part for part in (detail, str(last_error)) if part)
        raise RuntimeError(f"Remote Python 3.11+ was not found: {detail or 'no Python 3 interpreter responded'}")

    def _installed_version(self, facts: dict[str, str]) -> tuple[bool, str]:
        script = (
            "import json;"
            "from kernel.version import kvist_version;"
            "import kvist_web;"
            "print(json.dumps({'version':kvist_version()}))"
        )
        try:
            python = quote_remote_arg(facts["python"], facts["os"])
            output = self.transport.execute(
                f'{python} -c "{script.replace(chr(34), chr(92) + chr(34))}"',
                timeout=20,
            )
            return True, str(json.loads(output.strip().splitlines()[-1])["version"])
        except Exception:
            return False, ""

    def _install(self, facts: dict[str, str], version: str) -> None:
        if not _SAFE_VERSION.fullmatch(version):
            raise RuntimeError("Local Kvist version cannot be used for remote bootstrap.")
        self._report("Preparing Kvist package…")
        with _remote_wheel_artifact(version) as artifact:
            self._report("Uploading Kvist package…")
            remote_artifact = self.transport.upload_file(
                artifact,
                remote_directory=".kvist/web-bridge/artifacts",
            )
            python = quote_remote_arg(facts["python"], facts["os"])
            package = quote_remote_arg(remote_artifact, facts["os"])
            command = (
                f"{python} -m pip install --user --upgrade "
                f"{package}"
            )
            try:
                self._report("Installing Kvist…")
                self._execute_logged(command, timeout=300)
            except Exception as error:
                raise RuntimeError(
                    "Remote installation from the reviewed Kvist wheel failed. "
                    "The running bridge was not stopped."
                ) from error
            finally:
                self._discard_uploaded_artifact(remote_artifact)

    def _discard_uploaded_artifact(self, remote_artifact: str) -> None:
        """Do not leave release artifacts on a remote host after pip has read it.

        Older/custom transport adapters may not provide the cleanup capability;
        its absence or failure must never turn a completed install into a
        failure, and the transport itself restricts deletion to paths it
        uploaded during this connection.
        """
        cleanup = getattr(self.transport, "remove_uploaded_file", None)
        if not callable(cleanup):
            return
        try:
            cleanup(remote_artifact)
        except Exception:
            return

    def _ensure_bridge(self, facts: dict[str, str], wanted_version: str) -> dict:
        command = (
            f"{quote_remote_arg(facts['python'], facts['os'])} -m kvist_web.bridge_cli ensure "
            f"--expected-protocol {BRIDGE_PROTOCOL_VERSION} "
            f"--expected-version {quote_remote_arg(wanted_version, facts['os'])}"
        )
        output = self.transport.execute(command, timeout=40)
        value = json.loads(output.strip().splitlines()[-1])
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise RuntimeError("Remote bridge failed its startup check.")
        return value

    @staticmethod
    def _compatible(current: str, wanted: str) -> bool:
        # Exact builds are safest while the protocol is at v1. A stable future
        # protocol can loosen this to a declared compatibility range.
        return current == wanted


def _python_version(value: Any) -> tuple[int, int, int] | None:
    if not isinstance(value, (list, tuple)) or len(value) < 2:
        return None
    parts = value[:3]
    if not all(type(part) is int and part >= 0 for part in parts):
        return None
    major, minor = parts[:2]
    micro = parts[2] if len(parts) == 3 else 0
    return major, minor, micro


@contextmanager
def _remote_wheel_artifact(version: str):
    """Yield a verified local wheel without ever selecting a remote registry build.

    ``KVIST_REMOTE_WHEEL`` is the release path. A source checkout can also
    build the exact project into a temporary no-dependencies wheel, which
    keeps ordinary development runs working without quietly mutating a remote
    host from an unrelated package index. Installed and frozen applications
    must provide the explicit artifact.
    """
    configured = os.environ.get("KVIST_REMOTE_WHEEL", "").strip()
    if configured:
        try:
            artifact = Path(configured).expanduser().resolve(strict=True)
        except OSError as error:
            raise RuntimeError("KVIST_REMOTE_WHEEL does not name a readable local wheel.") from error
        _validate_wheel(artifact, version)
        yield artifact
        return

    source_root = _source_checkout_root()
    if source_root is None:
        raise RuntimeError(
            "Remote Kvist is missing or incompatible. Set KVIST_REMOTE_WHEEL "
            "to a local path for the matching Kvist wheel; an installed or frozen "
            "client refuses an implicit registry install to protect the remote host."
        )
    with tempfile.TemporaryDirectory(prefix="kvist-remote-wheel-") as temporary:
        artifact = _build_source_wheel(source_root, Path(temporary))
        _validate_wheel(artifact, version)
        yield artifact


def _source_checkout_root() -> Path | None:
    if getattr(sys, "frozen", False):
        return None
    for candidate in Path(__file__).resolve().parents:
        if (candidate / "pyproject.toml").is_file() and (candidate / "kernel").is_dir():
            return candidate
    return None


def _build_source_wheel(source_root: Path, staging: Path) -> Path:
    command = [
        sys.executable,
        "-m",
        "pip",
        "wheel",
        "--no-deps",
        "--no-build-isolation",
        "--wheel-dir",
        str(staging),
        str(source_root),
    ]
    try:
        subprocess.run(
            command,
            cwd=source_root,
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
            text=True,
            timeout=180,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise RuntimeError(
            "Could not build a local Kvist wheel for remote bootstrap. "
            "Set KVIST_REMOTE_WHEEL to a reviewed release artifact instead."
        ) from error
    wheels = sorted(staging.glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Local Kvist wheel build did not produce one wheel artifact.")
    return wheels[0]


def _validate_wheel(artifact: Path, expected_version: str) -> None:
    if not artifact.is_file() or artifact.suffix.casefold() != ".whl":
        raise RuntimeError("KVIST_REMOTE_WHEEL must name a local .whl file.")
    try:
        with zipfile.ZipFile(artifact) as wheel:
            metadata_name = next(
                name for name in wheel.namelist() if name.endswith(".dist-info/METADATA")
            )
            metadata = wheel.read(metadata_name).decode("utf-8", errors="replace")
    except (OSError, StopIteration, zipfile.BadZipFile) as error:
        raise RuntimeError("KVIST_REMOTE_WHEEL is not a valid Kvist wheel artifact.") from error
    fields = {
        key.casefold(): value.strip()
        for line in metadata.splitlines()
        if ":" in line
        for key, value in [line.split(":", 1)]
    }
    if fields.get("name", "").casefold().replace("-", "_") != "kvist":
        raise RuntimeError("KVIST_REMOTE_WHEEL does not contain the Kvist distribution.")
    if fields.get("version") != expected_version:
        raise RuntimeError(
            "KVIST_REMOTE_WHEEL version does not match this Kvist client; "
            "the remote installation was not changed."
        )


def quote_remote_arg(value: str, os_name: str) -> str:
    """Quote a remotely discovered executable for that host's command shell."""
    if os_name.casefold() == "windows":
        return subprocess.list2cmdline([value])
    return shlex.quote(value)
