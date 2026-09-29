# -*- coding: utf-8 -*-
"""What Kvist runs inside, and the commands that would stop it.

Kvist is a process in a chain: its own interpreter, the frontend that spawned
it, the editor or terminal around that, the desktop session around everything.
The same chain carries the person's open work. A command that stops any link
ends the turn mid-step, where Kvist can no longer report what happened.

Two consumers. ``describe_host`` gives the runtime prompt one line naming the
host, so the model knows where it lives. ``self_termination_denial`` is a hard
shell guard: such a step is handed to the person instead of being run.

The chain is read from /proc (Linux). Elsewhere it is empty, and the guard
keeps only its static checks (reboot, ending the login or desktop session).
"""
from __future__ import annotations

import os
import re
import shlex
from dataclasses import dataclass
from pathlib import Path

_PROC = Path("/proc")

HOST_VSCODE = "vscode"
HOST_JETBRAINS = "jetbrains"
HOST_ZED = "zed"

_HOST_LABELS = {
    HOST_VSCODE: "VS Code",
    HOST_JETBRAINS: "JetBrains IDE",
    HOST_ZED: "Zed",
}

_ZED_NAMES = frozenset({"zed", "zed-editor", "zeditor"})
_VSCODE_NAMES = frozenset({"code", "code-oss", "codium", "vscodium", "code-insiders"})
_JETBRAINS_NAMES = frozenset({
    "idea", "idea.sh", "pycharm", "pycharm.sh", "clion", "clion.sh",
    "goland", "goland.sh", "webstorm", "webstorm.sh", "rider", "rider.sh",
    "rustrover", "rustrover.sh", "phpstorm", "phpstorm.sh", "rubymine",
    "rubymine.sh", "datagrip", "datagrip.sh", "dataspell", "dataspell.sh",
})

# Every handoff ends the same way: the conversation is on disk, and which
# command reopens it depends on whether the turn ended finished.
_CONTINUE = (
    "The conversation stays saved on disk: afterwards /session reopens it and "
    "/resume continues an unfinished run."
)
_HANDOFF = {
    HOST_VSCODE: (
        "in VS Code run Ctrl+Shift+P > 'Developer: Reload Window' (or close and "
        "reopen VS Code), then open the Kvist panel."
    ),
    HOST_JETBRAINS: (
        "restart the JetBrains IDE (File > Exit and open it again, or the "
        "'Restart IDE' button the IDE offers after a plugin change), then open "
        "the Kvist tool window."
    ),
    HOST_ZED: "quit Zed and open it again, then reopen the Kvist thread.",
}
_HANDOFF_DEFAULT = "run this step themselves, then start Kvist in this project again."
_HANDOFF_SESSION = (
    "run this step themselves after saving their work; it ends the login or "
    "desktop session. Then start Kvist in this project again."
)

_SHELLS = frozenset({"sh", "bash", "dash", "zsh", "fish", "ksh"})
_SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&", "|&", ";;", "\n"})
_POWER_COMMANDS = frozenset({"reboot", "poweroff", "halt", "shutdown", "kexec"})

_SYSTEMCTL_ENDING_VERBS = frozenset({
    "reboot", "poweroff", "halt", "kexec", "soft-reboot", "exit",
    "rescue", "emergency",
})
_ENDING_TARGETS = frozenset({
    "reboot.target", "poweroff.target", "halt.target", "kexec.target",
    "soft-reboot.target", "rescue.target", "emergency.target", "exit.target",
    "ctrl-alt-del.target",
})
_STOPPING_VERBS = frozenset({
    "stop", "restart", "kill", "try-restart", "reload-or-restart",
    "try-reload-or-restart",
})
_SESSION_UNITS = frozenset({
    "display-manager", "sddm", "gdm", "gdm3", "lightdm", "ly", "greetd",
    "lxdm", "xdm", "graphical-session.target", "hyprland-session.target",
})
_LOGINCTL_ENDING_VERBS = frozenset({
    "terminate-session", "terminate-user", "kill-session", "kill-user",
    "poweroff", "reboot", "halt", "kexec",
})
_HYPRCTL_ENDING_RE = re.compile(r"\bdispatch\s+(?:exit|killactive|forcekillactive)\b")

