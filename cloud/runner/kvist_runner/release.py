# -*- coding: utf-8 -*-
"""A new release of Kvist and the runner, installed beside the running one and switched on by one pointer.

A release lives in ``<root>/releases/<id>/site`` (the two wheels unpacked there: both are pure Python, so
unpacking is what installing them does). Staging builds it
while tasks keep running on the old code; ``activate`` writes ``<root>/current-release``, and from then on the
runner (after it restarts) and every Kvist it starts put that ``site`` first on ``PYTHONPATH``. Dependencies are
the disk image's: a release that needs one the image lacks fails its import check and is never switched on.
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
import re
import shutil
import sys
import urllib.request
import zipfile
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .config import RunnerConfig

RELEASE_ID = re.compile(r"[0-9a-f]{16}")
WHEEL_NAME = re.compile(r"[\w.+-]+\.whl")
SHA256 = re.compile(r"[0-9a-f]{64}")
READY = "READY"
INSTALL_SECONDS = 300.0
DOWNLOAD_SECONDS = 120.0
# Imported with the release first on the path: what a task and the runner itself need to start.
SMOKE_CHECK = (
    "import sys, kernel, kernel.agent.agent, kvist_runner.runner as r;"
    "sys.exit(0 if kernel.__file__.startswith(sys.argv[1]) and r.__file__.startswith(sys.argv[1]) else 3)"
)


class ReleaseError(RuntimeError):
    """A release that cannot be staged or switched on; the message is what the server logs."""


def _pointer(config: RunnerConfig) -> Path:
    return config.root / "current-release"


def _dir(config: RunnerConfig, release: str) -> Path:
    return config.root / "releases" / release


def site_dir(config: RunnerConfig, release: str) -> Path:
    return _dir(config, release) / "site"


def is_ready(config: RunnerConfig, release: str) -> bool:
    return bool(RELEASE_ID.fullmatch(release)) and (_dir(config, release) / READY).is_file()


def current(config: RunnerConfig) -> str:
    """The release the pointer names, or empty (the image's own code) when there is none or it is not intact."""
    try:
        release = _pointer(config).read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    return release if is_ready(config, release) else ""


def overlay_env(config: RunnerConfig) -> dict[str, str]:
    release = current(config)
    return {"PYTHONPATH": str(site_dir(config, release))} if release else {}


def needs_restart(config: RunnerConfig) -> bool:
    """The pointer names a release this process is not running: after a reboot, the image's code starts first."""
    release = current(config)
    return bool(release) and str(site_dir(config, release)) not in sys.path


def activate(config: RunnerConfig, release: str) -> None:
    if not is_ready(config, release):
        raise ReleaseError(f"release {release} is not staged")
    temporary = _pointer(config).with_suffix(".new")
    temporary.write_text(release, encoding="utf-8")
    os.replace(temporary, _pointer(config))


def _base_url(gateway_url: str) -> str:
    parts = urlsplit(gateway_url)
    scheme = {"wss": "https", "ws": "http"}.get(parts.scheme, parts.scheme)
    return urlunsplit((scheme, parts.netloc, "", "", ""))


def _download(url: str, token: str, target: Path, sha256: str) -> None:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    digest = hashlib.sha256()
    with urllib.request.urlopen(request, timeout=DOWNLOAD_SECONDS) as response, open(target, "wb") as out:  # noqa: S310
        while chunk := response.read(1 << 20):
            digest.update(chunk)
            out.write(chunk)
    if digest.hexdigest() != sha256:
        raise ReleaseError(f"{target.name} does not match its checksum")


def _unpack(wheel: Path, site: Path) -> None:
    """A wheel into `site`. A name that leaves the folder, or a `.data` part (scripts, headers), is not accepted."""
    root = site.resolve()
    with zipfile.ZipFile(wheel) as archive:
        for member in archive.namelist():
            target = (site / member).resolve()
            if not target.is_relative_to(root) or ".data/" in member:
                raise ReleaseError(f"{wheel.name} holds {member!r}, which a release may not")
        archive.extractall(site)


async def _run(*argv: str, env: dict[str, str] | None = None) -> tuple[int, str]:
    process = await asyncio.create_subprocess_exec(
        *argv, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
        env=env, cwd="/",
    )
    try:
        output, _ = await asyncio.wait_for(process.communicate(), INSTALL_SECONDS)
    except TimeoutError:
        process.kill()
        await process.wait()
        raise ReleaseError(f"{Path(argv[0]).name} took longer than {INSTALL_SECONDS:.0f} s") from None
    return process.returncode or 0, output.decode("utf-8", "replace")[-400:]


def _checked_files(manifest: dict) -> list[dict]:
    release = str(manifest.get("release", ""))
    files = manifest.get("files")
    if not RELEASE_ID.fullmatch(release) or not isinstance(files, list) or not files:
        raise ReleaseError("the update message is malformed")
    for item in files:
        if not (isinstance(item, dict) and WHEEL_NAME.fullmatch(str(item.get("name", "")))
                and SHA256.fullmatch(str(item.get("sha256", "")))):
            raise ReleaseError("the update message names a file that is not a wheel")
    return files


async def stage(config: RunnerConfig, manifest: dict) -> str:
    """Download, verify, install beside the running code, and check it starts. Returns the release id."""
    files = _checked_files(manifest)
    release = str(manifest["release"])
    if is_ready(config, release):
        return release
    root = _dir(config, release)
    shutil.rmtree(root, ignore_errors=True)  # what an interrupted attempt left
    (root / "wheels").mkdir(parents=True)
    base = f"{_base_url(config.gateway_url)}/gateway/release/{release}"
    paths = []
    for item in files:
        target = root / "wheels" / str(item["name"])
        try:
            await asyncio.to_thread(_download, f"{base}/{item['name']}", config.token, target, str(item["sha256"]))
        except OSError as error:
            raise ReleaseError(f"download of {item['name']} failed: {error}") from error
        paths.append(str(target))
    site = site_dir(config, release)
    try:
        for path in paths:
            await asyncio.to_thread(_unpack, Path(path), site)
    except (OSError, zipfile.BadZipFile) as error:
        raise ReleaseError(f"unpacking failed: {error}") from error
    # Compiled now, so the first Kvist to start on the release does not pay for it (a failure only costs that time).
    await _run(sys.executable, "-m", "compileall", "-q", "-j", "0", str(site))
    env = {**os.environ, "PYTHONPATH": str(site)}
    code, output = await _run(sys.executable, "-c", SMOKE_CHECK, str(site), env=env)
    if code:
        raise ReleaseError(f"the release does not start: {output}")
    shutil.rmtree(root / "wheels", ignore_errors=True)
    (root / READY).write_text(release, encoding="utf-8")
    return release


def prune(config: RunnerConfig) -> None:
    """Drop every release but the one in use; each is a few hundred MB."""
    keep = current(config)
    with contextlib.suppress(OSError):
        for path in (config.root / "releases").iterdir():
            if path.name != keep:
                shutil.rmtree(path, ignore_errors=True)
