#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Pick what to install and watch it happen.

This is the front door: it offers the installers (`install_cli.py`,
`install_web.py`, `install_jetbrains.py`, `install_vscode.py`, `install_gui.py`,
`install_zed.py`, `install_kvide.py`) as an arrow-key list, all selected by default, and runs the
ones you keep.

    python scripts/installer/install.py             # pick with the arrow keys
    python scripts/installer/install.py --all       # everything, no questions
    python scripts/installer/install.py --cli --gui # named components, no questions

Quietly, the chosen installers run at once as background processes and a live
panel shows what each one is doing; each writes its own log, and a failed one
prints the tail of its output. A component that names its dependencies waits
for them: `install_zed.py` registers the binary `install_cli.py` installs, so
it stays queued until the CLI is through. With --verbose there is no panel:
they run one after another with their output going straight to the terminal.

Each installer keeps its own flags (--build, --uninstall, --plugin-zip, --vsix, ...).
Run it directly when you need those.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from _common import INSTALLER_DIR, IS_WINDOWS

try:  # rich is optional: install.py must also run under a bare interpreter.
    from rich.live import Live as _RichLive
except ImportError:  # pragma: no cover - only when rich is not installed
    _RichLive = None  # type: ignore[assignment]

if TYPE_CHECKING:
    from collections.abc import Iterator

    from rich.console import Console, ConsoleOptions
    from rich.text import Text


@dataclass(slots=True, frozen=True)
class Component:
    """One installer this script can run."""

    key: str
    label: str
    script: str
    # Keys of other components that must finish before this one starts. A
    # component that writes a path another installer creates has to wait for it,
    # or a parallel run would race for the file.
    after: tuple[str, ...] = ()


COMPONENTS = (
    Component("cli", "CLI — the `kvist` command", "install_cli.py"),
    Component("web", "Web control center — `kvist-web`", "install_web.py", after=("cli",)),
    Component("jetbrains", "JetBrains plugin", "install_jetbrains.py"),
    Component("vscode", "VS Code extension", "install_vscode.py"),
    Component("gui", "GUI application", "install_gui.py"),
    Component("zed", "Zed agent registration", "install_zed.py", after=("cli",)),
    Component("kvide", "kvide commit-message generator", "install_kvide.py", after=("cli",)),
)

# How much of a failed component's output is shown afterwards. It already went
# to that installer's own log; this is only the part worth reading inline.
_TAIL_LINES = 25
_LIVE_FPS = 30
# Marks for the four states a component passes through. The running one is
# animated from the clock, like the installers' own spinner.
_FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
_FRAME_PERIOD = 0.05
_MARKS = {"queued": " ", "done": "✓", "failed": "✗"}
_STYLES = {"queued": "dim", "running": "", "done": "green", "failed": "red"}


@dataclass(slots=True)
class Progress:
    """What one component is doing, written by its reader thread.

    Only `state` and `step` are read while the thread runs, and both are
    rebound whole rather than mutated, so the panel never sees a half-written
    value. `tail` is read only once the thread has been joined.
    """

    state: str = "queued"
    step: str = ""
    started_at: float | None = None
    elapsed: float = 0.0
    returncode: int | None = None
    tail: deque[str] = field(default_factory=lambda: deque(maxlen=_TAIL_LINES))


# --------------------------------------------------------------------- running

# The two states a component reaches and never leaves.
_TERMINAL_STATES = ("done", "failed")


def _await_dependencies(component: Component, progresses: dict[str, Progress]) -> None:
    """Block until the installers this one needs have finished.

    A component that only writes a path another installer creates stays queued
    until that one is through, so a parallel run cannot race for the file.
    A dependency that was not chosen at all does not block: the component checks
    its own input and reports it missing itself.
    """
    if not component.after:
        return
    while True:
        waiting = [
            key
            for key in component.after
            if key in progresses and progresses[key].state not in _TERMINAL_STATES
        ]
        if not waiting:
            return
        progresses[component.key].step = f"Waiting for: {', '.join(waiting)}"
        time.sleep(0.1)


def _run_component(component: Component, progress: Progress, progresses: dict[str, Progress]) -> None:
    """Run one installer to completion, tracking its latest line of output.

    stderr is folded into stdout so a single reader drains the child: that is
    both deadlock-free and the order the output was actually produced in.
    """
    # Include dependency waiting in the component's total elapsed time.
    progress.started_at = time.monotonic()
    _await_dependencies(component, progresses)
    command = [sys.executable, str(INSTALLER_DIR / component.script)]
    progress.step = "Starting installer"
    progress.state = "running"
    try:
        child = subprocess.Popen(
            command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1
        )
    except OSError as exc:
        progress.tail.append(str(exc))
        _finish(progress, 1)
        return
    with child.stdout:  # type: ignore[union-attr]
        for line in child.stdout:  # type: ignore[union-attr]
            stripped = line.rstrip()
            if not stripped:
                continue
            progress.tail.append(stripped)
            progress.step = stripped
            progress.elapsed = time.monotonic() - (progress.started_at or 0.0)
    _finish(progress, child.wait())


