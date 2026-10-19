# -*- coding: utf-8 -*-
"""``kvist --skill-op`` and ``kvist --memory-op``: the person's own management of skills and notes.

These are operations a person runs (or a frontend runs on their behalf after
its own dialog); the model has no tool that reaches them. Every op prints one
JSON object when stdout is not a terminal (or with ``--json``); on a terminal
the result is drawn by ``skill_ops_view``. ``approve`` and ``install-deps``
show what they will allow or download and ask on the terminal unless ``--yes``
is given; without a terminal and without ``--yes`` they refuse instead of
assuming consent.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

from kernel.plugins import bundled
from kernel.plugins.bundled import decline as decline_bundled
from kernel.plugins.bundled import restore as restore_bundled
from kernel.plugins.bundled import sync_bundled
from kernel.plugins.grants import GrantStore
from kernel.plugins.paths import workspace_skill_dir
from kernel.plugins.registry import RegistryError, SkillRegistry
from kernel.plugins.manifest import ManifestError

SKILL_OPS = ("list", "show", "install", "approve", "revoke", "install-deps", "rollback", "uninstall",
             "remove-version", "reset-data", "check")
_MEMORY_OPS_WITHOUT_PAYLOAD = ("status", "list", "pause", "resume")


class OpError(RuntimeError):
    pass


def run_skill_op(op: str, *, workspace: Path, skill: str | None, path: str | None, version: str | None,
                 assume_yes: bool, language: str = "en") -> dict[str, Any]:
    registry = SkillRegistry()
    grants = GrantStore()
    problems = sync_bundled(registry)
    result = _skill_op(op, registry, grants, workspace=workspace, skill=skill, path=path, version=version,
                       assume_yes=assume_yes, language=language)
    if problems:
        result["warnings"] = problems
    return result


def _skill_op(op: str, registry: SkillRegistry, grants: GrantStore, *, workspace: Path, skill: str | None,
              path: str | None, version: str | None, assume_yes: bool, language: str) -> dict[str, Any]:
    if op == "list":
        return {"ok": True, "skills": [_entry_row(entry, grants, workspace, language) for entry in registry.catalog()]}
    if op == "install":
        if not path:
            raise OpError("--skill-path is required for install")
        source, origin = _package_source(path)
        result = registry.install(source, origin=origin)
        if origin == "bundled":
            restore_bundled(registry, result.skill_id)
        # The person installs this version on purpose: it becomes the active
        # one, and the previous one stays installed for --skill-op rollback.
        registry.activate(result.skill_id, result.version, result.digest)
        entry = registry.entry(result.skill_id)
        return {"ok": True, "installed": result.skill_id, "version": result.version, "digest": result.digest,
                "created": result.created, "active_version": entry.version if entry else None,
                "available": entry.available if entry else False, "reason": entry.reason if entry else "",
                "note": "Approval happens when it is first attached in this workspace, or with --skill-op approve."}
    if op == "check" and path:
        from kernel.plugins.checks import check_package

        report = check_package(Path(path).resolve(), registry=registry)
        return {"ok": report.ok, "report": report.as_dict(), "summary": report.summary()}
    entry = registry.entry(_require(skill, "--skill"))
    if entry is None:
        raise OpError(f"no installed skill {skill!r}")
    if op == "show":
        row = _entry_row(entry, grants, workspace, language)
        row["versions"] = registry.versions(entry.id)
        if entry.manifest is not None:
            from kernel.plugins.skills import approval_text

            row["approval_text"] = approval_text(entry, language, [])[1]
        return {"ok": True, "skill": row}
    if op == "approve":
        return _approve(entry, grants, workspace, language, assume_yes)
    if op == "revoke":
        return {"ok": True, "revoked": grants.revoke(entry.id, workspace)}
    if op == "install-deps":
        return _install_deps(registry, entry, assume_yes)
    if op == "rollback":
        return {"ok": True, "active_version": registry.rollback(entry.id),
                "note": "Rolling back the code does not undo what the newer version already did."}
    if op == "remove-version":
        registry.remove_version(entry.id, _require(version, "--skill-version"))
        return {"ok": True, "removed_version": version}
    if op == "uninstall":
        shipped = any(info.get("origin") == "bundled" for info in registry.versions(entry.id).values())
        registry.uninstall(entry.id)
        grants.revoke(entry.id)
        note = "Data the skill kept in workspaces stays; remove it with --skill-op reset-data per workspace."
        if shipped:
            decline_bundled(registry, entry.id)
            note += (f" It ships with Kvist and will not be installed again at startup; to bring it back: "
                     f"kvist --skill-op install --skill-path bundled:{entry.id}")
        return {"ok": True, "uninstalled": entry.id, "note": note}
    if op == "reset-data":
        target = workspace_skill_dir(workspace, entry.id)
        existed = target.exists()
        shutil.rmtree(target, ignore_errors=True)
        return {"ok": True, "removed": existed, "path": str(target)}
    if op == "check":
        from kernel.plugins.checks import check_package

        report = check_package(entry.version_dir, registry=registry)
        return {"ok": report.ok, "report": report.as_dict(), "summary": report.summary()}
    raise OpError(f"unknown skill op {op!r}")


def _package_source(path: str) -> tuple[Path, str]:
    """A folder, or ``bundled:<id>`` for a skill shipped with Kvist."""
    if path.startswith("bundled:"):
        name = path.split(":", 1)[1]
        source = bundled.BUNDLED_ROOT / name
        if not name or "/" in name or not source.is_dir():
            available = sorted(item.name for item in source.parent.iterdir() if item.is_dir())
            raise OpError(f"no bundled skill {name!r}; bundled: {', '.join(available)}")
        return source, "bundled"
    return Path(path), "local"


def _entry_row(entry, grants: GrantStore, workspace: Path, language: str) -> dict[str, Any]:
    manifest = entry.manifest
    grant = grants.get(entry.id, entry.digest, workspace) if entry.digest else None
    return {
        "id": entry.id,
        "name": entry.label(language),
        "summary": entry.short(language),
        "version": entry.version,
        "kind": manifest.kind if manifest else None,
        "available": entry.available,
        "reason": entry.reason,
        "approved_here": grant is not None,
        "capabilities": [request.name for request in manifest.capabilities] if manifest else [],
        "tools": [tool.name for tool in manifest.tools] if manifest else [],
        "source": manifest.source if manifest else {},
        "digest": entry.digest,
    }


def _approve(entry, grants: GrantStore, workspace: Path, language: str, assume_yes: bool) -> dict[str, Any]:
    from kernel.plugins.skills import approval_text

    if entry.manifest is None:
        raise OpError(entry.reason)
    title, body = approval_text(entry, language, grants.granted_digests(entry.id))
    if not assume_yes:
        _ask(f"{title}\n\n{body}\n")
    grant = grants.put(entry.id, entry.digest, workspace, entry.manifest.required_capabilities())
    return {"ok": True, "approved": entry.id, "version": entry.version, "capabilities": sorted(grant.capabilities)}


def _install_deps(registry: SkillRegistry, entry, assume_yes: bool) -> dict[str, Any]:
    from kernel.plugins.deps import PACKAGE_INDEX, install_dependencies

    manifest = entry.manifest
    if manifest is None:
        raise OpError(entry.reason)
    if not assume_yes:
        _ask(f"Download and install for {manifest.id} {manifest.version} (binary wheels only, from "
             f"{', '.join(PACKAGE_INDEX)}):\n  " + "\n  ".join(manifest.python_deps) + "\n")
    path = install_dependencies(registry, manifest, progress=lambda line: print(line, file=sys.stderr))
    return {"ok": True, "environment": str(path), "pins": list(manifest.python_deps)}


def _ask(text: str) -> None:
    if not sys.stdin.isatty():
        raise OpError("this needs your confirmation on a terminal, or --yes")
    sys.stderr.write(text)
    sys.stderr.write("Proceed? [y/N] ")
    sys.stderr.flush()
    if sys.stdin.readline().strip().lower() not in ("y", "yes", "д", "да"):
        raise OpError("cancelled")


def run_memory_op(op: str, *, workspace: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Memory as the person manages it: they may confirm, rewrite and forget anything, and pause the rest."""
    from kernel.memory.commands import run_op

    return run_op(op, workspace, payload)


