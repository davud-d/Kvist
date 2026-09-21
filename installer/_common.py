# -*- coding: utf-8 -*-
"""Shared helpers for the Kvist installers (CLI, JetBrains plugin, VS Code extension, GUI)."""

from __future__ import annotations

import os
import platform
import re
import shlex
import shutil
import subprocess
import sys
import time
import zipfile
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

try:  # rich is optional: the installers must also run under a bare interpreter.
    from rich.status import Status as _RichStatus
except ImportError:  # pragma: no cover - only when rich is not installed
    _RichStatus = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import Console, ConsoleOptions
    from rich.status import Status
    from rich.text import Text

IS_WINDOWS = platform.system() == "Windows"
IS_MACOS = platform.system() == "Darwin"
IS_LINUX = platform.system() == "Linux"

INSTALLER_DIR = Path(__file__).resolve().parent
REPO_ROOT = INSTALLER_DIR.parent
ASSETS_DIR = INSTALLER_DIR / "assets"

# The plugin id, kept in sync with jetbrains-plugin/src/main/resources/META-INF/plugin.xml.
JETBRAINS_PLUGIN_ID = "com.kvist.intellij"
# electron-builder productName; the unpacked executable is named after `name`.
GUI_APP_NAME = "Kvist"
GUI_LINUX_DESKTOP_ID = "kvist"


# --------------------------------------------------------------- quiet output
#
# Every installer runs the same way: either it is quiet, and each command it
# spawns has its output redirected into a log while one spinner line names the
# current step, or it is --verbose, and every command writes straight to the
# terminal with no spinner and no log. The two modes are exclusive, so the open
# log handle below doubles as the mode flag.

# How much of the log a failed command echoes: enough to carry a pip resolver
# error or the tail of a Gradle/npm failure, not the whole build.
LOG_TAIL_LINES = 25
# Every step text stays on screen for at least this long, so phases that finish
# instantly don't flash by unreadably. Long-running phases are never padded —
# their text is already visible well past this when the next step starts.
_STEP_DWELL_SECONDS = 2
# rich's "dots" rule ticks every 80 ms; running it at 2x and repainting at
# 30 fps keeps the braille glyph rotating smoothly instead of strobing. Both
# are rich's own animation knobs — no installer ever draws a frame itself.
_SPINNER_SPEED = 2.0
_SPINNER_FPS = 30
# The step text pulses an ellipsis of its own: a dot is added every _DOTS_PERIOD
# up to three, then taken away one at a time, for as long as the step is up.
# Deliberately slower than the spinner glyph beside it, so the two don't beat.
_DOTS_PERIOD = 0.2
_DOTS_CYCLE = ("", ".", "..", "...", "..", ".")
# Step texts carry rich markup (e.g. "[yellow]...[/yellow]"), which has to come
# back out again whenever a step is printed to a plain stream instead.
_MARKUP_TAG = re.compile(r"\[/?[a-z]+\]")

_log_file: TextIO | None = None
_log_path: Path | None = None
# time.monotonic() when the current step text was painted; None before the
# first step. Module-level because step updates happen across the helpers.
_last_step_shown_at: float | None = None


class InstallCommandError(RuntimeError):
    """An external command failed. Its output is in the log, not on screen."""


class _PulsingText:
    """A step text that grows and shrinks a trailing ellipsis as it is redrawn.

    rich has no rule for pulsing text, so the dot count is derived from the
    clock here. It still animates on rich's own refresh loop rather than one of
    ours: Status re-renders this object on every tick, so the ellipsis keeps
    moving while the main thread is busy running the step.
    """

    def __init__(self, base: str) -> None:
        self._base = base
        self._started_at = time.monotonic()

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> Iterator[Text]:
        from rich.text import Text

        phase = int((time.monotonic() - self._started_at) / _DOTS_PERIOD)
        yield Text.from_markup(f"{self._base}{_DOTS_CYCLE[phase % len(_DOTS_CYCLE)]}")


