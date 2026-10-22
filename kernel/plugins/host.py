# -*- coding: utf-8 -*-
"""The core side of one skill runner: start, verify, serve calls, pause, stop.

A runner is started for one task when the task first calls the skill, and
lives as long as the task's resources do. Starting it copies the approved
package into a private snapshot and checks the snapshot's digest against the
grant, so content swapped after approval is caught before any of it runs.
``describe()`` is then compared with the manifest: it may narrow the tool list,
never widen it or change a tool's parameters.

A call is served on the caller's thread: the host answers the runner's context
requests through ``handler`` (the core services, which check every request)
and watches the task's control. A pause gives the skill a short grace to
return at its next request; a skill that does not is frozen in place (cgroup
freezer), so its code cannot act while the interface says "paused". A cancel or
timeout that the skill does not honour ends the runner.
"""
from __future__ import annotations

import json
import os
import socket
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from kernel.plugins.grants import Grant
from kernel.plugins.manifest import Manifest, ToolDef, package_digest, package_files, read_package_file, MAX_PACKAGE_BYTES
from kernel.plugins.net_broker import NetBroker, NetPolicy, TaskServices
from kernel.plugins.registry import CatalogEntry, SkillRegistry
from kernel.plugins.runner.kvist_wire import STATUSES, Channel, ProtocolError, error_payload
from kernel.plugins.sandbox import RunnerSpec, build_runner_argv, runner_unavailable_reason
from kernel.plugins.schema import validate
from kernel.plugins.screen import ScreenViewer
from kernel.plugins.resources import ManagedProcess, Owner, Resource, ResourceManager, StopOutcome
from kernel.agent.task_control import TaskControl

START_TIMEOUT = 45.0
PAUSE_GRACE = 3.0
CANCEL_GRACE = 5.0
_POLL = 0.1
PROXY_PORT = 8899
_DESCRIBE_KEYS = frozenset({"name", "summary", "parameters", "result", "readonly"})


class RunnerStartError(RuntimeError):
    """The runner could not be started or failed verification; nothing of the skill ran past it."""


