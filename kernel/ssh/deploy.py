# -*- coding: utf-8 -*-
"""Copy this checkout's sources to a saved machine and make its ``kvist-core`` run them.

For a build that has not been released: a Kvist installed from a release on the server cannot do remote work until a
release has the remote core in it (it refuses ``--remote-op``). The sources go to a folder on the machine, get their own
Python environment, and ``~/.local/share/kvist/kvist-core`` becomes a small script that runs them. A release binary that
was there is kept beside it as ``kvist-core.release``; only a frozen binary updates itself, so the script stays.
"""
from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path

from kernel.ssh import machines, messages
from kernel.ssh.command import Target, meaningful, ssh_prefix
from kernel.ssh.paths import RemoteError

REPO = Path(__file__).resolve().parents[2]
# The core imports the classic terminal and the Zed frontend by module name, and reads its version from pyproject.toml.
_PATHS = ("kernel", "frontends/tui/classic", "frontends/zed", "pyproject.toml", "requirements.txt")
DEFAULT_FOLDER = "$HOME/kvist-src"


def source_files(repo: Path = REPO) -> list[str]:
    """What the core needs to run, as git sees it (tracked, and new files that are not ignored); never tests."""
    listing = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "--", *_PATHS],
        cwd=repo, capture_output=True, encoding="utf-8", check=False,
    )
    if listing.returncode:
        raise RemoteError(f"git could not list the sources: {listing.stderr.strip()}")
    return sorted({name for name in listing.stdout.splitlines() if name and "/tests/" not in f"/{name}"})


def venv_guard(*, needs_pip: bool) -> str:
    """Make sure ``.venv`` is a working environment. A half-made one (Debian without ``python3-venv`` leaves the folder behind with no pip in it) is thrown away."""
    healthy = ".venv/bin/python -m pip --version >/dev/null 2>&1" if needs_pip else "[ -x .venv/bin/python ]"
    return (
        f"if ! {healthy}; then\n"
        "  rm -rf .venv\n"
        "  python3 -m venv .venv || { rm -rf .venv; echo 'python3 -m venv failed; on Debian or Ubuntu: apt install python3-venv' >&2; exit 21; }\n"
        "fi\n"
    )


def remote_script(folder: str, *, install_dependencies: bool = True) -> str:
    """What runs on the machine once the sources are there."""
    pip = ".venv/bin/python -m pip install -q -r requirements.txt\n" if install_dependencies else ""
    return (
        "set -e\n"
        f'cd "{folder}"\n'
        "python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' "
        "|| { echo 'Python 3.11 or newer is needed on the machine.' >&2; exit 20; }\n"
        f"{venv_guard(needs_pip=install_dependencies)}"
        f"{pip}"
        'core="$HOME/.local/share/kvist/kvist-core"\n'
        'mkdir -p "$(dirname "$core")"\n'
        'if [ -e "$core" ] && ! grep -q kvist-dev-shim "$core" 2>/dev/null; then mv "$core" "$core.release"; fi\n'
        f"printf '#!/bin/sh\\n# kvist-dev-shim: runs the sources in %s\\nexec \"%s/.venv/bin/python\" \"%s/kernel/__main__.py\" \"$@\"\\n'"
        f' "{folder}" "{folder}" "{folder}" > "$core"\n'
        'chmod +x "$core"\n'
        '"$core" --version\n'
    )


def install(target: Target, folder: str = DEFAULT_FOLDER, *, install_dependencies: bool = True, repo: Path = REPO) -> str:
    """Copy the sources to ``folder`` on the machine, set it up, and return what ``kvist-core --version`` says there."""
    names = source_files(repo)
    archive = subprocess.Popen(["tar", "-czf", "-", "-C", str(repo), *names], stdout=subprocess.PIPE)
    unpack = f'mkdir -p "{folder}" && tar -xzf - -C "{folder}"'
    copied = subprocess.run([*ssh_prefix(target), "sh -c " + shlex.quote(unpack)], stdin=archive.stdout, capture_output=True, encoding="utf-8", errors="replace")
    # Our copy of the pipe's read end must go: while it is open, tar never gets SIGPIPE when ssh ended early and waits for ever.
    archive.stdout.close()
    try:
        archive.wait(timeout=60)
    except subprocess.TimeoutExpired:
        archive.kill()
        archive.wait()
    if copied.returncode:
        raise RemoteError(f"Copying the sources failed: {meaningful(copied.stderr) or 'the machine closed the connection'}")
    if archive.returncode:
        raise RemoteError("Copying the sources failed: tar could not pack them on this computer.")
    script = remote_script(folder, install_dependencies=install_dependencies)
    done = subprocess.run([*ssh_prefix(target), "sh -c " + shlex.quote(script)], capture_output=True, encoding="utf-8", errors="replace")
    if done.returncode:
        raise RemoteError(f"Setting it up failed (exit {done.returncode}): {meaningful(done.stderr) or done.stdout.strip()}")
    return done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""