def run_mcp_op(op: str, *, name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    """MCP servers as the person configures them; secrets in env are never printed back."""
    from kernel.plugins.mcp import McpError, load_servers, remove_server, save_server

    if op == "list":
        servers = load_servers()
        return {"ok": True, "servers": [
            {"name": server.name, "command": server.command, "sandbox": server.sandbox, "network": server.network,
             "workspace": server.workspace, "readonly_tools": sorted(server.readonly_tools),
             "env_keys": sorted(server.env), "description": server.description}
            for server in servers.values()]}
    if op == "add":
        server = save_server(_require(name, "--mcp-name"), payload)
        return {"ok": True, "added": server.name,
                "note": "The tools are reviewed by the person the first time a task attaches the server."}
    if op == "remove":
        return {"ok": True, "removed": remove_server(_require(name, "--mcp-name"))}
    raise McpError(f"unknown mcp op {op!r}")


def run_connection_op(op: str, *, name: str | None, payload: dict[str, Any]) -> dict[str, Any]:
    from kernel.plugins.connections import load_connections, remove_connection, save_connection

    if op == "list":
        return {"ok": True, "connections": [item.public() for item in load_connections().values()]}
    if op == "add":
        return {"ok": True, "added": save_connection(_require(name, "--connection-name"), payload).public()}
    if op == "remove":
        return {"ok": True, "removed": remove_connection(_require(name, "--connection-name"))}
    raise OpError(f"unknown connection op {op!r}")


def main_connection_op(args) -> None:
    from kernel.plugins.connections import ConnectionError_

    try:
        raw = args.connection_payload if args.connection_payload is not None else (
            sys.stdin.read() if args.connection_op == "add" else "{}")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise OpError("the payload must be a JSON object")
        result = run_connection_op(args.connection_op, name=args.connection_name, payload=payload)
    except (OpError, ConnectionError_, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from error
    print(json.dumps(result, ensure_ascii=False))


def main_mcp_op(args) -> None:
    from kernel.plugins.mcp import McpError

    try:
        raw = args.mcp_payload if args.mcp_payload is not None else (sys.stdin.read() if args.mcp_op == "add" else "{}")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise OpError("the payload must be a JSON object")
        result = run_mcp_op(args.mcp_op, name=args.mcp_name, payload=payload)
    except (OpError, McpError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from error
    print(json.dumps(result, ensure_ascii=False))


def _require(value: str | None, flag: str) -> str:
    if not value:
        raise OpError(f"{flag} is required for this operation")
    return value


def main_skill_op(args) -> None:
    for_person = sys.stdout.isatty() and not getattr(args, "json", False)
    # JSON stays "en" unless --language says otherwise: programs must not depend on the person's locale.
    language = args.language or ("ru" if for_person and os.environ.get("LANG", "").startswith("ru") else "en")
    try:
        payload = run_skill_op(args.skill_op, workspace=Path(args.workspace or ".").resolve(), skill=args.skill,
                               path=args.skill_path, version=args.skill_version, assume_yes=args.yes,
                               language=language)
    except (OpError, RegistryError, ManifestError, OSError, RuntimeError, ValueError) as error:
        failure = {"ok": False, "error": str(error)}
        _print_result(args.skill_op, failure, for_person, language)
        raise SystemExit(1) from error
    _print_result(args.skill_op, payload, for_person, language)


def _print_result(op: str, payload: dict[str, Any], for_person: bool, language: str) -> None:
    if not for_person:
        print(json.dumps(payload, ensure_ascii=False))
        return
    from rich.console import Console

    from kernel.skill_ops_view import render

    render(op, payload, Console(), language)


def main_memory_op(args) -> None:
    from kernel.memory import MemoryStoreError

    try:
        raw = args.memory_payload if args.memory_payload is not None else (
            sys.stdin.read() if args.memory_op not in _MEMORY_OPS_WITHOUT_PAYLOAD else "{}")
        payload = json.loads(raw or "{}")
        if not isinstance(payload, dict):
            raise OpError("the payload must be a JSON object")
        result = run_memory_op(args.memory_op, workspace=Path(args.workspace or ".").resolve(), payload=payload)
    except MemoryStoreError as error:
        print(json.dumps({"ok": False, "error": str(error), "code": error.code}, ensure_ascii=False))
        raise SystemExit(1) from error
    except (OpError, json.JSONDecodeError, OSError) as error:
        print(json.dumps({"ok": False, "error": str(error)}, ensure_ascii=False))
        raise SystemExit(1) from error
    print(json.dumps(result, ensure_ascii=False))