class ServiceError(Exception):
    """A context request the core refused or could not serve; code is sent to the skill."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(slots=True)
class CallOutcome:
    status: str
    value: Any = None
    error_code: str = ""
    error_message: str = ""
    duration: float = 0.0
    requests: int = 0
    # The skill did not reach a safe point in time and was frozen mid-call.
    suspended: bool = False
    events: list[dict[str, Any]] = field(default_factory=list)


Handler = Callable[[str, dict[str, Any], str], Any]


class RunnerHost:
    def __init__(
        self,
        entry: CatalogEntry,
        grant: Grant,
        *,
        resources: ResourceManager,
        owner: Owner,
        registry: SkillRegistry,
        services: TaskServices,
        screen_size: str = "1280x800",
        on_screen: Callable[[ScreenViewer], None] | None = None,
    ) -> None:
        assert entry.manifest is not None and entry.version_dir is not None
        self.entry = entry
        self.manifest: Manifest = entry.manifest
        self.grant = grant
        self._resources = resources
        self.owner = owner
        self._registry = registry
        self._services = services
        self._screen_size = screen_size
        self._on_screen = on_screen
        self.process: ManagedProcess | None = None
        self.process_resource: Resource | None = None
        self.channel: Channel | None = None
        self.broker: NetBroker | None = None
        self.screen: ScreenViewer | None = None
        self.exchange_dir: Path | None = None
        self.described: dict[str, dict[str, Any]] = {}
        self._dirs: list[Resource] = []
        self._suspended_call: str = ""

    @property
    def alive(self) -> bool:
        return self.process is not None and self.process.alive()

    # --- start -----------------------------------------------------------

    def start(self) -> None:
        reason = runner_unavailable_reason()
        if reason:
            raise RunnerStartError(reason)
        if self.grant.digest != self.entry.digest:
            raise RunnerStartError("the approval was given for different content of this skill")
        base = self._resources.temp_dir(self.owner, f"skill-{self.manifest.id}")
        self._dirs.append(base)
        root = base.path
        assert root is not None
        snapshot = root / "skill"
        _snapshot(self.entry.version_dir, snapshot)
        if package_digest(snapshot) != self.grant.digest:
            raise RunnerStartError("the skill's files changed after approval; it was not started")
        self.exchange_dir = root / "exchange"
        self.exchange_dir.mkdir(mode=0o700)
        passwd, group = root / "passwd", root / "group"
        passwd.write_text(f"kvist:x:{os.getuid()}:{os.getgid()}:Kvist skill:/tmp/home:/usr/bin/false\n")
        group.write_text(f"kvist:x:{os.getgid()}:\n")
        net_dir, screen_dir = self._prepare_network(root), self._prepare_screen(root)
        env_dir = self._registry.env_dir(self.manifest) if self.manifest.python_deps else None
        parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        spec = RunnerSpec(
            skill_dir=snapshot, entrypoint=self.manifest.entrypoint, exchange_dir=self.exchange_dir,
            rpc_fd=child_sock.fileno(), passwd_file=passwd, group_file=group, env_dir=env_dir,
            net_dir=net_dir, proxy_port=PROXY_PORT if net_dir else 0, screen_dir=screen_dir,
            screen_size=self._screen_size,
        )
        self.process = ManagedProcess(
            build_runner_argv(spec),
            label=f"skill {self.manifest.id}",
            env={"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"},
            pass_fds=(child_sock.fileno(),),
            memory_mb=int(self.manifest.limits["memory_mb"]),
            pids_max=1024,
        )
        try:
            self.process_resource = self._resources.spawn(
                self.process, self.owner, details={"skill": self.manifest.id, "version": self.manifest.version})
        finally:
            child_sock.close()
        self.channel = Channel(parent_sock)
        self._handshake()
        if self.screen is not None:
            self.screen.set_state("running")
            if self._on_screen is not None:
                self._on_screen(self.screen)

    def _prepare_network(self, root: Path) -> Path | None:
        services = self.grant.allows("network.task_services")
        internet = self.grant.allows("network.internet")
        if not (services or internet):
            return None
        net_dir = root / "net"
        net_dir.mkdir(mode=0o700)
        domains = list(self.grant.scope("network.internet").get("domains", [])) if internet else []
        self.broker = NetBroker(net_dir / "proxy.sock", NetPolicy(allow_services=services, domains=domains),
                                self._services)
        self.broker.start()
        self._resources.register("endpoint", f"network proxy for {self.manifest.id}", self.owner,
                                 closer=lambda: self.broker.close() if self.broker else None)
        return net_dir

    def _prepare_screen(self, root: Path) -> Path | None:
        if not self.grant.allows("display"):
            return None
        screen_dir = root / "screen"
        screen_dir.mkdir(mode=0o700)
        self.screen = ScreenViewer(screen_dir, title=f"Kvist · {self.manifest.label('en')}")
        self.screen.start()
        self._resources.register("endpoint", "screen viewer", self.owner, closer=self.screen.close,
                                 details={"url": self.screen.base})
        return screen_dir

    def _handshake(self) -> None:
        assert self.channel is not None
        hello = self._receive_until({"hello", "fatal"}, START_TIMEOUT)
        if hello.get("kind") == "fatal":
            raise RunnerStartError(f"the skill failed to load: {_error_text(hello.get('error'))}")
        if hello.get("api") != 1:
            raise RunnerStartError(f"the runner speaks plugin API {hello.get('api')!r}, expected 1")
        self.channel.send({"kind": "describe"})
        reply = self._receive_until({"describe_result", "fatal"}, START_TIMEOUT)
        if reply.get("kind") == "fatal":
            raise RunnerStartError(f"describe() failed: {_error_text(reply.get('error'))}")
        self.described = check_description(self.manifest, reply.get("value"))

    def _receive_until(self, kinds: set[str], timeout: float) -> dict[str, Any]:
        assert self.channel is not None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive:
                raise RunnerStartError(f"the runner exited during start: {self._tail()}")
            try:
                message = self.channel.receive(timeout=_POLL)
            except (EOFError, ProtocolError) as error:
                raise RunnerStartError(f"the runner closed its channel: {error}; {self._tail()}") from None
            if message is not None and message.get("kind") in kinds:
                return message
        raise RunnerStartError(f"the runner did not answer within {timeout:.0f} s; {self._tail()}")

    # --- calls -----------------------------------------------------------

    def call(
        self,
        tool: ToolDef,
        arguments: dict[str, Any],
        handler: Handler,
        control: TaskControl,
        *,
        call_id: str | None = None,
        on_event: Callable[[dict[str, Any]], None] | None = None,
    ) -> CallOutcome:
        """Run one tool call to its end, a pause, a cancel or its timeout."""
        assert self.channel is not None
        call_id = call_id or f"skc_{uuid.uuid4().hex[:12]}"
        if tool.name not in self.described:
            return CallOutcome("error", error_code="not_described",
                               error_message=f"{tool.name} is declared but the skill's describe() did not offer it")
        self.settle_suspended()
        started = time.monotonic()
        deadline = started + min(tool.timeout_seconds, self.manifest.limits["call_timeout_seconds"])
        outcome = CallOutcome("error")
        self.channel.send({"kind": "execute", "call_id": call_id, "tool": tool.name, "arguments": arguments})
        stop_sent_at: float | None = None
        stop_status = ""
        while True:
            now = time.monotonic()
            if control.interrupted() and stop_sent_at is None:
                stop_status = "cancelled" if control.cancel_requested() else "paused"
                self.channel.send({"kind": "control", "action": "cancel" if stop_status == "cancelled" else "pause",
                                   "call_id": call_id})
                stop_sent_at = now
            if now > deadline and stop_sent_at is None:
                stop_status = "timeout"
                self.channel.send({"kind": "control", "action": "cancel", "call_id": call_id})
                stop_sent_at = now
            if stop_sent_at is not None and now - stop_sent_at > (PAUSE_GRACE if stop_status == "paused" else CANCEL_GRACE):
                return self._force_stop(call_id, stop_status, started, outcome)
            if not self.alive:
                return CallOutcome("error", error_code="runner_crashed",
                                   error_message=f"the skill runner exited: {self._tail()}",
                                   duration=time.monotonic() - started, requests=outcome.requests)
            try:
                message = self.channel.receive(timeout=_POLL)
            except (EOFError, ProtocolError) as error:
                if self.process_resource is not None:
                    self._resources.stop(self.process_resource.id)
                return CallOutcome("error", error_code="protocol_error", error_message=str(error),
                                   duration=time.monotonic() - started, requests=outcome.requests)
            if message is None:
                continue
            kind = message.get("kind")
            if kind == "request":
                outcome.requests += 1
                self._serve_request(message, call_id, handler, control, stop_status)
            elif kind == "event" and message.get("call_id") == call_id:
                outcome.events.append({"name": message.get("name"), "data": message.get("data")})
                if on_event is not None:
                    on_event(message)
            elif kind == "result" and message.get("call_id") == call_id:
                result = self._finish_result(tool, message, started, outcome.requests)
                result.events = outcome.events
                if stop_status and result.status == "success":
                    result.status = "partial" if stop_status == "paused" else result.status
                return result
            elif kind == "fatal":
                return CallOutcome("error", error_code="runner_failed", error_message=_error_text(message.get("error")),
                                   duration=time.monotonic() - started, requests=outcome.requests)

    def _serve_request(self, message: dict[str, Any], call_id: str, handler: Handler, control: TaskControl,
                       stop_status: str) -> None:
        assert self.channel is not None
        request_id = message.get("id")
        if message.get("call_id") != call_id:
            self.channel.send({"kind": "response", "id": request_id, "ok": False,
                               "error": error_payload("cancelled", "this call is no longer running")})
            return
        if stop_status or control.interrupted():
            code = "paused" if (stop_status == "paused" or control.pause_requested()) else "cancelled"
            self.channel.send({"kind": "response", "id": request_id, "ok": False,
                               "error": error_payload(code, f"the task was {code} by the person")})
            return
        method, params = message.get("method"), message.get("params")
        if not isinstance(method, str) or not isinstance(params, dict):
            self.channel.send({"kind": "response", "id": request_id, "ok": False,
                               "error": error_payload("bad_request", "method and params are required")})
            return
        try:
            value = handler(method, params, call_id)
            reply: dict[str, Any] = {"kind": "response", "id": request_id, "ok": True, "value": value}
        except ServiceError as error:
            reply = {"kind": "response", "id": request_id, "ok": False, "error": error_payload(error.code, error.message)}
        try:
            self.channel.send(reply)
        except ProtocolError as error:
            self.channel.send({"kind": "response", "id": request_id, "ok": False,
                               "error": error_payload("too_large", str(error))})

    def _finish_result(self, tool: ToolDef, message: dict[str, Any], started: float, requests: int) -> CallOutcome:
        duration = time.monotonic() - started
        status = message.get("status")
        if status not in STATUSES:
            return CallOutcome("error", error_code="bad_result", error_message=f"unknown status {status!r}",
                               duration=duration, requests=requests)
        value = message.get("value")
        size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
        limit = min(tool.max_result_bytes, int(self.manifest.limits["max_result_bytes"]))
        if size > limit:
            return CallOutcome("error", error_code="result_too_large",
                               error_message=f"the result is {size} bytes; the limit is {limit}",
                               duration=duration, requests=requests)
        if status == "success" and tool.result is not None:
            errors = validate(value, tool.result)
            if errors:
                return CallOutcome("error", error_code="bad_result",
                                   error_message="the result does not match the tool's declared schema: " + "; ".join(errors[:3]),
                                   duration=duration, requests=requests)
        error = message.get("error") if isinstance(message.get("error"), dict) else {}
        return CallOutcome(status, value=value, error_code=str(error.get("code") or ""),
                           error_message=str(error.get("message") or ""), duration=duration, requests=requests)

    def _force_stop(self, call_id: str, status: str, started: float, outcome: CallOutcome) -> CallOutcome:
        duration = time.monotonic() - started
        if status == "paused" and self.process is not None and self.process.freeze():
            self._suspended_call = call_id
            return CallOutcome("paused", error_code="suspended",
                               error_message="the skill did not stop at a safe point and was frozen in place",
                               duration=duration, requests=outcome.requests, suspended=True)
        if self.process_resource is not None:
            self._resources.stop(self.process_resource.id, grace=1.0)
        message = ("the skill did not stop in time; its runner was ended"
                   if status != "timeout" else "the call ran past its time limit; the runner was ended")
        return CallOutcome("timeout" if status == "timeout" else status, error_code="runner_stopped",
                           error_message=message, duration=duration, requests=outcome.requests)

    def settle_suspended(self) -> None:
        """Thaw a runner frozen mid-call and cancel that call before anything else runs."""
        if not self._suspended_call or self.process is None or self.channel is None:
            return
        call_id, self._suspended_call = self._suspended_call, ""
        self.process.thaw()
        self.channel.send({"kind": "control", "action": "cancel", "call_id": call_id})
        deadline = time.monotonic() + CANCEL_GRACE
        while time.monotonic() < deadline and self.alive:
            try:
                message = self.channel.receive(timeout=_POLL)
            except (EOFError, ProtocolError):
                break
            if message is None:
                continue
            if message.get("kind") == "request":
                self.channel.send({"kind": "response", "id": message.get("id"), "ok": False,
                                   "error": error_payload("cancelled", "the paused call was cancelled")})
            elif message.get("kind") == "result" and message.get("call_id") == call_id:
                return
        if self.process_resource is not None:
            self._resources.stop(self.process_resource.id, grace=1.0)

    # --- stop ------------------------------------------------------------

    def stop(self) -> list[StopOutcome]:
        """End the runner, its screen and proxy; return what happened to each."""
        outcomes: list[StopOutcome] = []
        if self.channel is not None and self.alive:
            try:
                self.channel.send({"kind": "shutdown"})
            except OSError:
                # The channel is already gone; the process stop below still runs.
                self.channel = None
        outcomes.extend(self._resources.cleanup(self.owner, "skill_stopped").outcomes)
        if self.channel is not None:
            self.channel.close()
            self.channel = None
        return outcomes

    def _tail(self) -> str:
        if self.process is None:
            return ""
        tail = self.process.output_tail(1500).strip()
        return f"runner output: {tail}" if tail else "the runner printed nothing"


def check_description(manifest: Manifest, value: Any) -> dict[str, dict[str, Any]]:
    """The tools ``describe()`` offers, checked against the manifest; RunnerStartError on any widening."""
    if not isinstance(value, dict) or not isinstance(value.get("tools"), list):
        raise RunnerStartError("describe() must return {\"tools\": [...]}")
    extra_keys = set(value) - {"tools", "version"}
    if extra_keys:
        raise RunnerStartError(f"describe() returned fields the core does not accept: {sorted(extra_keys)}")
    if "version" in value and value["version"] != manifest.version:
        raise RunnerStartError(f"describe() reports version {value['version']!r}, the manifest {manifest.version!r}")
    described: dict[str, dict[str, Any]] = {}
    for item in value["tools"]:
        item = {"name": item} if isinstance(item, str) else item
        if not isinstance(item, dict) or not isinstance(item.get("name"), str):
            raise RunnerStartError("each described tool is a name or an object with a name")
        if set(item) - _DESCRIBE_KEYS:
            raise RunnerStartError(f"describe() tool {item['name']} has fields the core does not accept: "
                                   f"{sorted(set(item) - _DESCRIBE_KEYS)}")
        declared = manifest.tool(item["name"])
        if declared is None:
            raise RunnerStartError(f"describe() offers {item['name']!r}, which the approved manifest does not declare")
        if "parameters" in item and item["parameters"] != declared.parameters:
            raise RunnerStartError(f"describe() changes the parameters of {item['name']}")
        if "result" in item and item["result"] != declared.result:
            raise RunnerStartError(f"describe() changes the result schema of {item['name']}")
        if "readonly" in item and item["readonly"] != declared.readonly:
            raise RunnerStartError(f"describe() changes whether {item['name']} is read-only")
        described[item["name"]] = item
    return described


def _snapshot(source: Path, target: Path) -> None:
    target.mkdir(mode=0o700)
    for relative, _path in package_files(source):
        destination = target / relative
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        destination.write_bytes(read_package_file(source, relative, MAX_PACKAGE_BYTES))
        os.chmod(destination, 0o400)


def _error_text(error: Any) -> str:
    if isinstance(error, dict):
        return f"{error.get('code', 'error')}: {error.get('message', '')}"
    return str(error)
