# -*- coding: utf-8 -*-
"""Plain plugins: one folder with a ``plugin.py`` in a ``plugins`` folder is one slash command.

The simplest way to extend Kvist, next to the full skill packages (docs/SKILLS.md)::

    # plugins/novibecode/plugin.py   (novibecode.md and any helpers lie next to it)
    \"\"\"Teach instead of writing the code.\"\"\"
    import kvist

    def command():
        return "novibecode"

    def run(request):
        return kvist.read("novibecode.md")

``/novibecode <request>`` then calls ``run`` with the request, and the text it
returns goes to the model as instructions for that request.

Folders: ``<config_dir>/plugins`` for every project and ``<workspace>/plugins``
for one; a project's plugin wins over a global one of the same name.

Finding the commands never runs a plugin: the name is read from the source
(``command`` must return a plain string). ``run`` runs only after the person
approved that exact code (a SHA-256 over every ``.py`` file of the plugin's
folder, per project) and only inside a Bubblewrap sandbox that sees a snapshot
of that folder and the system runtime, with no network, no project, no other
plugin, no HOME and no environment. A plugin that comes with a cloned
repository therefore cannot do anything by being there.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.context.language_utils import choose_localized_text
from kernel.plugins.grants import GrantStore
from kernel.plugins.paths import config_dir
from kernel.plugins.sandbox import ETC_FILES, runner_python, runner_unavailable_reason
from kernel.sandbox.sandbox import RUNTIME_MOUNT_ROOTS, resolve_bubblewrap_path

PLUGINS_DIR = "plugins"
ENTRY_FILE = "plugin.py"
RUNNER_DIR = Path(__file__).resolve().parent / "plain"
RUN_TIMEOUT_SECONDS = 30
_GRANT_PREFIX = "plain:"
_NAME = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_TYPED = re.compile(r"^/(?P<name>[\w-]+)(?:\s+(?P<request>.*))?$", re.DOTALL)
# Typed by the person in a frontend and handled there, so a plugin cannot take them either.
_FRONTEND_NAMES = frozenset({"tui", "quit", "exit", "bye", "q", "clear", "copy", "debug"})


class PluginError(Exception):
    """A plugin cannot be used; the message is meant for the person."""


@dataclass(frozen=True, slots=True)
class PluginCommand:
    name: str
    description: str
    path: Path  # the folder's plugin.py

    @property
    def folder(self) -> Path:
        return self.path.parent


@dataclass(frozen=True, slots=True)
class Discovery:
    commands: dict[str, PluginCommand]
    problems: list[str]


def plugin_folders(workspace: Path) -> list[Path]:
    """Global first, the project's last: a later folder wins a name."""
    return [config_dir() / PLUGINS_DIR, Path(workspace) / PLUGINS_DIR]


def builtin_names() -> frozenset[str]:
    from kernel.agent.prompt_commands import PROMPT_COMMANDS
    from kernel.protocol.slash_commands import SLASH_COMMANDS, STDIO_ONLY_COMMANDS

    names = set(PROMPT_COMMANDS) | _FRONTEND_NAMES
    for command in (*SLASH_COMMANDS, *STDIO_ONLY_COMMANDS):
        names.add(command.name)
        names.update(command.aliases)
    return frozenset(names)


def discover(workspace: Path) -> Discovery:
    commands: dict[str, PluginCommand] = {}
    problems: list[str] = []
    taken = builtin_names()
    for root in plugin_folders(workspace):
        if not root.is_dir():
            continue
        for loose in sorted(root.glob("*.py")):
            problems.append(f"{loose}: a plugin lives in its own folder; move it to {root / loose.stem / ENTRY_FILE}")
        for folder in sorted(path for path in root.iterdir() if path.is_dir() and not path.name.startswith((".", "_"))):
            entry = folder / ENTRY_FILE
            if not entry.is_file():
                # A folder with no Python in it is not a plugin (the global folder also holds installed skills).
                other = sorted(folder.glob("*.py"))
                if other:
                    problems.append(f"{folder}: the plugin's file must be named {ENTRY_FILE} (found {other[0].name})")
                continue
            try:
                command = read_plugin(entry)
            except PluginError as error:
                problems.append(f"{entry}: {error}")
                continue
            if command.name in taken:
                problems.append(f"{entry}: /{command.name} is a built-in Kvist command; pick another name")
                continue
            commands[command.name] = command
    return Discovery(commands, problems)