# pkill options that take a value, short and long; the rest are flags.
_PKILL_VALUE_SHORT = frozenset("dgGPstuUF")
_PKILL_VALUE_LONG = frozenset({
    "--delimiter", "--pgroup", "--group", "--parent", "--session", "--terminal",
    "--euid", "--uid", "--pidfile", "--signal", "--ns", "--nslist", "--older", "--env",
})
_PKILL_FLAG_LONG = {
    "--full": "f", "--ignore-case": "i", "--exact": "x", "--inverse": "v",
}
_KILLALL_VALUE_SHORT = frozenset("suoynZ")
_KILLALL_VALUE_LONG = frozenset({
    "--signal", "--user", "--older-than", "--younger-than", "--ns", "--context",
})
_KILLALL_FLAG_LONG = {"--ignore-case": "I", "--regexp": "r", "--exact": "e"}
# A signal given as an option: -9, -HUP, -SIGKILL, -RTMIN+1. At least two
# letters, so single-letter options such as pkill's -P stay options.
_SIGNAL_OPTION_RE = re.compile(r"^-(?:\d+|[A-Z][A-Z0-9+-]+)$")


@dataclass(frozen=True, slots=True)
class HostProcess:
    pid: int
    name: str  # the kernel's comm, at most 15 characters
    argv: tuple[str, ...]

    @property
    def program(self) -> str:
        return os.path.basename(self.argv[0]) if self.argv else ""


def ancestor_chain(pid: int | None = None) -> tuple[HostProcess, ...]:
    """Kvist's own process first, then each parent up to (not including) init."""
    chain: list[HostProcess] = []
    current = os.getpid() if pid is None else pid
    seen: set[int] = set()
    while current > 1 and current not in seen:
        seen.add(current)
        stat = _read_stat(current)
        if stat is None:
            break
        name, parent = stat
        chain.append(HostProcess(current, name, _read_argv(current)))
        current = parent
    return tuple(chain)


def _read_stat(pid: int) -> tuple[str, int] | None:
    try:
        raw = (_PROC / str(pid) / "stat").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    # comm sits in parentheses and may itself hold spaces and ')', so the
    # fields start after the last ')': state, then ppid.
    start = raw.find("(")
    end = raw.rfind(")")
    if start == -1 or end < start:
        return None
    fields = raw[end + 1:].split()
    try:
        return raw[start + 1:end], int(fields[1])
    except (IndexError, ValueError):
        return None


def _read_argv(pid: int) -> tuple[str, ...]:
    try:
        raw = (_PROC / str(pid) / "cmdline").read_bytes()
    except OSError:
        return ()
    return tuple(
        part.decode("utf-8", errors="replace") for part in raw.split(b"\0") if part
    )


def detect_host(chain: tuple[HostProcess, ...]) -> str:
    """The nearest editor Kvist runs under, or "" for a terminal or unknown host."""
    for process in chain:
        host = _host_of(process)
        if host:
            return host
    return ""


def _host_of(process: HostProcess) -> str:
    names = {process.name.lower(), process.program.lower()}
    if names & _ZED_NAMES:
        return HOST_ZED
    if names & _VSCODE_NAMES:
        return HOST_VSCODE
    if names & _JETBRAINS_NAMES:
        return HOST_JETBRAINS
    if process.name == "java" and any(
        arg.startswith(("com.intellij.", "-Didea.")) for arg in process.argv
    ):
        return HOST_JETBRAINS
    if "visual-studio-code" in (process.argv[0].lower() if process.argv else ""):
        return HOST_VSCODE
    return ""


def describe_host(chain: tuple[HostProcess, ...], host: str) -> str:
    """One prompt line: the host and the processes Kvist runs inside."""
    if not chain:
        return ""
    links = " <- ".join(f"{process.name} ({process.pid})" for process in chain[:8])
    label = _HOST_LABELS.get(host, "terminal or unrecognised host")
    return (
        f"{label}; Kvist runs inside: {links}. Stopping or restarting any of "
        "these ends this turn; that step is handed to the person. A restart "
        f"loses nothing: {_CONTINUE}"
    )


