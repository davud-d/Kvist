"""Paramiko SSH transport with OpenSSH config, agent and explicit trust flow."""
from __future__ import annotations

import base64
import hashlib
import io
import json
import logging
import socket
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from ..protocol import AuthMethod, ServerSpec
from ..security import SecretStore


_LOG = logging.getLogger("kvist.web.ssh")


def _paramiko():
    try:
        import paramiko  # type: ignore
    except ImportError as error:
        raise RuntimeError(
            "SSH support needs Paramiko. Reinstall Kvist with its declared dependencies."
        ) from error
    return paramiko


@dataclass(slots=True)
class UnknownHostKey(Exception):
    host: str
    key_type: str
    fingerprint: str

    def __str__(self) -> str:
        return f"Host key for {self.host} is not trusted ({self.key_type} {self.fingerprint})."


def key_fingerprint(key) -> str:
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


class _TrustPolicy:
    def __init__(self, owner: "SSHTransport", expected: str | None) -> None:
        self.owner = owner
        self.expected = expected

    def missing_host_key(self, client, hostname, key) -> None:
        fingerprint = key_fingerprint(key)
        if not self.expected:
            raise UnknownHostKey(hostname, key.get_name(), fingerprint)
        if self.expected != fingerprint:
            raise RuntimeError("The SSH host key changed during the trust flow.")
        self.owner.save_host_key(hostname, key)
        client.get_host_keys().add(hostname, key.get_name(), key)