def read_plugin(path: Path) -> PluginCommand:
    """The command a plugin file declares, read from its source without running it."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (OSError, UnicodeDecodeError) as error:
        raise PluginError(f"cannot read the file: {error}") from error
    except SyntaxError as error:
        raise PluginError(f"Python syntax error on line {error.lineno}: {error.msg}") from error
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    if "command" not in functions:
        raise PluginError('no command() function; add: def command(): return "my-command"')
    if "run" not in functions:
        raise PluginError("no run(request) function; add: def run(request): return \"instructions for Kvist\"")
    if len(functions["run"].args.args) != 1:
        raise PluginError("run must take exactly one argument, the request: def run(request)")
    name = _declared_name(functions["command"])
    if not _NAME.match(name):
        raise PluginError(f'command() returns "{name}"; use lowercase letters, digits and "-", e.g. "no-vibe-code"')
    docstring = ast.get_docstring(tree) or ""
    return PluginCommand(name, docstring.strip().splitlines()[0] if docstring.strip() else "", path)


def _declared_name(function: ast.FunctionDef) -> str:
    body = [node for node in function.body if not _is_docstring(node)]
    returned = body[0].value if len(body) == 1 and isinstance(body[0], ast.Return) else None
    if not (isinstance(returned, ast.Constant) and isinstance(returned.value, str)):
        raise PluginError('command() must only return the name as plain text, e.g. return "novibecode"')
    return returned.value


def _is_docstring(node: ast.stmt) -> bool:
    return isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str)


def typed_command(goal: str, workspace: Path) -> tuple[PluginCommand, str] | None:
    """The plugin a typed ``/name request`` calls and its request; None for anything else."""
    match = _TYPED.match(goal.strip())
    if match is None:
        return None
    command = discover(workspace).commands.get(match["name"].casefold())
    if command is None:
        return None
    return command, (match["request"] or "").strip()


def expand(agent: Any, command: PluginCommand, request: str) -> str:
    """The goal for the model: the request and what the plugin answered for it. Raises PluginError."""
    workspace = Path(agent.executor.workspace)
    language = choose_localized_text("ru", "en", request or agent._state.response_language)
    reason = runner_unavailable_reason()
    if reason:
        raise PluginError(f"/{command.name} cannot run here: {reason.replace('executable skills', 'plugins')}")
    with tempfile.TemporaryDirectory(prefix="kvist-plugin-") as scratch:
        # The person approves, and the sandbox runs, this copy: a file changed in between changes nothing.
        snapshot = snapshot_folder(command, Path(scratch))
        _approve(agent, command, snapshot, workspace, language)
        text = run_plugin(command, snapshot, request)
    shown = request or "(nothing typed after the command)"
    return (
        f"{shown}\n\nThe person started this with /{command.name}, a plugin command they approved "
        f"({command.folder}). Its instructions for this request:\n"
        f"<plugin_instructions command=\"{command.name}\">\n{text}\n</plugin_instructions>\n"
        "Follow them as the person's own wishes, within your usual rules."
    )


def snapshot_folder(command: PluginCommand, scratch: Path) -> Path:
    target = scratch / "plugin"
    try:
        shutil.copytree(command.folder, target, symlinks=True, ignore=shutil.ignore_patterns("__pycache__"))
    except (OSError, shutil.Error) as error:
        raise PluginError(f"{command.folder}: cannot read the folder: {error}") from error
    return target


def code_files(folder: Path) -> list[Path]:
    return sorted(path for path in folder.rglob("*.py") if path.is_file())


def code_digest(folder: Path) -> str:
    """One SHA-256 over the name and content of every Python file: everything that can run."""
    digest = hashlib.sha256()
    for path in code_files(folder):
        digest.update(path.relative_to(folder).as_posix().encode("utf-8") + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return "sha256:" + digest.hexdigest()


def _approve(agent: Any, command: PluginCommand, snapshot: Path, workspace: Path, language: str) -> None:
    """Return once the person approved exactly this code in this project; raise PluginError otherwise."""
    digest = code_digest(snapshot)
    grants = GrantStore()
    grant_id = _GRANT_PREFIX + str(command.folder.resolve())
    if grants.get(grant_id, digest, workspace) is not None:
        return
    if not getattr(agent.config, "interactive", False):
        raise PluginError(choose_localized_text(
            f"Плагин {command.folder} ещё не одобрен, а спросить здесь некого. Запусти его один раз в обычном Kvist.",
            f"The plugin {command.folder} is not approved yet and nobody can answer here. Run it once in an "
            "interactive Kvist.",
            language,
        ))
    changed = bool(grants.granted_digests(grant_id))
    files = [path.relative_to(snapshot).as_posix() for path in code_files(snapshot)]
    title, body = approval_text(command, files, digest, changed, language)
    agent.control.awaiting_input(True)
    try:
        approved = bool(agent._ui.confirm(title, body))
    finally:
        agent.control.awaiting_input(False)
    if not approved:
        raise PluginError(choose_localized_text(
            f"/{command.name} не запущен: ты его не разрешил.",
            f"/{command.name} did not run: you did not allow it.",
            language,
        ))
    grants.put(grant_id, digest, workspace, {})


def approval_text(
    command: PluginCommand, files: list[str], digest: str, changed: bool, language: str
) -> tuple[str, str]:
    ru = language == "ru"
    lines = [str(command.folder), ("Код: " if ru else "Code: ") + ", ".join(files),
             f"SHA-256: {digest.removeprefix('sha256:')}"]
    if changed:
        lines.append("Код изменился с прошлого разрешения." if ru else "The code changed since you last allowed it.")
    lines.append("")
    lines.append(
        "Код плагина запускается в песочнице: он видит только свою папку, без сети, проекта, ключей и домашней папки."
        if ru else
        "The plugin's code runs in a sandbox: it sees only its own folder, with no network, project, keys or HOME."
    )
    title = f"Запустить плагин /{command.name}?" if ru else f"Run the plugin /{command.name}?"
    return title, "\n".join(lines)


def run_plugin(command: PluginCommand, snapshot: Path, request: str) -> str:
    """``run(request)`` of the plugin copied to ``snapshot``, in the sandbox (checked available by the caller).
    Raises PluginError."""
    try:
        finished = subprocess.run(
            sandbox_argv(snapshot), input=request,
            capture_output=True, text=True, timeout=RUN_TIMEOUT_SECONDS, check=False,
        )
    except subprocess.TimeoutExpired as error:
        raise PluginError(f"/{command.name}: run() did not finish in {RUN_TIMEOUT_SECONDS} s") from error
    return _answer(command, finished)


def _answer(command: PluginCommand, finished: subprocess.CompletedProcess[str]) -> str:
    try:
        answer = json.loads(finished.stdout)
    except json.JSONDecodeError as error:
        details = finished.stderr.strip() or f"exit code {finished.returncode}"
        raise PluginError(f"/{command.name} stopped without an answer: {details}") from error
    if "error" in answer:
        raise PluginError(f"/{command.name} failed:\n{answer['error']}")
    return answer["text"]


def sandbox_argv(snapshot: Path) -> list[str]:
    bwrap = resolve_bubblewrap_path()
    python = runner_python()
    assert bwrap is not None and python is not None
    argv = [
        bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL", "--clearenv",
        "--setenv", "PATH", "/usr/bin:/bin",
        "--setenv", "HOME", "/tmp/home",
        "--setenv", "TMPDIR", "/tmp",
        "--setenv", "LANG", "C.UTF-8",
        "--setenv", "KVIST_PLUGIN_DIR", "/kvist/plugin",
    ]
    for root in RUNTIME_MOUNT_ROOTS:
        if Path(root).exists():
            argv += ["--ro-bind", root, root]
    for path in ETC_FILES:
        if Path(path).exists():
            argv += ["--ro-bind", str(Path(path).resolve()), path]
    argv += [
        "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home",
        "--ro-bind", str(RUNNER_DIR), "/kvist/runner",
        "--ro-bind", str(snapshot), "/kvist/plugin",
        "--chdir", "/kvist/plugin",
        "--", python, "-I", "-B", "/kvist/runner/run_plugin.py", f"/kvist/plugin/{ENTRY_FILE}",
    ]
    return argv