def open_log(path: Path) -> TextIO:
    """Open this run's log, truncating whatever the last run left.

    O_APPEND because child processes inherit this descriptor and advance the
    shared file offset; O_NOFOLLOW because the name is fixed and predictable
    inside a shared temp dir, and writing through someone else's symlink is
    not a risk worth taking for a log file.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_APPEND
    flags |= getattr(os, "O_NOFOLLOW", 0)
    return os.fdopen(os.open(path, flags, 0o600), "w", encoding="utf-8")


def log_tail(path: Path, lines: int) -> list[str]:
    """The last `lines` lines of a log, for a failure report."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    return content.splitlines()[-lines:]


def begin_quiet_mode(path: Path) -> None:
    """Redirect every command's output to `path` for the rest of this run.

    Not calling this *is* the verbose mode: commands keep the terminal and
    nothing is written to disk.
    """
    global _log_file, _log_path
    _log_file = open_log(path)
    _log_path = path


def close_log() -> None:
    """Close the log. The path stays known, so a failure can still cite it."""
    if _log_file is not None:
        _log_file.close()


def log(message: str) -> None:
    """Progress chatter: on screen when verbose, in the log when quiet.

    It can never go to the terminal in quiet mode — a spinner may be live
    there, and a stray print would be drawn straight through it.
    """
    if _log_file is None:
        print(f"==> {message}")
        return
    _record(f"==> {message}\n")


def warn(message: str) -> None:
    """Same split as log(): the terminal is off limits while a spinner runs.

    Anything the user must act on is re-reported by the caller once the
    spinner has stopped.
    """
    if _log_file is None:
        print(f"warning: {message}", file=sys.stderr)
        return
    _record(f"warning: {message}\n")


def _record(text: str) -> None:
    """Write one block of output wherever this run's output belongs.

    Flushed either way, because the next thing to write to that destination
    may be a child process that inherits it.
    """
    stream = sys.stdout if _log_file is None else _log_file
    stream.write(text)
    stream.flush()


def run_logged(command: list[str], *, cwd: Path | None = None) -> None:
    """Run one build command; in quiet mode its output never reaches the screen.

    With a log open, stdout and stderr are redirected into it at the file
    descriptor level, so npm, Gradle and PyInstaller write straight to disk and
    nothing is buffered in this process. Under --verbose there is no log and the
    child inherits the terminal instead. Failure raises InstallCommandError,
    which each main() turns into a log tail and a non-zero exit.
    """
    printable = shlex.join(str(part) for part in command)
    _record(f"\n$ {printable}\n")
    try:
        result = subprocess.run(
            command,
            cwd=str(cwd) if cwd is not None else None,
            stdout=_log_file,
            stderr=subprocess.STDOUT if _log_file is not None else None,
        )
    except OSError as exc:
        _record(f"{exc}\n")
        raise InstallCommandError(f"could not run `{printable}`: {exc}") from exc
    if result.returncode != 0:
        raise InstallCommandError(f"command failed (exit {result.returncode}): {printable}")


def probe_command(
    command: list[str], *, timeout: float | None = None
) -> subprocess.CompletedProcess[str]:
    """Run a short command whose output the installer reads back.

    These are probes, not build noise: their stdout is data, so it is captured
    and returned instead of being redirected. A failure is reported through the
    result — every caller decides what a failed probe means for it.
    """
    printable = shlex.join(str(part) for part in command)
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        _record(f"\n$ {printable}\n{exc}\n")
        return subprocess.CompletedProcess(command, returncode=1, stdout="", stderr=str(exc))
    _record(f"\n$ {printable}\n{result.stdout}{result.stderr}")
    return result


def report_command_failure(error: InstallCommandError) -> None:
    """Say why the install stopped, with the tail of the log behind it.

    Only ever called once the spinner has stopped, so the report cannot land
    on top of a live status line.
    """
    print(f"error: {error}", file=sys.stderr)
    if _log_path is None:  # --verbose: that output was already on screen
        return
    tail = log_tail(_log_path, LOG_TAIL_LINES)
    if tail:
        print(f"\nLast {len(tail)} lines of {_log_path}:", file=sys.stderr)
        for line in tail:
            print(f"  {line}", file=sys.stderr)
    print(f"\nFull log: {_log_path}", file=sys.stderr)


