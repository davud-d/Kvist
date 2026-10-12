# -*- coding: utf-8 -*-
"""The Python side of plugin API v1: the ``context`` object a skill's ``execute`` receives.

Every method is one synchronous request to the Kvist core over the runner's
protocol channel; the call blocks until the core answers. Arguments and results
are JSON values only — no Python objects or callbacks cross the boundary.
Files and images move through the exchange directory (``context.exchange``)
and are passed by path or attachment id.

The core checks each request against the skill's granted capabilities, the
task's role and phase, and the call that is running. A refusal raises
``Denied``; a stop or pause of the task raises ``Cancelled`` or ``Paused`` at
the next request, which is the skill's cue to return. Nothing here holds a
secret or a reference to the agent: this object is only a client.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

EXCHANGE_DIR = Path("/kvist/exchange")


class SkillError(Exception):
    """A request the core answered with an error; ``code`` is machine-readable."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


class Denied(SkillError):
    """The capability, role, phase or the person refused this request."""


class Cancelled(SkillError):
    """The task or this call was stopped; return promptly."""


class Paused(SkillError):
    """The person paused the task; return what you have, the agent decides how to go on."""


class Unavailable(SkillError):
    """The service does not exist on this machine (no Vision model, no frontend, unsupported platform)."""


_ERRORS: dict[str, type[SkillError]] = {
    "denied": Denied,
    "cancelled": Cancelled,
    "paused": Paused,
    "unavailable": Unavailable,
}

Transport = Callable[[str, dict[str, Any]], Any]


class _Group:
    def __init__(self, transport: Transport, prefix: str) -> None:
        self._transport = transport
        self._prefix = prefix

    def _call(self, method: str, **params: Any) -> Any:
        clean = {key: value for key, value in params.items() if value is not None}
        return self._transport(f"{self._prefix}.{method}", clean)


class Input(_Group):
    def task(self) -> dict[str, Any]:
        """{text, updates, language, session_id, run_id, call_id, message_id}."""
        return self._call("task")

    def attachments(self) -> list[dict[str, Any]]:
        """Attachments this call may use: id, name, kind, origin, size, width, height."""
        return self._call("attachments")


class Images(_Group):
    def info(self, attachment_id: str) -> dict[str, Any]:
        return self._call("info", id=attachment_id)

    def analyze(self, attachment_ids: list[str], question: str) -> dict[str, Any]:
        """Ask the Vision model about one or more images; raises Unavailable without Vision."""
        return self._call("analyze", ids=attachment_ids, question=question)

    def crop(self, attachment_id: str, box: list[int]) -> dict[str, Any]:
        """A new derived attachment of the region [left, top, right, bottom]; the original stays."""
        return self._call("crop", id=attachment_id, box=box)

    def resize(self, attachment_id: str, max_width: int, max_height: int) -> dict[str, Any]:
        return self._call("resize", id=attachment_id, max_width=max_width, max_height=max_height)

    def convert(self, attachment_id: str, format: str) -> dict[str, Any]:  # noqa: A002 - mirrors the API name
        return self._call("convert", id=attachment_id, format=format)

    def original(self, attachment_id: str) -> str:
        """Path (inside the sandbox) of a copy of the original file."""
        return self._call("original", id=attachment_id)["path"]

    def model_copy(self, attachment_id: str) -> str:
        """Path of the JPEG copy prepared for models (≤2048 px, no transparency)."""
        return self._call("model_copy", id=attachment_id)["path"]

    def add(self, path: str, name: str = "") -> dict[str, Any]:
        """Register an image file this skill wrote in the exchange directory as an attachment."""
        return self._call("add", path=str(path), name=name or None)


class Models(_Group):
    def list(self) -> list[dict[str, Any]]:
        return self._call("list")

    def complete(self, prompt: str | None = None, *, messages: list[dict[str, Any]] | None = None,
                 system: str | None = None, tier: str = "fast", max_tokens: int = 2000) -> dict[str, Any]:
        """{text, model, usage}. The core picks the model of ``tier`` and counts the tokens."""
        return self._call("complete", prompt=prompt, messages=messages, system=system, tier=tier,
                          max_tokens=max_tokens)

    def stream(self, prompt: str | None = None, *, messages: list[dict[str, Any]] | None = None,
               system: str | None = None, tier: str = "fast", max_tokens: int = 2000):
        """Yield text pieces as the model writes them."""
        handle = self._call("stream_start", prompt=prompt, messages=messages, system=system, tier=tier,
                            max_tokens=max_tokens)
        while True:
            piece = self._call("stream_next", stream_id=handle["stream_id"])
            if piece.get("text"):
                yield piece["text"]
            if piece.get("done"):
                return

    def json(self, prompt: str, schema: dict[str, Any], *, tier: str = "fast", max_tokens: int = 2000) -> Any:
        """A JSON value checked against ``schema`` by the core (one repair attempt)."""
        return self._call("json", prompt=prompt, schema=schema, tier=tier, max_tokens=max_tokens)["value"]

    def count_tokens(self, text: str) -> int:
        return self._call("count_tokens", text=text)["tokens"]