def self_termination_denial(
    segment: list[str], chain: tuple[HostProcess, ...], host: str
) -> str | None:
    """Why one pipe segment would stop Kvist's host, or None if it would not."""
    what = _what_segment_stops(segment, chain, depth=0)
    if what is None:
        return None
    handoff = _HANDOFF_SESSION if what == "session" else _HANDOFF.get(host, _HANDOFF_DEFAULT)
    target = (
        "the login or desktop session Kvist runs in" if what == "session"
        else f"{what}, a process Kvist itself runs inside"
    )
    return (
        f"self_termination: '{shlex.join(segment)}' would stop {target}, cutting "
        "this turn off mid-step with no report. Do not run it and do not look for "
        "another command that does the same. Finish every step before it, then "
        f"hand this step to the person: {handoff} {_CONTINUE}"
    )


def _what_segment_stops(
    segment: list[str], chain: tuple[HostProcess, ...], depth: int
) -> str | None:
    if not segment:
        return None
    program = _program_name(segment[0])
    args = segment[1:]
    if program in _SHELLS:
        return _shell_script_stops(args, chain, depth)
    if program in _POWER_COMMANDS:
        return "session"
    if program == "systemctl":
        return "session" if _systemctl_ends_session(args) else None
    if program == "loginctl":
        return "session" if any(arg in _LOGINCTL_ENDING_VERBS for arg in args) else None
    if program == "hyprctl":
        return "session" if _HYPRCTL_ENDING_RE.search(" ".join(args)) else None
    if program == "kill":
        return _describe(_kill_hits(args, chain))
    if program == "pkill":
        return _describe(_pkill_hits(args, chain))
    if program == "killall":
        return _describe(_killall_hits(args, chain))
    return None


def _program_name(token: str) -> str:
    name = os.path.basename(token.replace("\\", "/")).lower()
    return name[:-4] if name.endswith(".exe") else name


def _describe(hit: HostProcess | str | None) -> str | None:
    if hit is None or isinstance(hit, str):
        return hit
    return f"{hit.name} (pid {hit.pid})"


def _shell_script_stops(
    args: list[str], chain: tuple[HostProcess, ...], depth: int
) -> str | None:
    """Look inside `sh -c SCRIPT`: the guard must not be one quoting layer deep."""
    if depth >= 3:
        return None
    script = _shell_script(args)
    if script is None:
        return None
    lexer = shlex.shlex(script, posix=True, punctuation_chars=True)
    lexer.whitespace_split = True
    try:
        tokens = list(lexer)
    except ValueError:
        return None
    command: list[str] = []
    for token in [*tokens, ";"]:
        if token not in _SHELL_OPERATORS:
            command.append(token)
            continue
        what = _what_segment_stops(command, chain, depth + 1)
        if what is not None:
            return what
        command = []
    return None


def _shell_script(args: list[str]) -> str | None:
    """The SCRIPT of `sh -c SCRIPT`, also inside an option cluster such as -lc."""
    for index, arg in enumerate(args):
        if arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]:
            return args[index + 1] if index + 1 < len(args) else None
    return None


def _positionals(args: list[str]) -> list[str]:
    return [arg for arg in args if not arg.startswith("-")]


def _systemctl_ends_session(args: list[str]) -> bool:
    positionals = _positionals(args)
    if not positionals:
        return False
    verb, units = positionals[0], positionals[1:]
    if verb in _SYSTEMCTL_ENDING_VERBS:
        return True
    if verb == "isolate":
        # Isolating anything but the graphical target stops the desktop.
        return any(unit != "graphical.target" for unit in units)
    if verb == "start":
        return any(unit in _ENDING_TARGETS for unit in units)
    if verb in _STOPPING_VERBS:
        return any(unit.removesuffix(".service") in _SESSION_UNITS for unit in units)
    return False