def _finish(progress: Progress, returncode: int) -> None:
    progress.elapsed = time.monotonic() - (progress.started_at or time.monotonic())
    progress.returncode = returncode
    progress.state = "done" if returncode == 0 else "failed"


def _install_in_background(chosen: list[Component]) -> dict[str, Progress]:
    """Start every chosen installer at once and render until they all stop.

    They are separate processes on purpose: each one owns module-level state
    for its own log, which threads in one interpreter would trample.
    """
    progresses = {component.key: Progress() for component in chosen}
    threads = [
        threading.Thread(
            target=_run_component, args=(component, progresses[component.key], progresses)
        )
        for component in chosen
    ]
    for thread in threads:
        thread.start()
    try:
        _watch(chosen, progresses)
    finally:
        for thread in threads:
            thread.join()
    return progresses


def _install_one_by_one(chosen: list[Component]) -> dict[str, Progress]:
    """--verbose: run them in turn, output straight through, no panel.

    The terminal cannot be a live panel and a child's stdout at the same time,
    so verbose gives it entirely to the children — which also means they have
    to run one at a time to stay readable.
    """
    progresses: dict[str, Progress] = {}
    # COMPONENTS lists every component after the ones it depends on, so this is
    # dependency order for a list assembled in any other way too.
    rank = {component.key: index for index, component in enumerate(COMPONENTS)}
    for component in sorted(chosen, key=lambda item: rank[item.key]):
        print(f"\n=== {component.label} ===", flush=True)
        progress = Progress(state="running", started_at=time.monotonic())
        progresses[component.key] = progress
        result = subprocess.run(
            [sys.executable, str(INSTALLER_DIR / component.script), "--verbose"]
        )
        _finish(progress, result.returncode)
    return progresses


# -------------------------------------------------------------------- watching

class _Panel:
    """Rich renderable: one line per component, re-read on every refresh."""

    def __init__(self, components: list[Component], progresses: dict[str, Progress]) -> None:
        self._components = components
        self._progresses = progresses
        self._started_at = time.monotonic()

    def __rich_console__(self, console: Console, options: ConsoleOptions) -> Iterator[Text]:
        from rich.text import Text

        spinning = _FRAMES[int((time.monotonic() - self._started_at) / _FRAME_PERIOD) % len(_FRAMES)]
        for component in self._components:
            progress = self._progresses[component.key]
            state = progress.state
            active = state not in _TERMINAL_STATES and progress.started_at is not None
            mark = spinning if active else _MARKS.get(state, " ")
            elapsed = (
                time.monotonic() - progress.started_at
                if active and progress.started_at is not None
                else progress.elapsed
            )
            head = f" {mark} {component.label}  {elapsed:5.1f}s  "
            yield Text.assemble(
                (head, _STYLES[state]),
                (_fit(progress.step, max(0, console.width - len(head) - 1)), "dim"),
            )


def _fit(text: str, width: int) -> str:
    if width <= 1 or len(text) <= width:
        return text
    return text[: width - 1] + "…"


def _all_finished(progresses: dict[str, Progress]) -> bool:
    return all(progress.state in ("done", "failed") for progress in progresses.values())


def _watch(components: list[Component], progresses: dict[str, Progress]) -> None:
    if _RichLive is None or not sys.stdout.isatty():
        _watch_plainly(components, progresses)
        return
    with _RichLive(_Panel(components, progresses), refresh_per_second=_LIVE_FPS):
        while not _all_finished(progresses):
            time.sleep(0.05)


def _watch_plainly(components: list[Component], progresses: dict[str, Progress]) -> None:
    """No rich or no terminal: report each state change on its own line."""
    reported: dict[str, str] = {}
    while True:
        finished = _all_finished(progresses)
        for component in components:
            state = progresses[component.key].state
            if reported.get(component.key) != state:
                reported[component.key] = state
                print(f"{component.label}: {state}", flush=True)
        if finished:
            return
        time.sleep(0.2)


# ---------------------------------------------------------------------- picking

def _read_key() -> str:
    """One keypress from the terminal, as a name the menu understands."""
    if IS_WINDOWS:
        import msvcrt

        char = msvcrt.getwch()
        if char in ("\x00", "\xe0"):  # the prefix every arrow key arrives with
            return {"H": "up", "P": "down"}.get(msvcrt.getwch(), "")
        return _named_key(char)

    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    original = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd, termios.TCSADRAIN)
        # os.read, not sys.stdin.read: the buffered reader would swallow the
        # whole escape sequence into its own buffer, leaving the select below
        # with nothing to see and every arrow key looking like a lone Esc.
        char = os.read(fd, 1).decode(errors="ignore")
        if not char:
            return "quit"
        if char != "\x1b":
            return _named_key(char)
        # Esc on its own vs. the CSI sequence of an arrow key: the rest of a
        # real sequence is already waiting, a lone Esc leaves nothing behind.
        if not select.select([fd], [], [], 0.05)[0]:
            return "quit"
        return {"[A": "up", "[B": "down"}.get(os.read(fd, 2).decode(errors="ignore"), "")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, original)