class Files(_Group):
    def read(self, path: str, start_line: int | None = None, end_line: int | None = None) -> dict[str, Any]:
        return self._call("read", path=path, start_line=start_line, end_line=end_line)

    def read_many(self, paths: list[str]) -> dict[str, Any]:
        return self._call("read_many", paths=paths)

    def list(self, path: str = ".") -> dict[str, Any]:
        return self._call("list", path=path)

    def tree(self, path: str = ".", max_depth: int = 3) -> dict[str, Any]:
        return self._call("tree", path=path, max_depth=max_depth)

    def find(self, pattern: str, path: str = "") -> dict[str, Any]:
        return self._call("find", pattern=pattern, path=path)

    def search(self, query: str, path: str = "", max_results: int = 50) -> dict[str, Any]:
        return self._call("search", query=query, path=path, max_results=max_results)

    def write(self, path: str, content: str) -> dict[str, Any]:
        return self._call("write", path=path, content=content)

    def append(self, path: str, content: str) -> dict[str, Any]:
        return self._call("append", path=path, content=content)

    def replace(self, path: str, old_text: str, new_text: str, count: int = 1) -> dict[str, Any]:
        return self._call("replace", path=path, old_text=old_text, new_text=new_text, count=count)

    def read_bytes(self, path: str) -> str:
        """Copy a workspace file into the exchange directory; returns the sandbox path."""
        return self._call("read_bytes", path=path)["path"]

    def mkdir(self, path: str) -> dict[str, Any]:
        return self._call("mkdir", path=path)


class Changes(_Group):
    def list(self) -> dict[str, Any]:
        return self._call("list")

    def diff(self, path: str | None = None) -> dict[str, Any]:
        return self._call("diff", path=path)

    def preview(self, path: str, content: str) -> dict[str, Any]:
        return self._call("preview", path=path, content=content)


class Git(_Group):
    def status(self) -> dict[str, Any]:
        return self._call("status")

    def diff(self, path: str | None = None, ref: str | None = None, staged: bool = False) -> dict[str, Any]:
        return self._call("diff", path=path, ref=ref, staged=staged)


class Processes(_Group):
    def run(self, command: list[str], cwd: str | None = None, timeout: float | None = None) -> dict[str, Any]:
        """Run a command in the workspace sandbox and wait: {returncode, stdout, stderr, ...}."""
        return self._call("run", command=command, cwd=cwd, timeout=timeout)

    def start(self, command: list[str], cwd: str | None = None, expose_port: int | None = None,
              label: str | None = None) -> dict[str, Any]:
        """Start a background command owned by this task; ``expose_port`` makes it a task service."""
        return self._call("start", command=command, cwd=cwd, expose_port=expose_port, label=label)

    def output(self, job_id: str, wait: float = 0) -> dict[str, Any]:
        return self._call("output", job_id=job_id, wait=wait)

    def status(self, job_id: str) -> dict[str, Any]:
        return self._call("status", job_id=job_id)

    def stop(self, job_id: str) -> dict[str, Any]:
        return self._call("stop", job_id=job_id)

    def list(self) -> list[dict[str, Any]]:
        return self._call("list")


class Web(_Group):
    def search(self, query: str, max_results: int = 5, domains: list[str] | None = None,
               freshness: str | None = None) -> dict[str, Any]:
        return self._call("search", query=query, max_results=max_results, domains=domains, freshness=freshness)

    def fetch(self, url: str, max_chars: int = 12000, mode: str = "text") -> dict[str, Any]:
        return self._call("fetch", url=url, max_chars=max_chars, mode=mode)


class Http(_Group):
    def request(self, method: str, url: str, *, headers: dict[str, str] | None = None,
                body: str | None = None, json_body: Any = None, timeout: float = 20) -> dict[str, Any]:
        """{status, headers, text, truncated}; redirects are followed only to allowed destinations."""
        return self._call("request", method=method, url=url, headers=headers, body=body,
                          json=json_body, timeout=timeout)


