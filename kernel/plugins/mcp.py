# -*- coding: utf-8 -*-
"""Model Context Protocol servers behind the same tool registry as skills.

Only local stdio servers the person configured themselves are supported, in
``<config_dir>/plugins/mcp.json`` (managed with ``kvist --mcp-op``)::

    {"servers": {"github": {"command": ["github-mcp", "stdio"], "env": {"TOKEN": "…"},
                            "sandbox": "none", "network": true, "workspace": "none",
                            "readonly_tools": ["search_issues"], "description": "…"}}}

The rules the registry applies to skills apply here:

* tools appear as ``mcp__<server>__<tool>`` only after the server is attached
  to the task; their list (names and schemas) is pinned by the person's
  approval and a changed list needs a new approval;
* a tool counts as read-only only if the person listed it in
  ``readonly_tools`` — what the server says about itself is not trusted;
* results are untrusted data; secrets in ``env`` stay in the core's process
  table and are never shown to the model or to skills;
* the server process belongs to the task and stops with it;
* cancelling a call sends ``notifications/cancelled``, but what a server
  already did cannot be undone and is reported as unknown.

Remote (HTTP) servers are not supported in this version.
"""
from __future__ import annotations

import hashlib
import json
import queue
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kernel.session.durable_json import JsonStorageError, read_json_object, update_json_object
from kernel.plugins.paths import plugins_root
from kernel.sandbox.redaction import redact_text
from kernel.plugins.resources import ManagedProcess, Owner, Resource, ResourceManager
from kernel.sandbox.sandbox import RUNTIME_MOUNT_ROOTS, resolve_bubblewrap_path
from kernel.agent.task_control import TaskControl

PROTOCOL_VERSION = "2025-06-18"
SERVER_NAME_RE = re.compile(r"^[a-z][a-z0-9_]{0,30}[a-z0-9]$")
CALL_TIMEOUT = 60.0
START_TIMEOUT = 30.0
MAX_RESULT_CHARS = 200_000
MAX_TOOLS = 100
_WIRE_RE = re.compile(r"[^a-z0-9_]")


class McpError(Exception):
    """An MCP operation the core refuses or that failed; the message goes to the model."""


@dataclass(frozen=True, slots=True)
class ServerConfig:
    name: str
    command: list[str]
    env: dict[str, str]
    sandbox: str  # "bwrap" | "none"
    network: bool
    workspace: str  # "none" | "read" | "write"
    readonly_tools: frozenset[str]
    description: str


def config_path() -> Path:
    return plugins_root() / "mcp.json"


def load_servers(path: Path | None = None) -> dict[str, ServerConfig]:
    target = path or config_path()
    if not target.is_file():
        return {}
    try:
        document = read_json_object(target, boundary=target.parent, empty_as_missing=True)
    except JsonStorageError as error:
        raise McpError(f"mcp.json is damaged: {error}") from None
    servers = {}
    for name, raw in (document.get("servers") or {}).items():
        servers[name] = parse_server(name, raw)
    return servers


def parse_server(name: str, raw: Any) -> ServerConfig:
    if not SERVER_NAME_RE.match(name or "") or "__" in name:
        raise McpError(f"server name {name!r} must be lowercase letters, digits and single underscores")
    if not isinstance(raw, dict):
        raise McpError(f"server {name} must be an object")
    command = raw.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(part, str) and part for part in command):
        raise McpError(f"server {name}: command must be a non-empty list of strings")
    env = raw.get("env", {})
    if not isinstance(env, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
        raise McpError(f"server {name}: env must be an object of strings")
    sandbox = raw.get("sandbox", "bwrap")
    workspace = raw.get("workspace", "none")
    if sandbox not in ("bwrap", "none") or workspace not in ("none", "read", "write"):
        raise McpError(f"server {name}: sandbox is bwrap|none and workspace is none|read|write")
    readonly = raw.get("readonly_tools", [])
    if not isinstance(readonly, list) or not all(isinstance(item, str) for item in readonly):
        raise McpError(f"server {name}: readonly_tools must be a list of tool names")
    return ServerConfig(name, list(command), dict(env), sandbox, bool(raw.get("network", False)), workspace,
                        frozenset(readonly), str(raw.get("description", ""))[:300])


def save_server(name: str, raw: dict[str, Any], path: Path | None = None) -> ServerConfig:
    server = parse_server(name, raw)
    target = path or config_path()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)

    def update(document: dict[str, Any]) -> dict[str, Any]:
        document.setdefault("servers", {})[name] = raw
        return document

    update_json_object(target, update, boundary=target.parent, empty_as_missing=True)
    return server


