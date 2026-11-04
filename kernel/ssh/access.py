# -*- coding: utf-8 -*-
"""Getting signed in to a machine for the first time: trust its host key, and, for a password, turn it into a key.

Everything here runs from the machines dialog in the person's terminal. A password lives only in the environment of
one ssh child (read by a tiny SSH_ASKPASS helper) and is never written anywhere; what stays is a key Kvist made for
this machine. After that every connection is by key with no questions, which is the only way Kvist connects.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kernel.config.user_config import get_user_config_path
from kernel.ssh.command import Target, classify, ssh_prefix
from kernel.ssh.paths import RemoteError

Run = Callable[..., subprocess.CompletedProcess]
_SECONDS = 30
_PREFERRED_KEYS = ("ssh-ed25519", "ecdsa-sha2-nistp256", "ssh-rsa")


def _run(command: list[str], **options) -> subprocess.CompletedProcess:
    return subprocess.run(command, capture_output=True, encoding="utf-8", errors="replace", timeout=_SECONDS, check=False, **options)


def key_directory() -> Path:
    return get_user_config_path().parent / "ssh"


def key_path(name: str) -> Path:
    return key_directory() / name


def known_hosts() -> Path:
    return Path.home() / ".ssh" / "known_hosts"


@dataclass(frozen=True, slots=True)
class Endpoint:
    hostname: str
    port: int


def resolve_endpoint(host: str, port: int | None, run: Run = _run) -> Endpoint:
    """Where ssh would really go: a name from ~/.ssh/config stands for another host and maybe another port."""
    name = host.rsplit("@", 1)[-1]
    done = run(["ssh", "-G", *(["-p", str(port)] if port else []), "--", host])
    found = dict(line.split(" ", 1) for line in done.stdout.splitlines() if " " in line) if done.returncode == 0 else {}
    real_port = int(found["port"]) if found.get("port", "").isdigit() else (port or 22)
    return Endpoint(found.get("hostname", name), real_port)


def host_is_known(endpoint: Endpoint, run: Run = _run) -> bool:
    name = endpoint.hostname if endpoint.port == 22 else f"[{endpoint.hostname}]:{endpoint.port}"
    if not known_hosts().exists():
        return False
    return run(["ssh-keygen", "-F", name, "-f", str(known_hosts())]).returncode == 0


@dataclass(frozen=True, slots=True)
class ScannedKey:
    lines: tuple[str, ...]  # known_hosts lines, as ssh-keyscan wrote them
    fingerprint: str  # of the one shown to the person, e.g. ``SHA256:…``
    kind: str


def scan_host_key(endpoint: Endpoint, run: Run = _run) -> ScannedKey:
    """What the machine says its key is; the person decides whether to believe it."""
    done = run(["ssh-keyscan", "-T", "10", "-p", str(endpoint.port), "--", endpoint.hostname])
    lines = tuple(line for line in done.stdout.splitlines() if line.strip() and not line.startswith("#"))
    if not lines:
        raise RemoteError(f"{endpoint.hostname}:{endpoint.port} did not answer with a host key; is the address right and ssh running there?")
    by_kind = {line.split()[1]: line for line in lines if len(line.split()) >= 3}
    kind = next((name for name in _PREFERRED_KEYS if name in by_kind), next(iter(by_kind)))
    shown = run(["ssh-keygen", "-lf", "-"], input=by_kind[kind] + "\n")
    parts = shown.stdout.split()
    if shown.returncode or len(parts) < 2:
        raise RemoteError("ssh-keygen could not read the host key.")
    return ScannedKey(lines, parts[1], kind)


def trust_host_key(scanned: ScannedKey) -> None:
    """Add the key to ~/.ssh/known_hosts, where ssh itself looks, after the person confirmed the fingerprint."""
    path = known_hosts()
    path.parent.mkdir(mode=0o700, exist_ok=True)
    with open(path, "a", encoding="utf-8") as handle:
        handle.write("\n".join(scanned.lines) + "\n")
    path.chmod(0o600)


@dataclass(frozen=True, slots=True)
class Login:
    ok: bool
    problem: str  # the kind from ``classify`` when it did not work
    detail: str


def check_login(target: Target, run: Run = _run) -> Login:
    """Can ssh get in right now without anything being typed?"""
    done = run([*ssh_prefix(target), "true"])
    if done.returncode == 0:
        return Login(True, "", "")
    failure = classify(done.returncode, done.stderr, shim_state=None)
    return Login(False, failure.kind, done.stderr.strip().splitlines()[-1] if done.stderr.strip() else "")


def _private_key_directory() -> Path:
    directory = key_directory()
    directory.mkdir(parents=True, mode=0o700, exist_ok=True)
    return directory


def _askpass_helper() -> Path:
    helper = _private_key_directory() / "askpass.sh"
    if not helper.exists():
        helper.write_text('#!/bin/sh\nprintf \'%s\\n\' "$KVIST_SSH_PASSWORD"\n', encoding="utf-8")
    helper.chmod(0o700)
    return helper


def _make_key(name: str, run: Run) -> Path:
    _private_key_directory()
    path = key_path(name)
    if path.exists():
        return path
    done = run(["ssh-keygen", "-t", "ed25519", "-N", "", "-C", f"kvist-{name}", "-f", str(path)])
    if done.returncode:
        raise RemoteError(f"ssh-keygen failed: {done.stderr.strip()}")
    return path


def _password_ssh_command(target: Target) -> list[str]:
    """ssh that is allowed to ask for a password, but only once and only for a password."""
    program = os.environ.get("KVIST_SSH") or "ssh"
    options = [
        "-T", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "-o", "NumberOfPasswordPrompts=1",
        "-o", "PubkeyAuthentication=no", "-o", "PreferredAuthentications=password,keyboard-interactive",
    ]
    if target.port:
        options += ["-p", str(target.port)]
    install = "umask 077; mkdir -p ~/.ssh; cat >> ~/.ssh/authorized_keys"
    return [program, *options, "--", target.host, "sh -c " + shlex.quote(install)]


def install_key_with_password(name: str, target: Target, password: str, run: Run = _run) -> str:
    """Make a key for ``name``, put its public half on the machine using ``password``, prove the key works; return its path.

    Nothing is left behind when it does not work.
    """
    created = not key_path(name).exists()
    key = _make_key(name, run)
    try:
        _copy_public_key(key, target, password, run)
        with_key = Target(target.host, target.port, target.workspace, str(key))
        result = check_login(with_key, run)
        if not result.ok:
            raise RemoteError(f"The key was installed but signing in with it failed: {result.detail or result.problem}")
    except RemoteError:
        if created:
            for leftover in (key, Path(f"{key}.pub")):
                leftover.unlink(missing_ok=True)
        raise
    return str(key)


def _copy_public_key(key: Path, target: Target, password: str, run: Run) -> None:
    public = Path(f"{key}.pub").read_text(encoding="utf-8")
    environment = {
        **os.environ,
        "SSH_ASKPASS": str(_askpass_helper()),
        "SSH_ASKPASS_REQUIRE": "force",
        "KVIST_SSH_PASSWORD": password,
        # Older ssh only calls the helper when it thinks a screen exists.
        "DISPLAY": os.environ.get("DISPLAY", "kvist:0"),
    }
    done = run(_password_ssh_command(target), input=public, env=environment)
    if done.returncode:
        reason = re.sub(r"\s+", " ", done.stderr).strip() or f"ssh exited with {done.returncode}"
        raise RemoteError(f"Could not sign in with that password: {reason}")


def usable_key_file(path: str) -> str | None:
    """Why a key file cannot be used, or None when it can."""
    file = Path(path).expanduser()
    if not file.is_file():
        return f"{file} is not a file."
    if not os.access(file, os.R_OK):
        return f"{file} cannot be read."
    if file.stat().st_mode & 0o077:
        return f"{file} is open to other users; ssh refuses such a key (chmod 600)."
    return None


def tools_missing() -> list[str]:
    return [tool for tool in ("ssh", "ssh-keyscan", "ssh-keygen") if shutil.which(tool) is None]