class Connections(_Group):
    """Services the person configured; the core adds their credentials, the skill never sees them."""

    def list(self) -> list[dict[str, Any]]:
        return self._call("list")

    def request(self, connection: str, method: str, path: str, *, headers: dict[str, str] | None = None,
                body: str | None = None, json_body: Any = None, timeout: float = 20) -> dict[str, Any]:
        return self._call("request", connection=connection, method=method, path=path, headers=headers,
                          body=body, json=json_body, timeout=timeout)


class Ui(_Group):
    def notify(self, text: str) -> None:
        self._call("notify", text=text)

    def message(self, markdown: str) -> None:
        self._call("message", markdown=markdown)

    def error(self, text: str) -> None:
        self._call("error", text=text)

    def stream(self, pieces) -> None:
        handle = self._call("stream_start")
        for piece in pieces:
            self._call("stream_append", stream_id=handle["stream_id"], text=str(piece))
        self._call("stream_end", stream_id=handle["stream_id"])

    def choose(self, question: str, options: list[str], *, allow_skip: bool = False) -> dict[str, Any]:
        """{status: answered|skipped|cancelled|no_interactive_user, value}."""
        return self._call("choose", question=question, options=options, allow_skip=allow_skip)

    def ask(self, question: str, *, default: str = "", allow_skip: bool = False) -> dict[str, Any]:
        return self._call("ask", question=question, default=default, allow_skip=allow_skip)

    def confirm(self, question: str, details: str = "") -> dict[str, Any]:
        """{status, approved}; a missing frontend is never an approval."""
        return self._call("confirm", question=question, details=details)

    def present(self, *, attachment_id: str | None = None, artifact_id: str | None = None,
                caption: str = "") -> dict[str, Any]:
        return self._call("present", attachment_id=attachment_id, artifact_id=artifact_id, caption=caption)


class Sessions(_Group):
    def list(self) -> list[dict[str, Any]]:
        return self._call("list")

    def read(self, session: str, before: int | None = None, limit: int = 40) -> dict[str, Any]:
        return self._call("read", session=session, before=before, limit=limit)

    def search(self, query: str, limit: int = 20) -> dict[str, Any]:
        return self._call("search", query=query, limit=limit)


