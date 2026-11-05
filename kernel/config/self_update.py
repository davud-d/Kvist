# -*- coding: utf-8 -*-
"""Update of an installed ``kvist-core`` from the rolling ``main`` release.

A background check only *announces* a new build ("type /update"); it never downloads or
replaces anything. The person installs it with ``/update`` (``run_update``), when it suits them.

The release ``latest-plugin-build`` is rebuilt only from ``main``, so following it *is*
following ``main``. Only a binary placed by the release installer (frozen, named
``kvist-core``) updates itself. A source checkout, whatever branch it sits on, a
PyInstaller build in ``dist/`` and a copy under another name are never touched: they have
no business being replaced by a build of a different branch.

A new version is recognised by the SHA-256 GitHub publishes for each release asset, so a
platform whose binary was not rebuilt (the macOS ones lag) is not "updated" to itself.
The replacement is downloaded beside the installed file, verified against that digest and
run once with ``--help`` before it is moved into place; nothing changes until all of that
passed, and only then the person is told to restart.
"""
from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path

import httpx

from kernel.config.user_config import get_user_config_path, load_user_config

RELEASE_REPO = "davud-d/Kvist"
RELEASE_TAG = "latest-plugin-build"
RELEASE_API = f"https://api.github.com/repos/{RELEASE_REPO}/releases/tags/{RELEASE_TAG}"
DOWNLOAD_BASE = f"https://github.com/{RELEASE_REPO}/releases/download/{RELEASE_TAG}"

DISABLE_ENV = "KVIST_AUTO_UPDATE"
CONFIG_KEY = "auto_update"
CHECK_INTERVAL_SECONDS = 6 * 3600
# Several cores can start at once (an IDE and a terminal); one check per hour is plenty.
MIN_GAP_SECONDS = 3600
STATE_FILENAME = "update.json"
_CORE_NAMES = ("kvist-core", "kvist-core.exe")

_ASSETS = {
    ("linux", "x86_64"): "kvist-linux-x64",
    ("linux", "aarch64"): "kvist-linux-arm64",
    ("linux", "arm64"): "kvist-linux-arm64",
    ("darwin", "arm64"): "kvist-macos-arm64",
    ("darwin", "x86_64"): "kvist-macos-x64",
    ("win32", "amd64"): "kvist-windows-x64.exe",
    ("win32", "x86_64"): "kvist-windows-x64.exe",
}

# One checker per process; a rebuilt agent (new provider) only repoints where the news goes.
_started = False
_listeners: tuple[Callable[[str], None], Callable[[str], None]] | None = None


class UpdateError(RuntimeError):
    """A check or download failed; the installed binary is unchanged."""


def auto_update_enabled() -> bool:
    """Whether the background check runs; on by default. ``auto_update: false`` in config.json or
    ``KVIST_AUTO_UPDATE=0`` turns the announcement off (``/update`` keeps working)."""
    if os.environ.get(DISABLE_ENV, "").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return load_user_config().get(CONFIG_KEY) is not False


def release_asset_name() -> str | None:
    return _ASSETS.get((sys.platform, platform.machine().lower()))


def installed_core() -> Path | None:
    """The running binary when it is the installer's ``kvist-core`` in a writable folder, else None."""
    if not getattr(sys, "frozen", False):
        return None
    path = Path(sys.executable).resolve()
    if path.name not in _CORE_NAMES or not os.access(path.parent, os.W_OK):
        return None
    return path


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def fetch_release_digests() -> dict[str, str]:
    """Asset name -> SHA-256 of the rolling release, as GitHub reports them."""
    try:
        response = httpx.get(RELEASE_API, timeout=20, headers={"Accept": "application/vnd.github+json"})
        response.raise_for_status()
        assets = response.json().get("assets", [])
    except (httpx.HTTPError, ValueError) as error:
        raise UpdateError(f"release lookup failed: {error}") from error
    digests: dict[str, str] = {}
    for asset in assets:
        digest = str(asset.get("digest") or "")
        if digest.startswith("sha256:"):
            digests[str(asset.get("name"))] = digest.removeprefix("sha256:")
    return digests


def _download(asset: str, target: Path) -> None:
    try:
        with httpx.stream("GET", f"{DOWNLOAD_BASE}/{asset}", timeout=60, follow_redirects=True) as response:
            response.raise_for_status()
            with target.open("wb") as handle:
                for chunk in response.iter_bytes(1 << 20):
                    handle.write(chunk)
    except (httpx.HTTPError, OSError) as error:
        raise UpdateError(f"download failed: {error}") from error