def remove_server(name: str, path: Path | None = None) -> bool:
    target = path or config_path()
    removed = {"value": False}

    def update(document: dict[str, Any]) -> dict[str, Any]:
        removed["value"] = document.get("servers", {}).pop(name, None) is not None
        return document

    if target.is_file():
        update_json_object(target, update, boundary=target.parent, empty_as_missing=True)
    return removed["value"]


def wire_tool_name(server: str, tool: str) -> str:
    """``mcp__<server>__<tool>`` with the tool name folded to provider-safe characters."""
    folded = _WIRE_RE.sub("_", tool.lower()).strip("_")[:40] or "tool"
    return f"mcp__{server}__{folded}"


def tools_digest(tools: list[dict[str, Any]]) -> str:
    material = json.dumps([{"name": tool.get("name"), "inputSchema": tool.get("inputSchema")} for tool in tools],
                          sort_keys=True)
    return "sha256:" + hashlib.sha256(material.encode("utf-8")).hexdigest()


class McpClient:
    """One running server: newline-delimited JSON-RPC 2.0 over its stdin and stdout."""

    def __init__(self, server: ServerConfig, *, workspace: Path, resources: ResourceManager, owner: Owner) -> None:
        self.server = server
        self._workspace = workspace
        self._resources = resources
        self._owner = owner
        self.process: ManagedProcess | None = None
        self.resource: Resource | None = None
        self._next_id = 0
        self._pending: dict[int, queue.Queue] = {}
        self._lock = threading.Lock()
        self.tools_changed = False
        self.server_info: dict[str, Any] = {}

    def start(self) -> None:
        argv = self._argv()
        env = {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8", **self.server.env}
        self.process = ManagedProcess(argv, label=f"MCP server {self.server.name}", env=env,
                                      cwd=str(self._workspace) if self.server.workspace != "none" else None,
                                      memory_mb=2048, pids_max=512, protocol=True)
        self.resource = self._resources.spawn(self.process, self._owner, details={"mcp": self.server.name})
        threading.Thread(target=self._read_loop, name=f"kvist-mcp-{self.server.name}", daemon=True).start()
        reply = self.request("initialize", {
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {},
            "clientInfo": {"name": "kvist", "version": "1"},
        }, timeout=START_TIMEOUT)
        self.server_info = reply.get("serverInfo") or {}
        self.notify("notifications/initialized", {})

    def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor = None
        while True:
            reply = self.request("tools/list", {"cursor": cursor} if cursor else {}, timeout=CALL_TIMEOUT)
            batch = reply.get("tools") or []
            if not isinstance(batch, list):
                raise McpError("tools/list returned no list")
            tools.extend(tool for tool in batch if isinstance(tool, dict) and isinstance(tool.get("name"), str))
            cursor = reply.get("nextCursor")
            if not cursor or len(tools) >= MAX_TOOLS:
                break
        self.tools_changed = False
        return tools[:MAX_TOOLS]

    def call_tool(self, name: str, arguments: dict[str, Any], control: TaskControl, timeout: float = CALL_TIMEOUT) -> dict[str, Any]:
        request_id, answer = self._send("tools/call", {"name": name, "arguments": arguments})
        deadline = time.monotonic() + timeout
        while True:
            try:
                return self._result(answer.get(timeout=0.1))
            except queue.Empty:
                pass
            if control.interrupted() or time.monotonic() > deadline:
                self.notify("notifications/cancelled", {"requestId": request_id,
                                                        "reason": "stopped by the person" if control.interrupted() else "timeout"})
                with self._lock:
                    self._pending.pop(request_id, None)
                raise McpError("the call was cancelled; whether the server already acted on it is unknown"
                               if control.interrupted() else f"the call took longer than {timeout:.0f} s; "
                               "whether the server acted on it is unknown")
            if self.process is not None and not self.process.alive():
                raise McpError(f"the MCP server exited: {self.process.output_tail(800)}")

    def request(self, method: str, params: dict[str, Any], timeout: float) -> dict[str, Any]:
        _request_id, answer = self._send(method, params)
        try:
            return self._result(answer.get(timeout=timeout))
        except queue.Empty:
            tail = self.process.output_tail(800) if self.process else ""
            raise McpError(f"{method}: no answer within {timeout:.0f} s {tail}".strip()) from None

    def notify(self, method: str, params: dict[str, Any]) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def stop(self) -> None:
        if self.resource is not None:
            self._resources.stop(self.resource.id, grace=2.0)
            self.resource = None

    def _send(self, method: str, params: dict[str, Any]) -> tuple[int, queue.Queue]:
        with self._lock:
            self._next_id += 1
            request_id = self._next_id
            answer: queue.Queue = queue.Queue(maxsize=1)
            self._pending[request_id] = answer
        self._write({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        return request_id, answer

    def _write(self, message: dict[str, Any]) -> None:
        if self.process is None or self.process.proc is None or self.process.proc.stdin is None:
            raise McpError("the MCP server is not running")
        try:
            self.process.proc.stdin.write((json.dumps(message) + "\n").encode("utf-8"))
            self.process.proc.stdin.flush()
        except (BrokenPipeError, OSError) as error:
            raise McpError(f"the MCP server is not accepting input: {error}") from None

    def _read_loop(self) -> None:
        assert self.process is not None and self.process.proc is not None
        stream = self.process.proc.stdout
        for line in iter(stream.readline, b""):
            if len(line) > 4 * 1024 * 1024:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(message, dict):
                continue
            if message.get("method") == "notifications/tools/list_changed":
                self.tools_changed = True
                continue
            request_id = message.get("id")
            if "method" in message and request_id is not None:
                # Requests from the server (sampling, roots) are not offered: refuse them.
                self._write({"jsonrpc": "2.0", "id": request_id,
                             "error": {"code": -32601, "message": "not supported by Kvist"}})
                continue
            with self._lock:
                answer = self._pending.pop(request_id, None)
            if answer is not None:
                answer.put(message)

    @staticmethod
    def _result(message: dict[str, Any]) -> dict[str, Any]:
        if "error" in message:
            error = message["error"] if isinstance(message["error"], dict) else {}
            raise McpError(f"server error {error.get('code')}: {redact_text(str(error.get('message', '')))[:500]}")
        result = message.get("result")
        return result if isinstance(result, dict) else {}

    def _argv(self) -> list[str]:
        if self.server.sandbox == "none":
            return list(self.server.command)
        bwrap = resolve_bubblewrap_path()
        if bwrap is None:
            raise McpError(f"server {self.server.name} asks for a sandbox, and Bubblewrap is unavailable")
        argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--cap-drop", "ALL",
                "--setenv", "HOME", "/tmp/home"]
        if self.server.network:
            argv.append("--share-net")
            for path in ("/etc/resolv.conf", "/etc/hosts", "/etc/ssl", "/etc/ca-certificates", "/etc/nsswitch.conf"):
                if Path(path).exists():
                    argv += ["--ro-bind", path, path]
        for root in RUNTIME_MOUNT_ROOTS:
            if Path(root).exists():
                argv += ["--ro-bind", root, root]
        if Path("/etc/ld.so.cache").exists():
            argv += ["--ro-bind", "/etc/ld.so.cache", "/etc/ld.so.cache"]
        argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/tmp/home"]
        if self.server.workspace != "none":
            mode = "--bind" if self.server.workspace == "write" else "--ro-bind"
            argv += [mode, str(self._workspace), str(self._workspace),
                     "--tmpfs", str(self._workspace / ".kvist"), "--chdir", str(self._workspace)]
        return [*argv, "--", *self.server.command]


@dataclass(slots=True)
class AttachedServer:
    config: ServerConfig
    client: McpClient
    tools: dict[str, dict[str, Any]] = field(default_factory=dict)  # wire name -> tool
    digest: str = ""


def render_result(result: dict[str, Any]) -> dict[str, Any]:
    """The text a model may read from a tools/call result; images and resources are named, not inlined."""
    parts: list[str] = []
    for item in result.get("content") or []:
        if not isinstance(item, dict):
            continue
        if item.get("type") == "text":
            parts.append(str(item.get("text", "")))
        elif item.get("type") in ("image", "audio"):
            parts.append(f"[{item.get('type')} {item.get('mimeType', '')} not shown]")
        elif item.get("type") in ("resource", "resource_link"):
            resource = item.get("resource") or item
            parts.append(f"[resource {resource.get('uri', '')}]")
    text = "\n".join(parts)
    truncated = len(text) > MAX_RESULT_CHARS
    rendered: dict[str, Any] = {"text": text[:MAX_RESULT_CHARS], "truncated": truncated,
                                "is_error": bool(result.get("isError")), "untrusted": True}
    if isinstance(result.get("structuredContent"), dict):
        rendered["structured"] = result["structuredContent"]
    return rendered
