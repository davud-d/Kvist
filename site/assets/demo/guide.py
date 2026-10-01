#!/usr/bin/env python3
"""Drive one scripted Kvist session inside a pty so asciinema can record it.

The landing page's guides are short recordings of the real terminal, one per
flow. This is the driver that produces them: it opens Kvist exactly as a person
would and presses the same keys, then leaves.

Two things it deliberately never touches:

* the real `~/.kvist/config.json` — every guide runs under a throwaway HOME, so
  the recording cannot read, write or reveal the operator's providers or keys;
* a real API key — the provider guide types an obvious placeholder into the
  masked prompt, which is enough to show the flow and useless to anybody.

Diagnostics go to a log file, never to stderr: stderr shares the pty with the
recording, so a warning printed there would end up on the page.

usage: guide.py <repo> <home> <workspace> <scenario> [log]
"""
from __future__ import annotations

import os
import sys
import time

import pexpect

COLS, ROWS = 100, 32

KEYS = {
    "up": "\x1b[A",
    "down": "\x1b[B",
    "enter": "\r",
    "esc": "\x1b",
    "ctrl-c": "\x03",
    # Text prompts arrive pre-filled with the current value, so a guide that
    # types into one has to empty it first. Backspaces work everywhere.
    "clear": "\x7f" * 48,
}

# A placeholder, not a key. It is written into a throwaway HOME that is deleted
# after the recording, and the prompt masks it anyway.
PLACEHOLDER_KEY = "local-gateway-no-key-needed"

# The throwaway HOME hides the config file, but Kvist looks in the environment
# first, so an exported key would walk straight past it and into the recording.
# The child gets what a terminal session needs and nothing else.
PASS_THROUGH = ("PATH", "USER", "LOGNAME", "SHELL", "LANG", "TZ")

# prompt_toolkit probes the terminal for the cursor position and warns on the
# screen when nothing answers. A pty is not a terminal, so the driver answers.
CPR_QUERY = b"\x1b[6n"
CPR_REPLY = "\x1b[%d;1R" % ROWS


def steps_for(scenario: str) -> list[tuple]:
    """The script for one guide, as (verb, argument) pairs."""
    if scenario == "provider":
        # Down to OmniRoute: OpenAI, Anthropic, DeepSeek, OpenRouter, OmniRoute.
        # The guides run against a local gateway, so no real provider is called
        # and no real key is typed anywhere in these recordings.
        return [
            ("await", b"Select", 30),
            ("wait", 1.4),
            ("key", "down"), ("wait", 0.45),
            ("key", "down"), ("wait", 0.45),
            ("key", "down"), ("wait", 0.45),
            ("key", "down"), ("wait", 0.9),
            ("key", "enter"),
            ("await", b"API key", 30),
            ("wait", 1.2),
            ("type", PLACEHOLDER_KEY),
            ("wait", 0.7),
            ("key", "enter"),
            ("wait", 4.0),
        ]
    if scenario == "commands":
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.2),
            ("type", "/commands"),
            ("wait", 0.6),
            ("key", "enter"),
            ("wait", 5.0),
        ]
    if scenario == "role":
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.2),
            ("type", "/role"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 2.0),
            ("key", "down"), ("wait", 0.6),
            ("key", "down"), ("wait", 0.6),
            ("key", "down"), ("wait", 1.0),
            ("key", "enter"),
            ("wait", 2.5),
        ]
    if scenario == "effort":
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.2),
            ("type", "/effort"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 2.0),
            ("key", "down"), ("wait", 0.6),
            ("key", "down"), ("wait", 1.0),
            ("key", "enter"),
            ("wait", 3.0),
        ]
    if scenario == "session":
        # The menu opens on the current session, so creating a new one means
        # stepping down to "+ Create new session..." first.
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.2),
            ("type", "/session"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 2.4),
            ("key", "down"), ("wait", 1.0),
            ("key", "enter"),
            ("wait", 2.0),
            ("type", "review"),
            ("wait", 0.7),
            ("key", "enter"),
            ("wait", 4.0),
        ]
    if scenario == "changes":
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.2),
            ("type", "/changes"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 4.5),
            ("type", "/revert"),
            ("wait", 0.5),
            ("key", "enter"),
            # The confirmation opens on "No, cancel"; reverting is a decision.
            ("wait", 2.6),
            ("key", "down"), ("wait", 1.0),
            ("key", "enter"),
            ("wait", 3.5),
        ]
    if scenario == "custom":
        # /provider -> "+ Create custom provider...", then the whole dialog:
        # id, protocol, base URL, streaming, key, name, models. The key typed
        # here is not a secret: the endpoint is a gateway on this machine that
        # ignores it.
        #
        # The menu opens on the provider already in use — OmniRoute, one above
        # FreeFlow — not on the first entry, so this is two steps, not six.
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.0),
            ("type", "/provider"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 2.2),
            ("key", "down"), ("wait", 0.5),
            ("key", "down"), ("wait", 1.0),
            ("key", "enter"),
            ("wait", 2.2),
            ("type", "local-gateway"),
            ("wait", 0.6),
            ("key", "enter"),
            ("wait", 2.0),
            ("key", "enter"),              # protocol: OpenAI-compatible
            ("wait", 2.0),
            ("type", "http://127.0.0.1:20129/v1"),
            ("wait", 0.6),
            ("key", "enter"),
            ("wait", 2.2),
            ("key", "enter"),              # streaming: no / not sure
            ("wait", 2.0),
            # The prompt offers to skip the key, but the OpenAI client refuses
            # to be built without one, so a local gateway still needs a value.
            ("type", "local-gateway"),
            ("wait", 0.6),
            ("key", "enter"),
            ("wait", 2.0),
            ("type", "Local gateway"),
            ("wait", 0.6),
            ("key", "enter"),
            ("wait", 2.0),
            ("key", "enter"),              # one model id for all roles
            ("wait", 2.0),
            ("type", "local-fast"),
            ("wait", 0.6),
            ("key", "enter"),
            ("wait", 2.4),
            ("key", "enter"),              # "Create this provider?" — yes
            ("wait", 4.5),
        ]
    if scenario == "models":
        # The menu opens on "Enter one model id for all roles"; stepping down
        # once picks the per-role variant, which is the one worth showing.
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.0),
            ("type", "/models"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 2.6),
            ("key", "down"), ("wait", 1.0),
            ("key", "enter"),
            ("wait", 2.2),
            ("key", "clear"), ("wait", 0.4),
            ("type", "local-fast"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 1.8),
            ("key", "clear"), ("wait", 0.4),
            ("type", "local-smart"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 1.8),
            ("key", "clear"), ("wait", 0.4),
            ("type", "local-planner"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 3.5),
        ]
    if scenario == "style":
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.0),
            ("type", "/style"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 2.2),
            ("key", "down"), ("wait", 0.5),
            ("key", "down"), ("wait", 0.5),
            ("key", "down"), ("wait", 0.5),
            ("key", "down"), ("wait", 0.9),
            ("key", "enter"),
            ("wait", 3.5),
        ]
    if scenario == "changeapi":
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.0),
            ("type", "/change-api"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 2.4),
            ("type", PLACEHOLDER_KEY),
            ("wait", 0.8),
            ("key", "enter"),
            ("wait", 3.5),
        ]
    if scenario == "toggles":
        return [
            ("await", b"\xe2\x9d\xaf", 30),
            ("wait", 1.0),
            ("type", "/plan"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 3.0),
            ("type", "/verbose"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 3.0),
            ("type", "/plan"),
            ("wait", 0.5),
            ("key", "enter"),
            ("wait", 3.0),
        ]
    raise SystemExit(f"unknown scenario: {scenario}")