def _named_key(char: str) -> str:
    if char in ("\r", "\n"):
        return "enter"
    if char == " ":
        return "space"
    if char == "a":
        return "all"
    if char in ("q", "\x03", "\x04"):
        return "quit"
    return ""


def _menu_lines(selected: list[bool], cursor: int) -> list[str]:
    lines = ["", "  Install Kvist — what should go on this machine?", ""]
    for index, component in enumerate(COMPONENTS):
        pointer = "❯" if index == cursor else " "
        box = "x" if selected[index] else " "
        lines.append(f"  {pointer} [{box}] {component.label}")
    count = sum(selected)
    action = f"install {count} of {len(COMPONENTS)}" if count else "nothing selected"
    lines.append("")
    lines.append(f"  ↑↓ move · space toggle · a all/none · enter {action} · q quit")
    lines.append("")
    return lines


def _draw_menu(selected: list[bool], cursor: int, drawn: int) -> int:
    """Paint the picker over its last drawing; return the rows it filled.

    The height is measured rather than assumed: a line longer than the terminal
    wraps onto extra rows, and the cursor has to climb back over those too or
    each redraw eats a line above and the menu smears.
    """
    if drawn:
        sys.stdout.write(f"\x1b[{drawn}A\x1b[J")
    lines = _menu_lines(selected, cursor)
    width = max(1, shutil.get_terminal_size(fallback=(80, 24)).columns)
    sys.stdout.write("\n".join(lines) + "\n")
    sys.stdout.flush()
    return sum(max(1, -(-len(line) // width)) for line in lines)


def choose_components() -> list[Component] | None:
    """Arrow-key picker. Returns what to install, or None if the user quit.

    Everything starts selected, so "install all" is the plain Enter; clearing
    entries with space is how a single component gets installed on its own.
    """
    selected = [True] * len(COMPONENTS)
    cursor = 0
    drawn = 0
    while True:
        drawn = _draw_menu(selected, cursor, drawn)
        try:
            key = _read_key()
        except KeyboardInterrupt:
            return None
        if key == "quit":
            return None
        if key == "up":
            cursor = (cursor - 1) % len(COMPONENTS)
        elif key == "down":
            cursor = (cursor + 1) % len(COMPONENTS)
        elif key == "space":
            selected[cursor] = not selected[cursor]
        elif key == "all":
            fill = not all(selected)
            selected = [fill] * len(COMPONENTS)
        elif key == "enter" and any(selected):
            return [c for c, on in zip(COMPONENTS, selected) if on]


def selected_from_flags(args: argparse.Namespace) -> list[Component]:
    """The components named on the command line; empty when none were."""
    if args.all:
        return list(COMPONENTS)
    return [component for component in COMPONENTS if getattr(args, component.key)]


# ------------------------------------------------------------------------- main

def report(chosen: list[Component], progresses: dict[str, Progress], verbose: bool = False) -> int:
    """Print what happened; return the exit code for the whole run.

    `verbose` only decides whether to suggest it: there the failing installer's
    output was on screen as it happened, so there is nothing left to replay.
    """
    failed = [c for c in chosen if progresses[c.key].state == "failed"]
    for component in failed:
        progress = progresses[component.key]
        print(f"\n{component.label} failed (exit {progress.returncode}).", file=sys.stderr)
        for line in progress.tail:
            print(f"  {line}", file=sys.stderr)
    if not failed:
        print(f"\nDone. {len(chosen)} of {len(chosen)} installed.")
        return 0
    summary = f"\n{len(chosen) - len(failed)} of {len(chosen)} installed."
    if not verbose:
        summary += " Re-run with --verbose to watch the failing one directly."
    print(summary, file=sys.stderr)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--all", action="store_true", help="Install everything without asking.")
    for component in COMPONENTS:
        parser.add_argument(
            f"--{component.key}", action="store_true", help=f"Install the {component.label}."
        )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Run the installers one at a time with their output on screen.",
    )
    args = parser.parse_args()

    chosen = selected_from_flags(args)
    if not chosen:
        if not sys.stdin.isatty():
            parser.error("nothing to install: pass --all or a component flag")
        picked = choose_components()
        if picked is None:
            print("Nothing installed.")
            return 0
        chosen = picked

    runner = _install_one_by_one if args.verbose else _install_in_background
    try:
        progresses = runner(chosen)
    except KeyboardInterrupt:
        print("\nInstallation cancelled.", file=sys.stderr)
        return 130
    return report(chosen, progresses, args.verbose)


if __name__ == "__main__":
    sys.exit(main())