# --- this computer's own Kvist, as it is ---------------------------------------------------------------

_ARCHITECTURES = {"x86_64": "x86_64", "amd64": "x86_64", "aarch64": "arm64", "arm64": "arm64"}


def local_binary() -> Path | None:
    """The running Kvist when it is a frozen program (the installed ``kvist-core``); None when it runs from sources."""
    return Path(sys.executable) if getattr(sys, "frozen", False) else None


def _platform_of(system: str, machine: str) -> tuple[str, str]:
    return system.strip().lower(), _ARCHITECTURES.get(machine.strip().lower(), machine.strip().lower())


def machine_platform(target: Target) -> tuple[str, str]:
    """The machine's system and processor, as ``uname -sm`` says."""
    done = subprocess.run([*ssh_prefix(target), "uname -sm"], capture_output=True, encoding="utf-8", errors="replace", timeout=60, check=False)
    words = done.stdout.split()
    if done.returncode or len(words) < 2:
        raise RemoteError(f"Could not ask {target.label} which system it is: {done.stderr.strip() or 'no answer'}")
    return _platform_of(words[-2], words[-1])


def this_platform() -> tuple[str, str]:
    import platform

    return _platform_of(platform.system(), platform.machine())


# The new file is tried where it will live before it replaces anything: a binary built for another system, or against a newer
# C library than the machine has, fails here and leaves what was there alone.
BINARY_SCRIPT = (
    "set -e\n"
    'core="$HOME/.local/share/kvist/kvist-core"\n'
    'mkdir -p "$(dirname "$core")"\n'
    'cat > "$core.new"\n'
    'chmod 755 "$core.new"\n'
    'if ! "$core.new" --version >"$core.new.out" 2>"$core.new.err"; then\n'
    '  cat "$core.new.err" >&2; rm -f "$core.new" "$core.new.out" "$core.new.err"; exit 22\n'
    "fi\n"
    'rm -f "$core.new.err"\n'
    'if [ -e "$core" ]; then mv -f "$core" "$core.previous"; fi\n'
    'mv -f "$core.new" "$core"\n'
    'cat "$core.new.out"; rm -f "$core.new.out"\n'
)


def install_binary(target: Target, binary: Path, *, local: tuple[str, str] | None = None) -> str:
    """Copy ``binary`` (this computer's Kvist) to the machine as its ``kvist-core``; return what it says its version is there.

    The machine has to be the same kind of system as this one, because a program built here runs only on such a machine.
    """
    here, there = local or this_platform(), machine_platform(target)
    if here != there:
        raise RemoteError(
            f"{target.label} is {there[0]} {there[1]} and this computer is {here[0]} {here[1]}: a program built here does not run there."
        )
    with open(binary, "rb") as handle:
        done = subprocess.run(
            [*ssh_prefix(target), "sh -c " + shlex.quote(BINARY_SCRIPT)], stdin=handle, capture_output=True, encoding="utf-8", errors="replace", check=False
        )
    if done.returncode == 22:
        reason = meaningful(done.stderr) or "no reason given"
        raise RemoteError(messages.text("copy_does_not_run", messages.ui_language(), host=target.label, reason=reason))
    if done.returncode:
        raise RemoteError(f"Copying Kvist to {target.label} failed (exit {done.returncode}): {meaningful(done.stderr)}")
    return done.stdout.strip().splitlines()[-1] if done.stdout.strip() else ""


def install_core(target: Target) -> str:
    """Put a Kvist that can do remote work on the machine: this computer's own program, or, from a checkout, its sources."""
    binary = local_binary()
    return install_binary(target, binary) if binary else install(target)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Copy this checkout to a saved machine and make its kvist-core run it.")
    parser.add_argument("machine", help="a name saved with kvist --machines")
    parser.add_argument("--folder", default=DEFAULT_FOLDER, help="where on the machine the sources go (default: ~/kvist-src)")
    options = parser.parse_args(argv)
    machine = machines.get_machine(options.machine)
    if machine is None:
        print(f"No machine is saved as {options.machine!r}. See them with kvist --machines.", file=sys.stderr)
        return 2
    target = Target(machine.host, machine.port, "/", machine.key)
    print(f"Copying the sources to {machine.spec}:{options.folder} and installing their dependencies (this can take a few minutes)…")
    try:
        version = install(target, options.folder)
    except RemoteError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"Done: kvist-core on {machine.spec} now runs these sources ({version}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
