#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Register the installed Kvist CLI as a Zed ACP external agent.

Zed talks to coding agents through the Agent Client Protocol and finds them
under ``agent_servers`` in its settings file. This installer writes exactly one
member of that file. Zed's settings are JSONC, so the edit is a character-range
replacement: every comment, key and line ending the user has survives.

    python scripts/installer/install_zed.py
    python scripts/installer/install_zed.py --uninstall
    python scripts/installer/install_zed.py --settings /path/to/settings.json

It never builds, copies or starts a Kvist binary. The entry points at the
standalone ``kvist-core`` that ``install_cli.py`` already installed, so run that
one first. The ``kvist`` wrapper is deliberately not used: it forces
``--workspace "$PWD"``, which fights the workspace ACP hands over in
``session/new``, and it is a shell script Windows cannot exec.

Zed launches the entry through the user's own login shell (see
``pristine_config_dir``), so the entry also gives that shell a config directory
of its own. Editing the user's shell startup files instead is not this
installer's business.

Zed reads the file again on its own ("You do not need to restart Zed."), so
nothing has to be reloaded afterwards.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from _common import IS_WINDOWS, log, warn
from install_cli import CORE_NAME, DEFAULT_LIB_DIR

import jsonc_settings

DEFAULT_AGENT_NAME = "Kvist"
# Written next to the settings file, and only when it is not already there: the
# first change is the one worth undoing, and a later run must not destroy the
# user's own edits since then.
BACKUP_SUFFIX = ".kvist-backup"
ENTRY_INDENT = "  "
PRISTINE_CONFIG_DIR_NAME = "acp-pristine-config"


# --- Where Zed keeps its settings ---------------------------------------------


def candidate_settings_paths() -> list[Path]:
    """Places Zed may keep ``settings.json``, most likely first.

    Zed documents ``~/.config/zed/settings.json`` on macOS and on Linux without a
    custom ``XDG_CONFIG_HOME``. It does not document a Windows location -- only
    that settings live in the user profile -- so the ``APPDATA`` guess is listed
    but never chosen over an existing file, and never created.

    :returns: the candidates to try, in order, without duplicates.
    """
    paths: list[Path] = []
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        paths.append(Path(xdg) / "zed" / "settings.json")
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "Zed" / "settings.json")
    paths.append(Path.home() / ".config" / "zed" / "settings.json")
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def default_settings_path() -> Path:
    """The location to create when no candidate exists yet.

    :returns: ``$XDG_CONFIG_HOME/zed/settings.json`` when set, else the documented
        ``~/.config/zed/settings.json``.
    """
    xdg = os.environ.get("XDG_CONFIG_HOME")
    if xdg:
        return Path(xdg) / "zed" / "settings.json"
    return Path.home() / ".config" / "zed" / "settings.json"


def discover_settings_path(explicit: Path | None) -> Path:
    """The settings file this run edits.

    :param explicit: the ``--settings`` value, which always wins.
    :returns: an existing file, or the path to create on macOS and Linux.
    :raises SystemExit: when nothing exists on Windows, where the location is
        undocumented and inventing one would silently do nothing.
    """
    if explicit is not None:
        return explicit
    candidates = candidate_settings_paths()
    existing = [path for path in candidates if path.is_file()]
    if len(existing) > 1:
        warn(
            "Zed settings were found in more than one place; editing "
            f"{existing[0]}. Checked: " + ", ".join(str(path) for path in existing)
        )
    if existing:
        return existing[0]
    if not IS_WINDOWS:
        return default_settings_path()
    checked = "\n  ".join(str(path) for path in candidates)
    raise SystemExit(
        "error: no Zed settings file was found, and Zed does not document where "
        "Windows keeps it.\n"
        f"  Checked:\n  {checked}\n"
        "Open Zed's settings once so it creates the file, or pass --settings."
    )


# --- What gets written -------------------------------------------------------


def resolve_command(lib_dir: Path) -> str:
    """Absolute path of the standalone core binary the entry launches.

    :param lib_dir: where ``install_cli.py`` put the binary.
    :returns: the path Zed should exec.
    :raises SystemExit: when the CLI has not been installed there yet.
    """
    binary = lib_dir / CORE_NAME
    if not binary.is_file():
        raise SystemExit(
            f"error: {binary} is missing.\n"
            "Run scripts/installer/install_cli.py first. This installer only writes the "
            "Zed settings entry and never builds or copies a binary."
        )
    return str(binary)


def pristine_config_dir(command: str) -> Path:
    """Directory the entry hands to the launch as its ``XDG_CONFIG_HOME``.

    Zed spawns an external agent as ``<login shell> -c '<command> <args>'`` with
    no ``--no-config``, and the agent's own argv cannot add one: the shell runs
    before the binary does. A shell startup that prints anything -- a greeting,
    a prompt tool, a system-info banner -- puts those bytes on stdout ahead of
    the first JSON-RPC frame, and Zed rejects the whole stream as invalid UTF-8
    and reports a never-received ``initialize``. Pointing ``XDG_CONFIG_HOME``
    here makes the wrapper shell find no ``config.fish`` or ``config`` of the
    user's to load.

    The directory starts empty and holds no user startup file. fish drops its
    own skeleton into it on first launch (``fish/config.fish`` containing only
    an ``if status is-interactive`` block), which runs nothing under ``fish -c``.

    The agent keeps its own configuration: it lives in ``~/.kvist/`` and is not
    under XDG. What this costs is that tools the agent spawns also stop reading
    ``~/.config/<tool>/config``; git still reads ``~/.gitconfig`` and the system
    config.

    :param command: absolute path of the core binary the entry launches.
    :returns: the directory to put in the entry's ``env``, next to that binary.
    """
    return Path(command).parent / PRISTINE_CONFIG_DIR_NAME