class SSHTransport:
    """One reconnectable SSH connection; no user value enters a shell command."""

    def __init__(self, spec: ServerSpec, secrets_store: SecretStore) -> None:
        self.spec = spec
        self.secrets = secrets_store
        self._client = None
        self._lock = threading.RLock()
        self._known_hosts = Path.home() / ".ssh" / "known_hosts"
        self._uploaded_artifacts: set[str] = set()

    def connect(self, *, trust_fingerprint: str | None = None) -> None:
        paramiko = _paramiko()
        with self._lock:
            _LOG.info(
                "opening SSH connection",
                extra={
                    "component": "ssh",
                    "server": self.spec.id,
                    "connection": "connecting",
                },
            )
            self.close()
            client = paramiko.SSHClient()
            client.load_system_host_keys()
            try:
                client.load_host_keys(str(self._known_hosts))
            except OSError:
                pass
            client.set_missing_host_key_policy(_TrustPolicy(self, trust_fingerprint))
            values = self._connection_values(paramiko)
            try:
                client.connect(**values)
            except UnknownHostKey:
                raise
            except (paramiko.AuthenticationException, paramiko.BadAuthenticationType) as error:
                _LOG.warning(
                    "SSH authentication rejected",
                    extra={
                        "component": "ssh",
                        "server": self.spec.id,
                        "connection": "failed",
                    },
                )
                raise RuntimeError(
                    f"SSH authentication failed for {self.spec.name}. The server rejected the configured credentials."
                ) from error
            except (paramiko.SSHException, OSError, socket.error) as error:
                raise RuntimeError(f"Could not connect to {self.spec.name}: {error}") from error
            self._client = client
            _LOG.info(
                "SSH connection established",
                extra={
                    "component": "ssh",
                    "server": self.spec.id,
                    "connection": "connected",
                },
            )

    def execute(self, command: str, *, stdin: str = "", timeout: float = 45.0) -> str:
        with self._lock:
            if self._client is None or not self._transport_active():
                self.connect()
            try:
                channel_in, channel_out, channel_error = self._client.exec_command(
                    command, timeout=timeout
                )
                if stdin:
                    channel_in.write(stdin)
                channel_in.channel.shutdown_write()
                output = channel_out.read().decode("utf-8", errors="replace")
                error = channel_error.read().decode("utf-8", errors="replace")
                status = channel_out.channel.recv_exit_status()
            except Exception:
                self.close()
                raise
            if status != 0:
                raise RuntimeError((error or output or f"Remote command exited {status}").strip())
            return output

    def upload_file(
        self,
        source: str | Path,
        *,
        remote_directory: str,
        permissions: int = 0o600,
    ) -> str:
        """Copy one local artifact to a private relative SFTP location.

        This deliberately accepts neither remote absolute paths nor traversal:
        callers can deploy a reviewed local file without interpolating a value
        into a remote shell command. A unique basename also makes concurrent
        controller bootstraps independent.
        """
        try:
            local = Path(source).expanduser().resolve(strict=True)
        except OSError as error:
            raise RuntimeError("Local upload artifact is unavailable.") from error
        if not local.is_file():
            raise ValueError("Local upload artifact must be a regular file.")
        directory = _safe_sftp_relative_path(remote_directory)
        filename = local.name
        if not filename or filename in {".", ".."}:
            raise ValueError("Local upload artifact has an invalid filename.")
        remote = f"{directory}/{uuid.uuid4().hex}-{filename}"
        temporary = f"{remote}.part"
        with self._lock:
            if self._client is None or not self._transport_active():
                self.connect()
            sftp = self._client.open_sftp()
            try:
                _sftp_mkdirs(sftp, directory)
                sftp.put(str(local), temporary, confirm=True)
                sftp.chmod(temporary, permissions)
                sftp.rename(temporary, remote)
                sftp.chmod(remote, permissions)
                # An SSH exec command is not required to start in the same
                # directory as SFTP. Return SFTP's canonical path so pip gets
                # the exact artifact on Unix and Windows alike.
                uploaded = sftp.normalize(remote)
            except Exception as error:
                for candidate in (temporary, remote):
                    try:
                        sftp.remove(candidate)
                    except Exception:
                        pass
                raise RuntimeError("Could not upload the remote bootstrap artifact.") from error
            finally:
                sftp.close()
            self._uploaded_artifacts.add(uploaded)
        return uploaded

    def remove_uploaded_file(self, remote_path: str) -> None:
        """Best-effort cleanup restricted to an artifact this transport uploaded.

        The in-memory capability set is intentional: cleanup cannot be used as
        a generic remote delete primitive, and no caller-controlled path is
        interpolated into a shell command.
        """
        with self._lock:
            if remote_path not in self._uploaded_artifacts:
                raise ValueError("Remote path is not a bootstrap artifact uploaded by this transport.")
            if self._client is None or not self._transport_active():
                self.connect()
            sftp = self._client.open_sftp()
            try:
                sftp.remove(remote_path)
            except Exception as error:
                raise RuntimeError("Could not remove the remote bootstrap artifact.") from error
            finally:
                sftp.close()
            self._uploaded_artifacts.discard(remote_path)

    def close(self) -> None:
        client, self._client = self._client, None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def save_host_key(self, hostname: str, key) -> None:
        paramiko = _paramiko()
        self._known_hosts.parent.mkdir(parents=True, exist_ok=True)
        hosts = paramiko.HostKeys()
        try:
            hosts.load(str(self._known_hosts))
        except OSError:
            pass
        hosts.add(hostname, key.get_name(), key)
        hosts.save(str(self._known_hosts))
        if self._known_hosts.exists():
            self._known_hosts.chmod(0o600)

    def _transport_active(self) -> bool:
        transport = self._client.get_transport() if self._client is not None else None
        return bool(transport and transport.is_active())

    def _connection_values(self, paramiko) -> dict[str, Any]:
        spec = self.spec
        lookup: dict[str, Any] = {}
        target = spec.ssh_alias or spec.host
        if spec.ssh_alias:
            config = paramiko.SSHConfig()
            path = Path.home() / ".ssh" / "config"
            try:
                with path.open(encoding="utf-8") as handle:
                    config.parse(handle)
                lookup = config.lookup(spec.ssh_alias)
            except OSError:
                lookup = {}
        host = str(lookup.get("hostname") or spec.host or target)
        port = int(lookup.get("port") or spec.port)
        username = str(lookup.get("user") or spec.username or "") or None
        identity_files = lookup.get("identityfile") or []
        key_filename = spec.private_key_path or (identity_files[0] if identity_files else None)
        values: dict[str, Any] = {
            "hostname": host,
            "port": port,
            "username": username,
            "timeout": 12,
            "banner_timeout": 12,
            "auth_timeout": 15,
            "allow_agent": spec.auth_method in {AuthMethod.CONFIG, AuthMethod.AGENT},
            "look_for_keys": spec.auth_method in {AuthMethod.CONFIG, AuthMethod.AGENT},
        }
        if key_filename:
            values["key_filename"] = str(Path(key_filename).expanduser())
        if spec.auth_method == AuthMethod.PASSWORD:
            password = self.secrets.get(f"server:{spec.id}:password")
            if not password:
                raise RuntimeError("SSH password is not available; edit the connection.")
            values["password"] = password
            values["allow_agent"] = False
            values["look_for_keys"] = False
        proxy = lookup.get("proxycommand")
        if proxy:
            values["sock"] = paramiko.ProxyCommand(proxy)
        return values


def _safe_sftp_relative_path(value: str) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or any(part in {"", ".", ".."} for part in path.parts)
    ):
        raise ValueError("Remote upload directory must be a relative safe path.")
    return "/".join(path.parts)


def _sftp_mkdirs(sftp, directory: str) -> None:
    """Create a relative SFTP directory tree without invoking a remote shell."""
    current = ""
    for part in directory.split("/"):
        current = part if not current else f"{current}/{part}"
        try:
            sftp.stat(current)
            continue
        except OSError:
            pass
        try:
            sftp.mkdir(current, mode=0o700)
        except OSError:
            # A concurrent bootstrap can create the same directory. Confirm it
            # now exists instead of treating that benign race as success blindly.
            sftp.stat(current)