def _kill_hits(args: list[str], chain: tuple[HostProcess, ...]) -> HostProcess | str | None:
    targets: list[str] = []
    signal_seen = False
    options_done = False
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if options_done or not arg.startswith("-") or arg == "-":
            targets.append(arg)
            continue
        if arg == "--":
            options_done = True
        elif arg in ("-l", "-L", "--list", "--table"):
            return None
        elif arg in ("-s", "-n", "--signal", "-q", "--queue"):
            signal_seen = True
            index += 1
        elif arg.startswith("--signal="):
            signal_seen = True
        elif not signal_seen:
            signal_seen = True
        else:
            # After the signal a negative number is a target: `kill -9 -1`.
            targets.append(arg)
    pids = {process.pid: process for process in chain}
    for target in targets:
        try:
            pid = int(target)
        except ValueError:
            continue
        if pid == -1:
            return "every process the user owns"
        hit = pids.get(abs(pid))
        if hit is not None:
            return hit
    return None


def _parse_options(
    args: list[str],
    value_short: frozenset[str],
    value_long: frozenset[str],
    flag_long: dict[str, str],
) -> tuple[set[str], dict[str, str], list[str]]:
    """Split pkill/killall style arguments into flags, option values and positionals."""
    flags: set[str] = set()
    values: dict[str, str] = {}
    positionals: list[str] = []
    index = 0
    while index < len(args):
        arg = args[index]
        index += 1
        if arg == "--":
            positionals.extend(args[index:])
            break
        if not arg.startswith("-") or arg == "-":
            positionals.append(arg)
            continue
        if arg.startswith("--"):
            name, _, inline = arg.partition("=")
            if name in flag_long:
                flags.add(flag_long[name])
            elif name in value_long:
                if not inline and index < len(args):
                    inline = args[index]
                    index += 1
                values[name] = inline
            continue
        if _SIGNAL_OPTION_RE.match(arg):
            continue
        for offset, letter in enumerate(arg[1:], start=1):
            if letter not in value_short:
                flags.add(letter)
                continue
            value = arg[offset + 1:]
            if not value and index < len(args):
                value = args[index]
                index += 1
            values["-" + letter] = value
            break
    return flags, values, positionals


def _pkill_hits(args: list[str], chain: tuple[HostProcess, ...]) -> HostProcess | str | None:
    flags, values, patterns = _parse_options(
        args, _PKILL_VALUE_SHORT, _PKILL_VALUE_LONG, _PKILL_FLAG_LONG
    )
    if "v" in flags:
        return "every process except the pattern, Kvist's host among them"
    parents = values.get("-P") or values.get("--parent")
    if parents:
        hit = _child_of_listed_parent(parents, chain)
        if hit is not None:
            return hit
    regex_flags = re.IGNORECASE if "i" in flags else 0
    for pattern in patterns:
        try:
            compiled = re.compile(pattern, regex_flags)
        except re.error:
            continue  # pkill itself refuses an invalid pattern
        match = compiled.fullmatch if "x" in flags else compiled.search
        for process in chain:
            subject = " ".join(process.argv) if "f" in flags else process.name
            if subject and match(subject):
                return process
    return None


def _child_of_listed_parent(
    parents: str, chain: tuple[HostProcess, ...]
) -> HostProcess | None:
    listed = {item.strip() for item in parents.split(",")}
    for child, parent in zip(chain, chain[1:]):
        if str(parent.pid) in listed:
            return child
    return None


def _killall_hits(args: list[str], chain: tuple[HostProcess, ...]) -> HostProcess | None:
    flags, _values, names = _parse_options(
        args, _KILLALL_VALUE_SHORT, _KILLALL_VALUE_LONG, _KILLALL_FLAG_LONG
    )
    if "l" in flags:
        return None
    fold = "I" in flags
    for name in names:
        for process in chain:
            if _killall_matches(name, process, regex="r" in flags, fold=fold):
                return process
    return None


def _killall_matches(name: str, process: HostProcess, *, regex: bool, fold: bool) -> bool:
    candidates = [process.name, process.program]
    if regex:
        try:
            compiled = re.compile(name, re.IGNORECASE if fold else 0)
        except re.error:
            return False
        return any(compiled.search(candidate) for candidate in candidates if candidate)
    if fold:
        name = name.casefold()
        candidates = [candidate.casefold() for candidate in candidates]
    # killall compares a long name's first 15 characters with the truncated comm.
    return name in candidates or (len(name) > 15 and name[:15] == candidates[0])