@contextmanager
def spinner(text: str) -> Iterator[Status | None]:
    """This run's one and only status line, or None when nobody can see it.

    There is nothing to animate when the output was never hidden (verbose),
    when rich is missing, or when stdout is not a terminal; on None the caller
    falls back to plain prints.
    """
    global _last_step_shown_at
    if _log_file is None or _RichStatus is None or not sys.stdout.isatty():
        yield None
        return
    with _RichStatus(
        _PulsingText(text),
        spinner="dots",
        speed=_SPINNER_SPEED,
        refresh_per_second=_SPINNER_FPS,
    ) as status:
        # Seed the dwell clock with the opening step, so the first show_step()
        # honours it too instead of replacing a line nobody has read yet.
        _last_step_shown_at = time.monotonic()
        yield status


def hold_last_step() -> None:
    """Let the step now on screen register before the spinner is torn down.

    The closing step is painted and then the spinner exits immediately, so
    without this the last thing an installer says would never be readable.
    """
    if _last_step_shown_at is None:
        return
    wait = _STEP_DWELL_SECONDS - (time.monotonic() - _last_step_shown_at)
    if wait <= 0:
        return
    try:
        time.sleep(wait)
    except KeyboardInterrupt:
        return


def show_step(status: Status | None, text: str) -> None:
    """Announce the next step, on the spinner line or as a plain printed line.

    With no spinner (piped output, no rich, --verbose) the step is printed
    instead of animated. That fallback is what makes a child installer legible
    to install.py, which reads these lines to show the progress of several
    installers running at once.

    On the spinner line, a step that has been up for less than
    _STEP_DWELL_SECONDS (i.e. it completed too fast to read) is held for the
    remainder before being replaced. Callers with their own cadence (a
    countdown) call status.update() with a plain string instead, which shows
    no ellipsis.
    """
    global _last_step_shown_at
    if status is None:
        print(_MARKUP_TAG.sub("", text), flush=True)
        return
    if _last_step_shown_at is not None:
        wait = _STEP_DWELL_SECONDS - (time.monotonic() - _last_step_shown_at)
        if wait > 0:
            time.sleep(wait)
    status.update(_PulsingText(text))
    _last_step_shown_at = time.monotonic()


# Every installer opens by saying which platform it is on, and each one branches
# on that answer, so the texts live next to the helper that shows them.
OS_STEPS = {
    "detect": "Detecting operating system",
    "windows": "Detected Windows",
    "linux": "Detected Linux",
    "macos": "[yellow]Detected macOS (untested)[/yellow]",
}


def show_detected_os(status: Status | None) -> str:
    """Announce the platform on the step line; return its OS_STEPS key."""
    show_step(status, OS_STEPS["detect"])
    key = "windows" if IS_WINDOWS else "macos" if IS_MACOS else "linux"
    show_step(status, OS_STEPS[key])
    return key


def human_size(path: Path) -> str:
    """A file's size as the installer says it out loud, e.g. "12.4 MB"."""
    try:
        size = path.stat().st_size
    except OSError:
        return "unknown size"
    if size < 1024:
        return f"{size} B"
    if size < 1024 * 1024:
        return f"{size / 1024:.1f} KB"
    return f"{size / (1024 * 1024):.1f} MB"


def safe_extract_zip(zip_path: Path, dest_dir: Path) -> None:
    """Extract every member of `zip_path` under `dest_dir`, refusing escapes.

    `ZipFile.extractall` follows `../` components and absolute paths in member
    names and will happily recreate symlink entries, so a hostile archive can
    write anywhere the process can. This rejects any member that would land
    outside `dest_dir` and any symlink member.
    """
    dest_dir = dest_dir.resolve()
    try:
        archive = zipfile.ZipFile(zip_path)
    except (OSError, zipfile.BadZipFile) as exc:
        raise SystemExit(f"error: invalid ZIP archive: {zip_path}") from exc
    with archive:
        for info in archive.infolist():
            # Unix mode is stored in the top 16 bits of external_attr; 0xA000 == S_IFLNK.
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                raise SystemExit(f"error: refusing symlink entry in archive: {info.filename}")
            target = (dest_dir / info.filename).resolve()
            if target != dest_dir and dest_dir not in target.parents:
                raise SystemExit(f"error: refusing path-traversal entry in archive: {info.filename}")
        try:
            archive.extractall(dest_dir)
        except (OSError, zipfile.BadZipFile) as exc:
            raise SystemExit(f"error: could not extract ZIP archive: {zip_path}") from exc