class Memory(_Group):
    def search(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        return self._call("search", query=query, limit=limit)

    def list(self, include_outdated: bool = False) -> list[dict[str, Any]]:
        return self._call("list", include_outdated=include_outdated)

    def add(self, text: str, *, kind: str = "observation", topic: str = "", files: list[str] | None = None) -> dict[str, Any]:
        return self._call("add", text=text, kind=kind, topic=topic, files=files)

    def update(self, note_id: str, *, text: str | None = None, status: str | None = None,
               topic: str | None = None) -> dict[str, Any]:
        return self._call("update", id=note_id, text=text, status=status, topic=topic)

    def delete(self, note_id: str) -> dict[str, Any]:
        return self._call("delete", id=note_id)


class Storage(_Group):
    """The skill's own data; scope is call, task, session or workspace."""

    def get(self, key: str, scope: str = "workspace") -> dict[str, Any]:
        """{value, version}; version 0 means the key does not exist."""
        return self._call("get", key=key, scope=scope)

    def set(self, key: str, value: Any, scope: str = "workspace", expected_version: int | None = None) -> dict[str, Any]:
        """Write ``value``; with ``expected_version`` the write fails if someone changed the key meanwhile."""
        return self._call("set", key=key, value=value, scope=scope, expected_version=expected_version)

    def delete(self, key: str, scope: str = "workspace") -> dict[str, Any]:
        return self._call("delete", key=key, scope=scope)

    def keys(self, scope: str = "workspace") -> list[str]:
        return self._call("keys", scope=scope)


class Runtime(_Group):
    def status(self) -> dict[str, Any]:
        """{state, paused, cancelled, deadline_seconds}."""
        return self._call("status")

    def check(self) -> None:
        """Raise Cancelled or Paused if the task is stopping; call it between long steps."""
        self._call("check")

    def sleep(self, seconds: float) -> None:
        """Wait, ending early (with Cancelled/Paused) if the task is stopped."""
        deadline = time.monotonic() + max(0.0, seconds)
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            self._call("wait", seconds=min(remaining, 5.0))

    def progress(self, text: str) -> None:
        self._call("progress", text=text)

    def register_resource(self, label: str, kind: str = "logical", details: dict[str, Any] | None = None) -> str:
        """Tell the core about something this skill keeps alive (a browser tab) so it is listed and reported."""
        return self._call("register_resource", label=label, kind=kind, details=details)["resource_id"]

    def release_resource(self, resource_id: str) -> None:
        self._call("release_resource", resource_id=resource_id)


class Events(_Group):
    def poll(self, after: int = 0, timeout: float = 0) -> dict[str, Any]:
        """{events: [{seq, type, data}], next}; acknowledging is passing ``next`` as ``after``."""
        return self._call("poll", after=after, timeout=timeout)


class Agents(_Group):
    def start(self, task: str, *, role: str | None = None, context: str = "",
              attachments: list[str] | None = None) -> dict[str, Any]:
        return self._call("start", task=task, role=role, context=context, attachments=attachments)

    def message(self, agent_id: str, text: str) -> dict[str, Any]:
        return self._call("message", agent_id=agent_id, text=text)

    def update(self, agent_id: str, task: str) -> dict[str, Any]:
        return self._call("update", agent_id=agent_id, task=task)

    def status(self, agent_id: str) -> dict[str, Any]:
        return self._call("status", agent_id=agent_id)

    def wait(self, agent_id: str, timeout: float = 60) -> dict[str, Any]:
        return self._call("wait", agent_id=agent_id, timeout=timeout)

    def inspect(self, agent_id: str, cursor: int = 0, limit: int = 20) -> dict[str, Any]:
        return self._call("inspect", agent_id=agent_id, cursor=cursor, limit=limit)

    def result(self, agent_id: str) -> dict[str, Any]:
        return self._call("result", agent_id=agent_id)

    def stop(self, agent_id: str) -> dict[str, Any]:
        return self._call("stop", agent_id=agent_id)


class Environment(_Group):
    def info(self) -> dict[str, Any]:
        return self._call("info")


class Settings(_Group):
    def get(self) -> dict[str, Any]:
        """This skill's settings: the manifest defaults overlaid with the person's values."""
        return self._call("get")


class Permissions(_Group):
    def current(self) -> dict[str, Any]:
        return self._call("current")

    def request(self, capability: str, reason: str, scope: dict[str, Any] | None = None) -> dict[str, Any]:
        """Ask the person (through the core) for a capability for this task; {granted, status}."""
        return self._call("request", capability=capability, reason=reason, scope=scope)


class Artifacts(_Group):
    def save(self, path: str, *, name: str = "", kind: str = "file", caption: str = "") -> dict[str, Any]:
        """Keep an exchange file as a result for the person; returns {artifact_id, path}."""
        return self._call("save", path=str(path), name=name or None, kind=kind, caption=caption)

    def list(self) -> list[dict[str, Any]]:
        return self._call("list")


class Log(_Group):
    def write(self, text: str, level: str = "info") -> None:
        self._call("write", text=text, level=level)


class Display(_Group):
    def info(self) -> dict[str, Any]:
        """{display, width, height, viewer_url}: the skill's own virtual screen."""
        return self._call("info")

    def screenshot(self, name: str = "screen.png") -> dict[str, Any]:
        """Save the whole virtual screen as an artifact."""
        return self._call("screenshot", name=name)


class Network(_Group):
    def proxy(self) -> dict[str, Any]:
        """{url}: the only way out of the sandbox; the core decides every destination."""
        return self._call("proxy")

    def services(self) -> list[dict[str, Any]]:
        """Servers this task started that the skill may reach: {port, label, url}."""
        return self._call("services")


class Context:
    """What ``execute(tool_name, arguments, context)`` receives."""

    def __init__(self, transport: Transport, *, call_id: str, tool: str) -> None:
        self.call_id = call_id
        self.tool = tool
        self.exchange = EXCHANGE_DIR
        self.input = Input(transport, "input")
        self.images = Images(transport, "images")
        self.models = Models(transport, "models")
        self.files = Files(transport, "files")
        self.changes = Changes(transport, "changes")
        self.git = Git(transport, "git")
        self.processes = Processes(transport, "processes")
        self.web = Web(transport, "web")
        self.http = Http(transport, "http")
        self.connections = Connections(transport, "connections")
        self.ui = Ui(transport, "ui")
        self.sessions = Sessions(transport, "sessions")
        self.memory = Memory(transport, "memory")
        self.storage = Storage(transport, "storage")
        self.runtime = Runtime(transport, "runtime")
        self.events = Events(transport, "events")
        self.agents = Agents(transport, "agents")
        self.environment = Environment(transport, "environment")
        self.settings = Settings(transport, "settings")
        self.permissions = Permissions(transport, "permissions")
        self.artifacts = Artifacts(transport, "artifacts")
        self.log = Log(transport, "log")
        self.display = Display(transport, "display")
        self.network = Network(transport, "network")


def raise_for_error(error: dict[str, Any]) -> None:
    code = str(error.get("code") or "error")
    raise _ERRORS.get(code, SkillError)(code, str(error.get("message") or ""))
