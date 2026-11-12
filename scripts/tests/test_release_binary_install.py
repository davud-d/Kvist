# -*- coding: utf-8 -*-
"""scripts/release/binary/install.sh against a local file:// release, with a shell script as the binary."""
from __future__ import annotations

import hashlib
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "release" / "binary" / "install.sh"

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not shutil.which("bash") or not shutil.which("curl"),
    reason="needs bash and curl",
)

FAKE_BINARY = b'#!/bin/sh\n[ "$1" = "--version" ] && { echo "kvist 0.0-test"; exit 0; }\necho "ran: $*"\n'


def _asset_name() -> str:
    system = {"Linux": "linux", "Darwin": "macos"}.get(platform.system())
    machine = {"x86_64": "x64", "AMD64": "x64", "aarch64": "arm64", "arm64": "arm64"}.get(platform.machine())
    if system is None or machine is None:
        pytest.skip("the installer has no binary for this system")
    return f"kvist-{system}-{machine}"


def _release(directory: Path, *, corrupt_sum: bool = False) -> str:
    directory.mkdir()
    asset = _asset_name()
    (directory / asset).write_bytes(FAKE_BINARY)
    digest = "0" * 64 if corrupt_sum else hashlib.sha256(FAKE_BINARY).hexdigest()
    # Another platform's line first: only this system's own line may be compared.
    (directory / "SHA256SUMS").write_text(f"{'1' * 64}  kvist-other-os\n{digest}  {asset}\n", encoding="utf-8")
    return directory.as_uri()


def _install(tmp_path: Path, release_url: str) -> subprocess.CompletedProcess[str]:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    environment = {
        "HOME": str(home), "PATH": os.environ["PATH"], "TMPDIR": str(tmp_path),
        "KVIST_RELEASE_URL": release_url, "KVIST_INSTALL_NO_RUN": "1",
    }
    return subprocess.run(["bash", str(SCRIPT)], env=environment, capture_output=True, text=True, timeout=60)


def test_installs_the_binary_and_the_launcher(tmp_path):
    result = _install(tmp_path, _release(tmp_path / "release"))

    assert result.returncode == 0, result.stderr
    core = tmp_path / "home" / ".local" / "share" / "kvist" / "kvist-core"
    launcher = tmp_path / "home" / ".local" / "bin" / "kvist"
    assert core.read_bytes() == FAKE_BINARY and os.access(core, os.X_OK)
    run = subprocess.run([str(launcher), "hello"], capture_output=True, text=True, cwd=tmp_path, timeout=30)
    assert run.stdout.strip() == f"ran: --workspace {tmp_path.resolve()} hello"
    assert not list(tmp_path.glob("kvist-install.*")), "the download folder was not removed"


def test_a_wrong_checksum_installs_nothing(tmp_path):
    result = _install(tmp_path, _release(tmp_path / "release", corrupt_sum=True))

    assert result.returncode != 0
    assert "SHA-256" in result.stderr
    assert not (tmp_path / "home" / ".local").exists()


def test_a_missing_binary_installs_nothing(tmp_path):
    release_url = _release(tmp_path / "release")
    (tmp_path / "release" / _asset_name()).unlink()

    result = _install(tmp_path, release_url)

    assert result.returncode != 0
    assert "could not download" in result.stderr
    assert not (tmp_path / "home" / ".local").exists()