def agent_entry(command: str) -> str:
    """JSON text for one ``agent_servers`` member.

    Only the keys Zed documents for a custom agent server: ``type``, ``command``,
    ``args``, ``env``. There is no icon field and no credentials field -- an API
    key must never be written into an editor's settings.

    :param command: absolute path of the core binary.
    :returns: pretty-printed JSON for the entry's value.
    """
    return json.dumps(
        {
            "type": "custom",
            "command": command,
            "args": ["--acp"],
            "env": {"XDG_CONFIG_HOME": str(pristine_config_dir(command))},
        },
        indent=2,
    )


# --- Reading and writing the file --------------------------------------------


def read_settings(path: Path) -> str:
    """The file's exact text, including its line endings.

    Read as bytes and decoded, never through text mode: universal newlines would
    rewrite a CRLF file as LF on the way back out.

    :param path: the settings file.
    :returns: the whole document.
    :raises SystemExit: when the file is missing or not UTF-8.
    """
    try:
        return path.read_bytes().decode("utf-8")
    except FileNotFoundError:
        return "{}\n"
    except UnicodeDecodeError as error:
        raise SystemExit(f"error: {path} is not UTF-8 text: {error}") from error


def backup_settings(path: Path) -> Path | None:
    """Copy the file aside once, before the first change this installer makes.

    :param path: the settings file.
    :returns: the backup path, or None when there was nothing to copy or a backup
        from an earlier run is already there.
    """
    if not path.is_file():
        return None
    backup = path.with_name(path.name + BACKUP_SUFFIX)
    if backup.exists():
        return None
    shutil.copy2(path, backup)
    return backup


def write_settings_file(path: Path, text: str) -> None:
    """Replace ``path`` atomically, keeping the mode it already had.

    The temporary file is written and fsynced before the rename, so a crash
    leaves either the old document or the new one and never a half-written file
    Zed would fail to parse.

    :param path: the settings file.
    :param text: the whole document to write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = path.stat().st_mode & 0o777 if path.exists() else 0o600
    descriptor, temporary_name = tempfile.mkstemp(
        dir=path.parent, prefix=f"{path.name}.", suffix=".tmp"
    )
    temporary = Path(temporary_name)
    try:
        # newline="" keeps the newlines exactly as `text` holds them.
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        temporary.chmod(mode)
        temporary.replace(path)
    except OSError:
        temporary.unlink(missing_ok=True)
        raise


# --- Install and remove ------------------------------------------------------


def install(path: Path, name: str, command: str) -> bool:
    """Write the agent entry into the settings file.

    :param path: the settings file.
    :param name: the agent entry name Zed will list.
    :param command: absolute path of the core binary to launch.
    :returns: True when the file changed, False when it already read that way.
    :raises SystemExit: when the document cannot be edited safely.
    """
    # The entry names this directory; it has to be there before Zed runs it.
    pristine_config_dir(command).mkdir(parents=True, exist_ok=True)
    before = read_settings(path)
    try:
        after = jsonc_settings.set_agent_server(before, name, agent_entry(command))
    except jsonc_settings.JsoncError as error:
        raise SystemExit(f"error: cannot edit {path}: {error}") from error
    if after == before:
        log(f"{path} already registers {name!r}; nothing to do")
        return False
    backup = backup_settings(path)
    if backup is not None:
        log(f"backed up {path.name} to {backup.name}")
    write_settings_file(path, after)
    log(f"registered {name!r} in {path}")
    return True


def uninstall(path: Path, name: str) -> bool:
    """Remove the agent entry and nothing else.

    :param path: the settings file.
    :param name: the agent entry name to remove.
    :returns: True when the file changed.
    :raises SystemExit: when the document cannot be edited safely.
    """
    if not path.is_file():
        log(f"{path} does not exist; nothing to remove")
        return False
    before = read_settings(path)
    try:
        after = jsonc_settings.remove_agent_server(before, name)
    except jsonc_settings.JsoncError as error:
        raise SystemExit(f"error: cannot edit {path}: {error}") from error
    if after == before:
        log(f"{path} has no {name!r} entry; nothing to do")
        return False
    write_settings_file(path, after)
    log(f"removed {name!r} from {path}")
    return True


# --- Command line ------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--uninstall", action="store_true", help="Remove the Zed entry instead of writing it.")
    parser.add_argument("--settings", type=Path, help="Edit this settings file instead of looking for one.")
    parser.add_argument(
        "--lib-dir",
        type=Path,
        default=DEFAULT_LIB_DIR,
        help=f"Where install_cli.py put the binary (default: {DEFAULT_LIB_DIR}).",
    )
    parser.add_argument("--name", default=DEFAULT_AGENT_NAME, help=f"Agent entry name (default: {DEFAULT_AGENT_NAME}).")
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Also print the entry that was written or removed.",
    )
    args = parser.parse_args()

    path = discover_settings_path(args.settings)
    if args.uninstall:
        uninstall(path, args.name)
        print(f"\nDone. Removed {args.name!r} from {path}.")
        return

    command = resolve_command(args.lib_dir)
    changed = install(path, args.name, command)
    if args.verbose:
        print(agent_entry(command))
    if changed:
        print(f"\nDone. {args.name} is registered in {path}.")
        print("Zed picks the change up on its own; no restart needed.")
    else:
        print(f"\nDone. {args.name} was already registered in {path}.")


if __name__ == "__main__":
    main()