def remove_path(target: Path) -> bool:
    """Delete a file, directory, or symlink without following it. No-op if absent.

    A naive ``if target.exists(): shutil.rmtree(target)`` is wrong twice: it
    misses broken symlinks (``exists()`` is False for them) and it crashes on a
    symlinked directory (``rmtree`` refuses one, and a dev checkout often
    symlinks the installed plugin folder back to the repo). Returns whether
    anything was removed.
    """
    if target.is_symlink() or (target.exists() and not target.is_dir()):
        target.unlink()
        return True
    if target.is_dir():
        shutil.rmtree(target)
        return True
    return False


def jetbrains_config_roots() -> list[Path]:
    """Directories that hold one sub-directory per installed JetBrains IDE.

    Since 2020.1 every IDE keeps its per-user data under a shared vendor root
    (`.../JetBrains/<Product><version>`), regardless of whether it was installed
    standalone, from a distro package, or via Toolbox.
    """
    home = Path.home()
    if IS_WINDOWS:
        base = Path(os.environ.get("APPDATA", home / "AppData" / "Roaming"))
        return [base / "JetBrains"]
    if IS_MACOS:
        return [home / "Library" / "Application Support" / "JetBrains"]
    roots = [home / ".local" / "share" / "JetBrains"]
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        roots.insert(0, Path(xdg) / "JetBrains")
    return roots


# Product-directory prefixes -> human label. A directory is "IntelliJIdea2025.2",
# "PyCharm2024.3", "GoLand2025.1" and so on; the trailing version is stripped.
JETBRAINS_PRODUCT_PREFIXES = {
    "IntelliJIdea": "IntelliJ IDEA",
    "IdeaIC": "IntelliJ IDEA Community",
    "PyCharm": "PyCharm",
    "PyCharmCE": "PyCharm Community",
    "WebStorm": "WebStorm",
    "PhpStorm": "PhpStorm",
    "GoLand": "GoLand",
    "CLion": "CLion",
    "Rider": "Rider",
    "RubyMine": "RubyMine",
    "DataGrip": "DataGrip",
    "DataSpell": "DataSpell",
    "RustRover": "RustRover",
    "AndroidStudio": "Android Studio",
    "Aqua": "Aqua",
}


def _match_product(dir_name: str) -> str | None:
    for prefix, label in sorted(JETBRAINS_PRODUCT_PREFIXES.items(), key=lambda kv: -len(kv[0])):
        rest = dir_name[len(prefix):]
        if dir_name.startswith(prefix) and rest[:1].isdigit():
            return f"{label} {rest}"
    return None


def discover_jetbrains_ides() -> list[tuple[str, Path]]:
    """Return (label, config_dir) for every installed JetBrains IDE found."""
    found: list[tuple[str, Path]] = []
    seen: set[Path] = set()
    for root in jetbrains_config_roots():
        if not root.is_dir():
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir() or entry in seen:
                continue
            label = _match_product(entry.name)
            if label is None:
                continue
            seen.add(entry)
            found.append((label, entry))
    return found


def user_desktop_dir() -> Path:
    home = Path.home()
    if IS_WINDOWS:
        # Fall back gracefully when the shell folder is redirected (OneDrive etc.).
        try:
            import winreg

            with winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders",
            ) as key:
                value, _ = winreg.QueryValueEx(key, "Desktop")
                expanded = Path(os.path.expandvars(value))
                if expanded.is_dir():
                    return expanded
        except OSError:
            pass
    return home / "Desktop"