def _runs(binary: Path) -> bool:
    # The installer does the same check: a binary built against a newer C library than this
    # system has must not replace one that works.
    try:
        done = subprocess.run([str(binary), "--help"], capture_output=True, timeout=60, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _put_in_place(new: Path, core: Path) -> None:
    try:
        os.replace(new, core)
        return
    except PermissionError:
        if sys.platform != "win32":
            raise
    # Windows cannot overwrite a running exe but can rename it away.
    old = core.with_name(core.name + ".old")
    old.unlink(missing_ok=True)
    os.replace(core, old)
    os.replace(new, core)


def update_available(core: Path, asset: str, digests: dict[str, str]) -> bool:
    """True when the release carries a build of ``asset`` that differs from the installed ``core``."""
    wanted = digests.get(asset)
    return wanted is not None and sha256_file(core) != wanted


def update_core(core: Path, asset: str, digests: dict[str, str]) -> bool:
    """Replace ``core`` with the release build when it differs. True when it was replaced."""
    if not update_available(core, asset, digests):
        return False
    wanted = digests[asset]
    staging = core.with_name(f".{core.stem}.update")
    try:
        _download(asset, staging)
        if sha256_file(staging) != wanted:
            raise UpdateError("the downloaded file does not match the published SHA-256")
        staging.chmod(0o755)
        if not _runs(staging):
            raise UpdateError("the downloaded build does not start on this system")
        _put_in_place(staging, core)
    except OSError as error:
        raise UpdateError(f"could not install the update: {error}") from error
    finally:
        staging.unlink(missing_ok=True)
    return True


def _state_path() -> Path:
    return get_user_config_path().parent / STATE_FILENAME


def _last_check() -> float:
    try:
        return float(json.loads(_state_path().read_text(encoding="utf-8")).get("last_check", 0))
    except (OSError, ValueError, AttributeError):
        return 0.0


def _remember_check() -> None:
    path = _state_path()
    temporary = path.with_name(path.name + ".tmp")
    try:
        temporary.write_text(json.dumps({"last_check": time.time()}), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass  # only costs one extra check at the next start


def _russian() -> bool:
    locale = (os.environ.get("LC_ALL") or os.environ.get("LANG") or "").lower()
    return locale.startswith("ru")


def available_notice() -> str:
    if _russian():
        return "Доступна новая версия Kvist. Введи /update, чтобы установить её."
    return "A new version of Kvist is available. Type /update to install it."


def run_update(russian: bool) -> tuple[bool, str]:
    """The ``/update`` command: install the newest build now. Returns ``(ok, message)``."""
    core = installed_core()
    asset = release_asset_name()
    if core is None or asset is None:
        return False, (
            "Это не установленная сборка kvist-core (или папка недоступна для записи): обновляй исходники через git."
            if russian
            else "This is not an installed kvist-core build (or its folder is not writable): update a source checkout with git."
        )
    try:
        replaced = update_core(core, asset, fetch_release_digests())
    except UpdateError as error:
        return False, f"[update] {error}"
    if replaced:
        return True, (
            "Kvist обновлён. Перезапусти его, чтобы получить новую версию."
            if russian
            else "Kvist has been updated. Restart it to run the new version."
        )
    return True, "Уже последняя версия." if russian else "Already up to date."


def _loop(core: Path, asset: str) -> None:
    wait = max(0.0, _last_check() + MIN_GAP_SECONDS - time.time())
    while True:
        time.sleep(wait)
        wait = CHECK_INTERVAL_SECONDS
        try:
            found = update_available(core, asset, fetch_release_digests())
        except (UpdateError, OSError) as error:
            _listeners[1](f"[update] {error}")
            continue
        finally:
            _remember_check()
        if found:
            _listeners[0](available_notice())
            return


def start(notify: Callable[[str], None], debug: Callable[[str], None]) -> None:
    """Begin checking in a daemon thread unless this process is not an updatable install."""
    global _started, _listeners
    _listeners = (notify, debug)
    core = installed_core()
    asset = release_asset_name()
    if _started or core is None or asset is None or not auto_update_enabled():
        return
    _started = True
    threading.Thread(target=_loop, args=(core, asset), name="kvist-update", daemon=True).start()
