#!/usr/bin/env python3
"""Drive one real Kvist run inside a pty so asciinema can record it.

The terminal frontend has no "run this goal and exit" flag, so the goal is
typed into the live prompt_toolkit application exactly as a person would type
it. Approval prompts are answered with `y` after they appear on screen: the
gate is shown, not bypassed.

usage: drive.py <repo> <workspace> <locale>
"""
from __future__ import annotations

import os
import sys
import time

import pexpect

COLS, ROWS = 100, 32

GOALS = {
    "en": (
        "check.py fails. Read slugify.py and check.py, fix slugify so runs of "
        "non-alphanumeric characters collapse into a single dash and leading "
        "and trailing dashes are stripped, then run python3 check.py."
    ),
    "ru": (
        "check.py падает. Прочитай slugify.py и check.py, почини slugify: "
        "подряд идущие не-буквенно-цифровые символы должны схлопываться в один "
        "дефис, а дефисы по краям убираться. Потом запусти python3 check.py."
    ),
}

# The banner animates continuously, so "no output" never happens while Kvist
# is alive. The run is over when the final-answer panel has been drawn; the
# quiet and hard limits are only there so a stuck run still terminates.
# A run can end two ways: final_answer draws a panel, report_to_user prints
# the reporter's JSON. Watch for both.
DONE_MARKERS = (b"Final Answer", b'"report"')
LINGER_SECONDS = 6.0
QUIET_SECONDS = 90.0
HARD_LIMIT_SECONDS = 300.0


def main() -> int:
    if len(sys.argv) != 4:
        print("usage: drive.py <repo> <workspace> <locale>", file=sys.stderr)
        return 2
    repo, workspace, locale = sys.argv[1], sys.argv[2], sys.argv[3]
    goal = GOALS[locale]

    env = dict(os.environ)
    env["TERM"] = "xterm-256color"
    env["COLUMNS"] = str(COLS)
    env["LINES"] = str(ROWS)
    env["PYTHONUNBUFFERED"] = "1"

    child = pexpect.spawn(
        os.path.join(repo, ".venv/bin/python"),
        ["main.py", "--workspace", workspace],
        cwd=repo,
        env=env,
        dimensions=(ROWS, COLS),
        encoding=None,
        timeout=None,
    )
    child.logfile_read = sys.stdout.buffer

    started = time.monotonic()
    typed = False
    last_output = time.monotonic()
    pending = b""

    def pump(budget: float) -> bytes:
        try:
            return child.read_nonblocking(size=8192, timeout=budget)
        except pexpect.TIMEOUT:
            return b""
        except (pexpect.EOF, OSError):
            raise SystemExit(0)

    # Let the banner settle before typing, the way a person would.
    while time.monotonic() - started < 6.0:
        pump(0.3)
        sys.stdout.buffer.flush()

    child.setwinsize(ROWS, COLS)
    for chunk in _typing_chunks(goal):
        child.send(chunk)
        deadline = time.monotonic() + 0.04
        while time.monotonic() < deadline:
            pump(0.01)
            sys.stdout.buffer.flush()
    time.sleep(0.6)
    child.send("\r")
    typed = True
    last_output = time.monotonic()

    done_at = None
    while True:
        now = time.monotonic()
        if now - started > HARD_LIMIT_SECONDS:
            break
        if done_at is not None and now - done_at > LINGER_SECONDS:
            break
        data = pump(0.5)
        sys.stdout.buffer.flush()
        if data:
            last_output = now
            pending = (pending + data)[-4096:]
            if b"[y/N]:" in pending:
                time.sleep(1.2)
                child.send("y\r")
                pending = b""
                last_output = time.monotonic()
            elif done_at is None and any(m in pending for m in DONE_MARKERS):
                done_at = now
        elif typed and now - last_output > QUIET_SECONDS:
            break

    time.sleep(0.5)
    child.send("/bye\r")
    deadline = time.monotonic() + 6.0
    while time.monotonic() < deadline:
        if not pump(0.4):
            continue
        sys.stdout.buffer.flush()
    sys.stdout.buffer.flush()
    try:
        child.close(force=True)
    except Exception:
        pass
    return 0


def _typing_chunks(text: str, size: int = 3):
    """Send the goal in small pieces so the recording shows it being typed."""
    for index in range(0, len(text), size):
        yield text[index:index + size].encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
