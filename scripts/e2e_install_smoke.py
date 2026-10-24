# -*- coding: utf-8 -*-
"""Install smoke test: the CLI installer, then the installed `kvist` on a fake provider.

Runs scripts/installer/install_cli.py into a scratch home directory (it builds
the binary from this checkout, so it takes minutes), checks that the launcher
and the core binary were installed and report a version, then drives the
installed launcher through the scenarios of e2e_tools_smoke.py: three file
tools and one terminal command, answered by a fake local provider. No real
LLM is called and no other installer runs.

The checkout's dist/ and .venv-build are the installer's working directories;
run this from a clean checkout or a copy of one.

    python scripts/e2e_install_smoke.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import e2e_tools_smoke as smoke

INSTALL_TIMEOUT_SECONDS = 25 * 60
FIRST_START_TIMEOUT_SECONDS = 90  # a onefile binary unpacks itself on its first run
IS_WINDOWS = os.name == "nt"
LAUNCHER = "kvist.cmd" if IS_WINDOWS else "kvist"
CORE = "kvist-core.exe" if IS_WINDOWS else "kvist-core"


def run_installer(home: Path, lib_dir: Path, bin_dir: Path) -> None:
    """Run install_cli.py with the scratch home; raises AssertionError with the tail of its output."""
    env = dict(os.environ, HOME=str(home), USERPROFILE=str(home), PYTHONUTF8="1",
               LOCALAPPDATA=str(home / "AppData" / "Local"),
               # On PATH already, so the installer leaves the user's real PATH alone.
               PATH=os.pathsep.join([str(bin_dir), os.environ.get("PATH", "")]))
    started = time.monotonic()
    result = subprocess.run(
        [sys.executable, str(smoke.ROOT / "scripts" / "installer" / "install_cli.py"),
         "--lib-dir", str(lib_dir), "--bin-dir", str(bin_dir)],
        cwd=smoke.ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=INSTALL_TIMEOUT_SECONDS, check=False)
    tail = "\n".join((result.stdout + result.stderr).splitlines()[-30:])
    assert result.returncode == 0, f"the installer exited {result.returncode}:\n{tail}"
    print(f"installer finished in {time.monotonic() - started:.0f}s")


def check_installed(lib_dir: Path, bin_dir: Path, workspace: Path) -> Path:
    """The files the installer promises exist and run; returns the launcher path."""
    core, launcher = lib_dir / CORE, bin_dir / LAUNCHER
    assert core.is_file(), f"{core} was not installed"
    assert launcher.is_file(), f"{launcher} was not installed"
    reported = subprocess.run([str(launcher), "--version"], cwd=workspace, capture_output=True,
                              text=True, timeout=FIRST_START_TIMEOUT_SECONDS, check=False)
    assert reported.returncode == 0 and reported.stdout.strip(), (
        f"`kvist --version` exited {reported.returncode}: {reported.stdout!r} {reported.stderr!r}")
    print(f"installed: {launcher} -> {reported.stdout.strip()}")
    return launcher


def main() -> int:
    provider = smoke.FakeProvider()
    with tempfile.TemporaryDirectory(prefix="kvist-install-smoke-") as scratch:
        home = Path(scratch, "home")
        workspace = Path(scratch, "workspace")
        lib_dir, bin_dir = home / "kvist" / "lib", home / "kvist" / "bin"
        for directory in (home, workspace):
            directory.mkdir()
        smoke.configure(home, provider.url)
        smoke.allow_python(workspace)
        core = None
        try:
            run_installer(home, lib_dir, bin_dir)
            launcher = check_installed(lib_dir, bin_dir, workspace)
            launched = time.monotonic()
            core = smoke.Kvist([str(launcher)], workspace, home)
            core.collect_until("hello", FIRST_START_TIMEOUT_SECONDS)
            print(f"core ready  ({time.monotonic() - launched:.1f}s after launch)")
            elapsed = smoke.run_turns(core, workspace)
        except (AssertionError, TimeoutError, subprocess.TimeoutExpired) as error:
            print(f"FAIL: {error}")
            if core is not None:
                print("--- core stderr:\n" + "\n".join(core.stderr_lines[-40:]))
            print(f"--- provider saw {len(provider.requests)} requests")
            return 1
        finally:
            if core is not None:
                core.stop()
            provider.server.shutdown()
    print(f"all {len(smoke.SCENARIOS)} turns passed in {elapsed:.1f}s")
    if elapsed > smoke.TURN_BUDGET_SECONDS:
        print(f"FAIL: over the {smoke.TURN_BUDGET_SECONDS}s budget")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