def main() -> int:
    if len(sys.argv) not in (5, 6):
        print("usage: guide.py <repo> <home> <workspace> <scenario> [log]", file=sys.stderr)
        return 2
    repo, home, workspace, scenario = sys.argv[1:5]
    log_path = sys.argv[5] if len(sys.argv) == 6 else os.devnull
    steps = steps_for(scenario)
    log = open(log_path, "a", encoding="utf-8")

    env = {
        name: os.environ[name] for name in PASS_THROUGH if name in os.environ
    }
    env.update(
        {name: value for name, value in os.environ.items() if name.startswith("LC_")}
    )
    env["HOME"] = home
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

    def pump(budget: float) -> bytes:
        try:
            data = child.read_nonblocking(size=8192, timeout=budget)
        except pexpect.TIMEOUT:
            return b""
        except (pexpect.EOF, OSError):
            raise SystemExit(0)
        if CPR_QUERY in data:
            child.send(CPR_REPLY)
        return data

    def idle(seconds: float) -> None:
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            pump(0.05)
            sys.stdout.buffer.flush()

    def until(marker: bytes, timeout: float) -> None:
        seen = b""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            seen = (seen + pump(0.2))[-8192:]
            sys.stdout.buffer.flush()
            if marker in seen:
                return
        log.write(f"[{scenario}] marker {marker!r} never appeared\n")
        log.flush()

    for step in steps:
        verb, argument = step[0], step[1]
        if verb == "wait":
            idle(argument)
        elif verb == "await":
            until(argument, step[2])
        elif verb == "key":
            child.send(KEYS[argument])
            idle(0.12)
        elif verb == "type":
            for chunk in _typing(argument):
                child.send(chunk)
                idle(0.035)
        else:
            raise SystemExit(f"unknown step: {verb}")

    child.send("/bye\r")
    idle(4.0)
    sys.stdout.buffer.flush()
    try:
        child.close(force=True)
    except Exception:
        pass
    log.close()
    return 0


def _typing(text: str, size: int = 2):
    """Send text in small pieces so the recording shows it being typed."""
    for index in range(0, len(text), size):
        yield text[index:index + size].encode("utf-8")


if __name__ == "__main__":
    raise SystemExit(main())
