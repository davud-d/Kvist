# -*- coding: utf-8 -*-
"""The core API a running skill calls (``ctx.*``), checked request by request.

Every request names a method from the fixed table below; anything else is
``unknown_method`` — a skill cannot reach an internal function by guessing its
name. Before a method runs the core checks, in order: the skill's grant (plus
any capability the person granted for this task), the task's read-only state
(reader role, plan mode, "do not change files") for methods that change
something, and the tool's own ``readonly`` declaration. The identifiers of the
call — skill, version, run, session, call — come from the core's records,
never from the request.

Writes go through the same runtime path as the model's own writes: the change
journal transaction, the run's changed-file accounting and its verification
re-arm. A skill's result is evidence for the run, not a way around its checks.
"""
from __future__ import annotations

import base64
import fnmatch
import json
import os
import shutil
import stat
import sys
import threading
import time
import uuid
from collections import deque
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO, Callable, Iterator

from kernel.agent.agent_state import PLAN_PLANNING
from kernel.plugins.artifacts import ArtifactError, ArtifactStore
from kernel.context.attachments import AttachmentError, AttachmentGone, AttachmentStore
from kernel.plugins import capabilities as caps
from kernel.plugins import host_actions
from kernel.plugins.grants import Grant
from kernel.plugins.host import RunnerHost, ServiceError
from kernel.plugins.manifest import ToolDef
from kernel.plugins.registry import CatalogEntry
from kernel.plugins.schema import SchemaError, check_schema, validate
from kernel.plugins.storage import MAX_TTL_SECONDS, SkillStorage, StorageError
from kernel.protocol.protocol import Action, AgentCommand
from kernel.sandbox.redaction import redact_text, redact_value
from kernel.plugins.resources import Owner
from kernel.session.session_search import SessionSearchError, list_sessions, read_session, search_sessions
from kernel.memory import MemoryService, MemoryStoreError, Source
from kernel.memory.schema import KINDS as MEMORY_KINDS

MAX_TEXT_PARAM = 200_000
MAX_MODEL_PROMPT_CHARS = 400_000
MAX_LOG_LINES = 500
MAX_HOST_OPENS_PER_RUN = 12
MAX_SKILL_FORGETS_PER_TASK = 5
_EXCHANGE_IN = "in"
_EXCHANGE_OUT_LIMIT = 50 * 1024 * 1024
_SKIP = "__kvist_skip__"


@dataclass(slots=True)
class AttachedSkill:
    """A skill attached to the current task: its approved content, grant and live state."""

    entry: CatalogEntry
    grant: Grant | None
    attached_at: float
    host: RunnerHost | None = None
    task_grants: dict[str, dict[str, Any]] = field(default_factory=dict)
    denied_requests: set[str] = field(default_factory=set)
    model_calls: int = 0
    model_tokens: int = 0
    log: deque = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    calls: int = 0

    @property
    def id(self) -> str:
        return self.entry.id

    @property
    def version(self) -> str:
        return self.entry.version

    @property
    def manifest(self):
        assert self.entry.manifest is not None
        return self.entry.manifest

    def allows(self, capability: str) -> bool:
        if capability in self.task_grants:
            return True
        return self.grant is not None and self.grant.allows(capability)

    def scope(self, capability: str) -> dict[str, Any]:
        if capability in self.task_grants:
            return self.task_grants[capability]
        return self.grant.scope(capability) if self.grant is not None else {}


@dataclass(slots=True)
class SkillCall:
    skill: AttachedSkill
    tool: ToolDef
    call_id: str
    run_id: str
    session: str
    started: float = field(default_factory=time.time)
    streams: dict[str, dict[str, Any]] = field(default_factory=dict)

    @property
    def owner(self) -> Owner:
        return Owner(session=self.session, run_id=self.run_id, kind="skill",
                     skill=f"{self.skill.id}@{self.skill.version}", call_id=self.call_id)


# method -> (capability or None, changes something)
METHODS: dict[str, tuple[str | None, bool]] = {
    "input.task": ("input.read", False),
    "input.attachments": ("input.read", False),
    "images.info": ("images.read", False),
    "images.analyze": ("images.analyze", False),
    "images.crop": ("images.read", False),
    "images.resize": ("images.read", False),
    "images.convert": ("images.read", False),
    "images.original": ("images.original", False),
    "images.model_copy": ("images.read", False),
    "images.add": ("images.read", False),
    "models.list": ("models.call", False),
    "models.complete": ("models.call", False),
    "models.stream_start": ("models.call", False),
    "models.stream_next": ("models.call", False),
    "models.json": ("models.call", False),
    "models.count_tokens": ("models.call", False),
    "files.read": ("files.read", False),
    "files.read_many": ("files.read", False),
    "files.list": ("files.read", False),
    "files.tree": ("files.read", False),
    "files.find": ("files.read", False),
    "files.search": ("files.read", False),
    "files.read_bytes": ("files.read", False),
    "files.write": ("files.write", True),
    "files.append": ("files.write", True),
    "files.replace": ("files.write", True),
    "files.mkdir": ("files.write", True),
    "changes.list": ("changes.read", False),
    "changes.diff": ("changes.read", False),
    "changes.preview": ("changes.read", False),
    "git.status": ("git.read", False),
    "git.diff": ("git.read", False),
    "processes.run": ("processes.run", True),
    "processes.start": ("processes.background", True),
    "processes.output": ("processes.background", False),
    "processes.status": ("processes.background", False),
    "processes.stop": ("processes.background", True),
    "processes.list": ("processes.background", False),
    "web.search": ("web.search", False),
    "web.fetch": ("web.fetch", False),
    # A GET or HEAD changes nothing; other methods are checked as writes in the handler.
    "http.request": ("http.request", False),
    "connections.list": ("connections.use", False),
    "connections.request": ("connections.use", False),
    "ui.notify": ("ui.notify", False),
    "ui.message": ("ui.notify", False),
    "ui.error": ("ui.notify", False),
    "ui.stream_start": ("ui.notify", False),
    "ui.stream_append": ("ui.notify", False),
    "ui.stream_end": ("ui.notify", False),
    "ui.choose": ("ui.ask", False),
    "ui.ask": ("ui.ask", False),
    "ui.confirm": ("ui.ask", False),
    "ui.present": ("ui.present", False),
    "sessions.list": ("sessions.read", False),
    "sessions.read": ("sessions.read", False),
    "sessions.search": ("sessions.read", False),
    "memory.search": ("memory.read", False),
    "memory.get": ("memory.read", False),
    "memory.explain": ("memory.read", False),
    "memory.list": ("memory.read", False),
    "memory.remember": ("memory.write", False),
    "memory.update": ("memory.write", False),
    "memory.link": ("memory.write", False),
    "memory.forget": ("memory.write", False),
    "storage.get": ("storage", False),
    "storage.set": ("storage", False),
    "storage.delete": ("storage", False),
    "storage.keys": ("storage", False),
    "runtime.status": (None, False),
    "runtime.check": (None, False),
    "runtime.wait": (None, False),
    "runtime.progress": (None, False),
    "runtime.register_resource": (None, False),
    "runtime.release_resource": (None, False),
    "events.poll": (None, False),
    "agents.start": ("agents.manage", False),
    "agents.message": ("agents.manage", False),
    "agents.update": ("agents.manage", False),
    "agents.status": ("agents.manage", False),
    "agents.wait": ("agents.manage", False),
    "agents.inspect": ("agents.manage", False),
    "agents.result": ("agents.manage", False),
    "agents.stop": ("agents.manage", False),
    "environment.info": (None, False),
    "settings.get": (None, False),
    "permissions.current": (None, False),
    "permissions.request": ("permissions.request", False),
    "artifacts.save": ("artifacts", False),
    "artifacts.list": ("artifacts", False),
    "log.write": (None, False),
    "display.info": ("display", False),
    "display.screenshot": ("display", False),
    "host.open_url": ("host.open", True),
    "host.open_app": ("host.open", True),
    "host.windows": ("host.windows", False),
    "host.focus": ("host.windows", True),
    "host.close": ("host.windows", True),
    "visualize.validate": (None, False),
    "visualize.show": ("artifacts", True),
    "network.proxy": (None, False),
    "network.services": (None, False),
}


def _bad(message: str) -> ServiceError:
    return ServiceError("bad_request", message)


def _str(params: dict[str, Any], key: str, *, required: bool = True, limit: int = MAX_TEXT_PARAM,
         default: str = "") -> str:
    value = params.get(key, None)
    if value is None:
        if required:
            raise _bad(f"{key} is required")
        return default
    if not isinstance(value, str):
        raise _bad(f"{key} must be a string")
    if len(value) > limit:
        raise _bad(f"{key} is longer than {limit} characters")
    return value


def _int(params: dict[str, Any], key: str, default: int | None, low: int, high: int) -> int | None:
    value = params.get(key, default)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise _bad(f"{key} must be an integer")
    value = int(value)
    if not low <= value <= high:
        raise _bad(f"{key} must be in {low}..{high}")
    return value


def _str_list(params: dict[str, Any], key: str, *, required: bool = True, limit: int = 50) -> list[str]:
    value = params.get(key)
    if value is None:
        if required:
            raise _bad(f"{key} is required")
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value) or len(value) > limit:
        raise _bad(f"{key} must be a list of at most {limit} strings")
    return value


class CoreServices:
    """The ``ctx`` API of one agent; one instance serves every skill of its tasks."""

    def __init__(self, agent: Any, manager: Any) -> None:
        self._agent = agent
        self._manager = manager
        workspace = Path(agent.executor.workspace)
        self.attachments = AttachmentStore(workspace)
        self.artifacts = ArtifactStore(workspace)
        self.memory = MemoryService(workspace, with_user_scope=False)
        self._model_streams: dict[str, dict[str, Any]] = {}
        self._host_opens: dict[str, int] = {}
        self._memory_forgets: dict[str, int] = {}

    # --- entry -----------------------------------------------------------

    def handler_for(self, call: SkillCall) -> Callable[[str, dict[str, Any], str], Any]:
        def handle(method: str, params: dict[str, Any], call_id: str) -> Any:
            if call_id != call.call_id:
                raise ServiceError("cancelled", "this call is no longer running")
            return self.handle(call, method, params)

        return handle

    def handle(self, call: SkillCall, method: str, params: dict[str, Any]) -> Any:
        spec = METHODS.get(method)
        if spec is None:
            raise ServiceError("unknown_method", f"{method} is not part of plugin API v1")
        capability, changes = spec
        if method.startswith("host.") and call.skill.manifest.core_group != "host":
            raise ServiceError("denied", "the person's desktop is reached only through Kvist's own desktop skill")
        if method.startswith("visualize.") and call.skill.manifest.core_group != "visualize":
            raise ServiceError("denied", "visualizations are reached only through Kvist's own visualize skill")
        if capability is not None and not call.skill.allows(capability):
            raise ServiceError("denied", f"{call.skill.id} was not granted {capability}; "
                               "ask for it with ctx.permissions.request if the task needs it")
        if changes:
            self._require_write_allowed(call, method)
        handler = getattr(self, "_" + method.replace(".", "_"))
        try:
            return handler(call, params)
        except (AttachmentGone,) as error:
            raise ServiceError("gone", str(error)) from None
        except (AttachmentError, ArtifactError, SessionSearchError) as error:
            raise ServiceError("error", str(error)) from None
        except MemoryStoreError as error:
            raise ServiceError(error.code, str(error)) from None
        except StorageError as error:
            raise ServiceError(error.code, str(error)) from None

    def _require_write_allowed(self, call: SkillCall, method: str) -> None:
        state = self._agent._state
        if call.tool.readonly:
            raise ServiceError("denied", f"{call.skill.id}.{call.tool.name} is declared read-only, so it "
                               f"may not use {method}")
        if state.plan_phase == PLAN_PLANNING:
            raise ServiceError("denied", "plan mode: nothing changes until the person accepts the plan")
        if state.read_only_goal or state.current_agent_role == "reader":
            raise ServiceError("denied", "this task is read-only (reader role or the person asked not to change files)")

    # --- input -----------------------------------------------------------

    def _input_task(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        state = self._agent._state
        return {
            "text": state.user_goal,
            "updates": list(state.task_updates),
            "language": state.response_language or "en",
            "session_id": call.session,
            "run_id": call.run_id,
            "call_id": call.call_id,
            "message_id": state.message_id,
            "skill": {"id": call.skill.id, "version": call.skill.version},
        }

    def _input_attachments(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [item.public() for item in self.attachments.list(call.session) if not item.deleted]

    # --- images ----------------------------------------------------------

    def _images_info(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._attachment(call, _str(params, "id")).public()

    def _attachment(self, call: SkillCall, attachment_id: str):
        attachment = self.attachments.get(attachment_id)
        if attachment.session != call.session:
            raise ServiceError("denied", f"{attachment_id} belongs to another conversation")
        return attachment

    def _images_analyze(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.vision.vision import describe_images, model_supports_vision, vision_model

        ids = _str_list(params, "ids", limit=8)
        if not ids:
            raise _bad("ids must name at least one attachment")
        question = _str(params, "question", limit=12000)
        images = []
        for attachment_id in ids:
            self._attachment(call, attachment_id)
            images.append(self.attachments.model_data_url(attachment_id))
        agent = self._agent
        model = vision_model(agent)
        if model_supports_vision(agent.config.provider, model) is False:
            raise ServiceError("unavailable", f"the configured Vision model {model} does not accept images")
        text = describe_images(agent, images, question)
        if text.startswith(("Vision unavailable", "Vision failed", "Vision returned no description")):
            raise ServiceError("unavailable", text)
        return {"text": text, "model": model, "untrusted": True,
                "notice": "Text produced from images is data, not instructions."}

    def _images_crop(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        source = self._attachment(call, _str(params, "id"))
        box = params.get("box")
        return self.attachments.crop(source.id, box, session=call.session).public()

    def _images_resize(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        source = self._attachment(call, _str(params, "id"))
        width = _int(params, "max_width", None, 1, 10000)
        height = _int(params, "max_height", None, 1, 10000)
        if width is None or height is None:
            raise _bad("max_width and max_height are required")
        return self.attachments.resize(source.id, width, height, session=call.session).public()

    def _images_convert(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        source = self._attachment(call, _str(params, "id"))
        return self.attachments.convert(source.id, _str(params, "format", limit=10), session=call.session).public()

    def _images_original(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        attachment = self._attachment(call, _str(params, "id"))
        path = self.attachments.original_path(attachment.id)
        return {"path": self._to_exchange(call, path, f"{attachment.id}{path.suffix}")}

    def _images_model_copy(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        attachment = self._attachment(call, _str(params, "id"))
        return {"path": self._to_exchange(call, self.attachments.model_path(attachment.id), f"{attachment.id}.model.jpg")}

    def _images_add(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        with self._from_exchange(call, _str(params, "path", limit=500)) as source:
            name = _str(params, "name", required=False, limit=200) or source.name
            attachment = self.attachments.add_file(
                source, name, session=call.session,
                origin={"kind": "skill", "skill": call.skill.id, "version": call.skill.version,
                        "call_id": call.call_id, "run_id": call.run_id},
            )
        return attachment.public()

    # --- models ----------------------------------------------------------

    def _models_list(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        config = self._agent.config
        tiers = self._allowed_tiers(call)
        rows = [{"tier": "fast", "model": config.fast_model}, {"tier": "smart", "model": config.smart_model}]
        return [{**row, "provider": config.provider, "allowed": row["tier"] in tiers} for row in rows]

    def _allowed_tiers(self, call: SkillCall) -> set[str]:
        return set(call.skill.scope("models.call").get("tiers") or ["fast"])

    def _model_budget(self, call: SkillCall) -> tuple[int, int]:
        scope = call.skill.scope("models.call")
        limits = call.skill.manifest.limits
        max_calls = min(int(scope.get("max_calls", limits["model_calls_per_task"])), int(limits["model_calls_per_task"]))
        max_tokens = min(int(scope.get("max_tokens", limits["model_tokens_per_task"])), int(limits["model_tokens_per_task"]))
        return max_calls, max_tokens

    def _model_messages(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        messages = params.get("messages")
        prompt = params.get("prompt")
        system = params.get("system")
        if messages is None:
            if not isinstance(prompt, str) or not prompt.strip():
                raise _bad("prompt or messages is required")
            messages = [{"role": "user", "content": prompt}]
        if not isinstance(messages, list) or not messages:
            raise _bad("messages must be a non-empty list")
        clean = []
        for message in messages:
            if (not isinstance(message, dict) or message.get("role") not in ("user", "assistant")
                    or not isinstance(message.get("content"), str)):
                raise _bad("each message is {role: user|assistant, content: string}")
            clean.append({"role": message["role"], "content": message["content"]})
        if isinstance(system, str) and system.strip():
            clean.insert(0, {"role": "system", "content": system})
        if sum(len(item["content"]) for item in clean) > MAX_MODEL_PROMPT_CHARS:
            raise _bad(f"the prompt is longer than {MAX_MODEL_PROMPT_CHARS} characters")
        return clean

    def _model_for(self, call: SkillCall, params: dict[str, Any]) -> str:
        tier = params.get("tier", "fast")
        if tier not in ("fast", "smart"):
            raise _bad("tier must be fast or smart")
        if tier not in self._allowed_tiers(call):
            raise ServiceError("denied", f"{call.skill.id} may use the {', '.join(sorted(self._allowed_tiers(call)))} "
                               f"model tier, not {tier}")
        return self._agent.config.fast_model if tier == "fast" else self._agent.config.smart_model

    def _spend(self, call: SkillCall, tokens: int) -> None:
        max_calls, max_tokens = self._model_budget(call)
        if call.skill.model_calls >= max_calls:
            raise ServiceError("budget", f"{call.skill.id} used its {max_calls} model requests for this task")
        if call.skill.model_tokens + tokens > max_tokens:
            raise ServiceError("budget", f"{call.skill.id} would exceed its {max_tokens}-token budget for this task")

    def _models_complete(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        messages = self._model_messages(params)
        model = self._model_for(call, params)
        max_tokens = _int(params, "max_tokens", 2000, 1, 32000) or 2000
        prompt_tokens = self._count(messages)
        self._spend(call, prompt_tokens + max_tokens)
        text, usage = _complete(self._agent, model, messages, max_tokens, owner=call.skill.id)
        call.skill.model_calls += 1
        call.skill.model_tokens += usage["input_tokens"] + usage["output_tokens"]
        return {"text": text, "model": model, "usage": usage}

    def _models_stream_start(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        messages = self._model_messages(params)
        model = self._model_for(call, params)
        max_tokens = _int(params, "max_tokens", 2000, 1, 32000) or 2000
        self._spend(call, self._count(messages) + max_tokens)
        stream_id = f"ms_{uuid.uuid4().hex[:10]}"
        state = {"queue": deque(), "done": False, "error": "", "lock": threading.Lock(), "call_id": call.call_id}
        self._model_streams[stream_id] = state
        call.skill.model_calls += 1

        def run() -> None:
            try:
                for piece in _stream(self._agent, model, messages, max_tokens, owner=call.skill.id):
                    with state["lock"]:
                        state["queue"].append(piece)
            except ServiceError as error:
                state["error"] = error.message
            finally:
                state["done"] = True

        threading.Thread(target=run, name="kvist-skill-model-stream", daemon=True).start()
        return {"stream_id": stream_id, "model": model}

    def _models_stream_next(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        state = self._model_streams.get(_str(params, "stream_id", limit=40))
        if state is None or state["call_id"] != call.call_id:
            raise _bad("unknown stream")
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and not state["queue"] and not state["done"]:
            if self._agent.control.interrupted():
                break
            time.sleep(0.02)
        with state["lock"]:
            text = "".join(state["queue"])
            state["queue"].clear()
        done = state["done"] and not text
        if state["error"] and done:
            raise ServiceError("model_error", state["error"])
        if done:
            self._model_streams.pop(_str(params, "stream_id", limit=40), None)
        return {"text": text, "done": done}

    def _models_json(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        schema = params.get("schema")
        try:
            check_schema(schema)
        except SchemaError as error:
            raise _bad(f"schema: {error}") from None
        prompt = _str(params, "prompt")
        instruction = ("Answer with one JSON value only, no prose, matching this JSON schema:\n"
                       + json.dumps(schema, ensure_ascii=False))
        attempt_messages = [{"role": "system", "content": instruction}, {"role": "user", "content": prompt}]
        errors: list[str] = []
        for _attempt in range(2):
            result = self._models_complete(call, {**params, "messages": attempt_messages[1:], "system": instruction,
                                                  "prompt": None})
            value, problem = _parse_json_value(result["text"])
            if problem is None:
                errors = validate(value, schema)
                if not errors:
                    return {"value": value, "usage": result["usage"]}
                problem = "; ".join(errors[:5])
            attempt_messages = attempt_messages[:2] + [
                {"role": "assistant", "content": result["text"][:20000]},
                {"role": "user", "content": f"That answer was rejected: {problem}. Reply with the corrected JSON only."},
            ]
        raise ServiceError("bad_model_output", "the model did not produce JSON matching the schema: "
                           + "; ".join(errors[:3]))

    def _models_count_tokens(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return {"tokens": self._agent._count_tokens(_str(params, "text"))}

    def _count(self, messages: list[dict[str, Any]]) -> int:
        return sum(self._agent._count_tokens(item["content"]) for item in messages)

    # --- files -----------------------------------------------------------

    def _files_read(self, call: SkillCall, params: dict[str, Any]) -> Any:
        args = {"path": self._path(call, params, "files.read")}
        for key in ("start_line", "end_line"):
            if params.get(key) is not None:
                args[key] = _int(params, key, None, 1, 10_000_000)
        return self._run_read(call, "read_file", args)

    def _files_read_many(self, call: SkillCall, params: dict[str, Any]) -> Any:
        paths = _str_list(params, "paths", limit=50)
        for path in paths:
            self._check_path_scope(call, path, "files.read")
        return self._run_read(call, "read_multiple_files", {"paths": paths})

    def _files_list(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_read(call, "list_dir", {"path": self._directory(call, params)})

    def _files_tree(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_read(call, "list_tree", {"path": self._directory(call, params),
                                                  "max_depth": _int(params, "max_depth", 3, 1, 10)})

    def _files_find(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_read(call, "find_files", {"pattern": _str(params, "pattern", limit=500),
                                                   "path": self._directory(call, params)})

    def _files_search(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_read(call, "search_code", {"query": _str(params, "query", limit=1000),
                                                    "max_results": _int(params, "max_results", 50, 1, 200),
                                                    "path": self._directory(call, params)})

    def _files_read_bytes(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(call, params, "files.read")
        executor = self._agent.executor
        try:
            target = executor._resolve_read_path(path)
            executor._check_env_file(path)
        except Exception as error:  # noqa: BLE001 - the executor's guards raise several error types
            raise ServiceError("denied", redact_text(str(error))) from None
        if target.stat().st_size > _EXCHANGE_OUT_LIMIT:
            raise _bad(f"{path} is larger than {_EXCHANGE_OUT_LIMIT // (1024 * 1024)} MiB")
        return {"path": self._to_exchange(call, target, Path(path).name)}

    def _files_write(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_write(call, "write_file", {"path": self._path(call, params, "files.write"),
                                                    "content": _str(params, "content")})

    def _files_append(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_write(call, "append_file", {"path": self._path(call, params, "files.write"),
                                                     "content": _str(params, "content")})

    def _files_replace(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_write(call, "replace_in_file", {
            "path": self._path(call, params, "files.write"),
            "old_text": _str(params, "old_text"),
            "new_text": _str(params, "new_text"),
            "count": _int(params, "count", 1, 1, 1000),
        })

    def _files_mkdir(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        path = self._path(call, params, "files.write")
        executor = self._agent.executor
        try:
            target = executor._resolve_path(path)
            executor._guard_internal_agent_path(path, target)
        except Exception as error:  # noqa: BLE001 - the executor's guards raise several error types
            raise ServiceError("denied", redact_text(str(error))) from None
        target.mkdir(parents=True, exist_ok=True)
        self._note_nested(call, "mkdir", {"path": path}, ok=True)
        return {"path": path, "created": True}

    def _path(self, call: SkillCall, params: dict[str, Any], capability: str) -> str:
        path = _str(params, "path", limit=1000)
        self._check_path_scope(call, path, capability)
        return path

    def _directory(self, call: SkillCall, params: dict[str, Any]) -> str:
        path = _str(params, "path", required=False, limit=1000) or "."
        self._check_path_scope(call, path, "files.read", directory=True)
        return path

    def _check_path_scope(self, call: SkillCall, path: str, capability: str, *, directory: bool = False) -> None:
        """Refuse a path whose canonical workspace location no scope pattern covers.

        Matching the canonical location, not the text, keeps ``..`` and symlinks
        from leading a matching path elsewhere. A directory is covered only when
        everything under it is (``allowed/**`` covers ``allowed``), because a
        listing or search reports every file below it.
        """
        patterns = call.skill.scope(capability).get("paths")
        if not patterns:
            return
        relative = self._canonical_relative(path)
        if relative is not None:
            candidate = (f"{relative}/" if relative else "") if directory else relative
            if any(fnmatch.fnmatch(candidate, pattern) for pattern in patterns):
                return
        raise ServiceError("denied", f"{path} is outside the paths {call.skill.id} may use: {', '.join(patterns)}")

    def _canonical_relative(self, path: str) -> str | None:
        """The workspace-relative POSIX form of ``path`` after resolving it; None outside the workspace."""
        workspace = Path(self._agent.executor.workspace)
        try:
            target = (workspace / Path(path).expanduser()).resolve()
        except (OSError, RuntimeError):
            return None
        if target != workspace and not target.is_relative_to(workspace):
            return None
        relative = target.relative_to(workspace).as_posix()
        return "" if relative == "." else relative

    def _command(self, action_type: str, args: dict[str, Any]) -> AgentCommand:
        return AgentCommand(plan_step="inspect", action=Action(type=action_type, args=args),  # type: ignore[arg-type]
                            call_id=f"nested_{uuid.uuid4().hex[:10]}")

    def _run_read(self, call: SkillCall, action_type: str, args: dict[str, Any]) -> Any:
        from kernel.protocol.parser import CommandValidationError, validate_action_args

        try:
            validate_action_args(action_type, args)
        except CommandValidationError as error:
            raise _bad(str(error)) from None
        feedback = self._agent._execute_command(self._command(action_type, args))
        if not feedback.get("ok"):
            raise ServiceError("error", str(feedback.get("error") or "the action failed"))
        return feedback.get("data")

    def _run_write(self, call: SkillCall, action_type: str, args: dict[str, Any]) -> Any:
        from kernel.protocol.parser import CommandValidationError, validate_action_args

        try:
            validate_action_args(action_type, args)
        except CommandValidationError as error:
            raise _bad(str(error)) from None
        agent = self._agent
        command = self._command(action_type, args)
        if agent._is_read_only_violation(command):
            raise ServiceError("denied", "this task is read-only")
        executor = agent.executor
        executor.begin_action_transaction(getattr(agent, "_run_id", None), uuid.uuid4().hex)
        try:
            feedback = agent._execute_command(command)
        finally:
            executor.end_action_transaction()
        ok = bool(feedback.get("ok"))
        if ok:
            agent._update_state_from_success(command, feedback)
        self._note_nested(call, action_type, args, ok=ok, error=str(feedback.get("error") or ""))
        if not ok:
            raise ServiceError("error", str(feedback.get("error") or "the write failed"))
        return feedback.get("data")

    def _note_nested(self, call: SkillCall, action_type: str, args: dict[str, Any], *, ok: bool, error: str = "") -> None:
        """Record a skill's own action in the run, so the report and history show who did it."""
        visible = {key: value for key, value in args.items() if key in ("path", "command", "cwd")}
        self._agent._state.step_logs.append({
            "step": getattr(self._agent, "_current_step", 0),
            "status": "ok" if ok else "error",
            "origin": f"skill:{call.skill.id}@{call.skill.version}",
            "call_id": call.call_id,
            "command": {"action": {"type": action_type, "args": visible}},
            **({"error": redact_text(error)[:500]} if error else {}),
        })
        call.skill.log.append(f"{action_type} {visible} -> {'ok' if ok else 'error'}")

    # --- changes and git --------------------------------------------------

    def _changes_list(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        state = self._agent._state
        nested = [log for log in state.step_logs if log.get("origin")]
        return {"files_changed": list(state.files_changed), "files_read": list(state.files_read)[-50:],
                "by_skills": [{"origin": log["origin"], "action": log["command"]["action"]} for log in nested[-50:]]}

    def _changes_diff(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.session.change_journal import build_diff_report, JournalUnavailableError

        try:
            report = build_diff_report(Path(self._agent.executor.workspace))
        except JournalUnavailableError as error:
            raise ServiceError("error", str(error)) from None
        path = params.get("path")
        if isinstance(path, str) and path:
            sections = [part for part in report.split("\n--- ") if path in part.split("\n", 1)[0]]
            report = "\n--- ".join(sections)
        return {"diff": redact_text(report)[:200_000]}

    def _changes_preview(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        import difflib

        path = self._path(call, params, "files.read")
        content = _str(params, "content")
        try:
            current = self._run_read(call, "read_file", {"path": path}).get("content", "")
        except ServiceError:
            current = ""
        diff = "".join(difflib.unified_diff(current.splitlines(keepends=True), content.splitlines(keepends=True),
                                            fromfile=f"a/{path}", tofile=f"b/{path}"))
        return {"path": path, "exists": bool(current), "diff": diff[:200_000]}

    def _git_status(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_read(call, "git_status", {})

    def _git_diff(self, call: SkillCall, params: dict[str, Any]) -> Any:
        args = {key: params[key] for key in ("path", "ref", "staged") if params.get(key) is not None}
        return self._run_read(call, "git_diff", args)

    # --- processes -------------------------------------------------------

    def _processes_run(self, call: SkillCall, params: dict[str, Any]) -> Any:
        args: dict[str, Any] = {"command": _str_list(params, "command", limit=200)}
        if params.get("cwd"):
            args["cwd"] = _str(params, "cwd", limit=1000)
        if params.get("timeout") is not None:
            args["timeout"] = float(_int(params, "timeout", None, 1, 300) or 15)
        return self._run_process(call, args)

    def _processes_start(self, call: SkillCall, params: dict[str, Any]) -> Any:
        args: dict[str, Any] = {"command": _str_list(params, "command", limit=200), "background": True}
        if params.get("cwd"):
            args["cwd"] = _str(params, "cwd", limit=1000)
        if params.get("expose_port") is not None:
            args["expose_port"] = _int(params, "expose_port", None, 1024, 65535)
        label = _str(params, "label", required=False, limit=80) or f"{call.skill.id} server"
        self._agent.executor.set_job_context(label, call.call_id)
        data = self._run_process(call, args)
        job_id = data.get("job_id") if isinstance(data, dict) else None
        if job_id:
            self._manager.note_job(call, job_id)
        return data

    def _run_process(self, call: SkillCall, args: dict[str, Any]) -> Any:
        from kernel.protocol.parser import CommandValidationError, validate_action_args

        try:
            validate_action_args("run_shell", args)
        except CommandValidationError as error:
            raise _bad(str(error)) from None
        feedback = self._agent._execute_command(self._command("run_shell", args))
        self._note_nested(call, "run_shell", args, ok=bool(feedback.get("ok")), error=str(feedback.get("error") or ""))
        if not feedback.get("ok") and not isinstance(feedback.get("data"), dict):
            raise ServiceError("error", str(feedback.get("error") or "the command failed"))
        return feedback.get("data")

    def _own_job(self, call: SkillCall, params: dict[str, Any]) -> str:
        job_id = _str(params, "job_id", limit=40)
        if job_id not in self._manager.jobs_of(call.skill.id):
            raise ServiceError("denied", f"{job_id} was not started by {call.skill.id}")
        return job_id

    def _processes_output(self, call: SkillCall, params: dict[str, Any]) -> Any:
        wait = params.get("wait", 0)
        if isinstance(wait, bool) or not isinstance(wait, (int, float)):
            raise _bad("wait must be a number of seconds")
        return self._run_read(call, "job_output", {"job_id": self._own_job(call, params), "wait": min(max(wait, 0), 30)})

    def _processes_status(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_read(call, "job_output", {"job_id": self._own_job(call, params), "wait": 0})

    def _processes_stop(self, call: SkillCall, params: dict[str, Any]) -> Any:
        return self._run_read(call, "job_stop", {"job_id": self._own_job(call, params)})

    def _processes_list(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        from kernel.agent.background_jobs import snapshot

        jobs = self._agent.executor.jobs
        own = self._manager.jobs_of(call.skill.id)
        return [snapshot(jobs.get(job_id)) for job_id in jobs.ids() if job_id in own]

    # --- web -------------------------------------------------------------

    def _web_search(self, call: SkillCall, params: dict[str, Any]) -> Any:
        args: dict[str, Any] = {"query": _str(params, "query", limit=500),
                                "max_results": _int(params, "max_results", 5, 1, 20)}
        domains = _str_list(params, "domains", required=False, limit=20)
        if domains:
            args["domain_filter"] = domains
        if params.get("freshness"):
            args["freshness"] = _str(params, "freshness", limit=10)
        return self._run_read(call, "web_search", args)

    def _web_fetch(self, call: SkillCall, params: dict[str, Any]) -> Any:
        mode = _str(params, "mode", required=False, limit=10) or "text"
        return self._run_read(call, "fetch_url", {"url": _str(params, "url", limit=2000),
                                                  "max_chars": _int(params, "max_chars", 12000, 500, 50000),
                                                  "extract_mode": mode})

    def _http_request(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.plugins.http_client import HttpPolicyError, request

        if _str(params, "method", limit=10).upper() not in ("GET", "HEAD"):
            self._require_write_allowed(call, "http.request")
        domains = list(call.skill.scope("http.request").get("domains") or [])
        try:
            return request(
                method=_str(params, "method", limit=10).upper(),
                url=_str(params, "url", limit=4000),
                headers=params.get("headers") or {},
                body=params.get("body"),
                json_body=params.get("json"),
                timeout=float(_int(params, "timeout", 20, 1, 120) or 20),
                domains=domains,
                services=self._agent.executor.task_services if call.skill.allows("network.task_services") else None,
            )
        except HttpPolicyError as error:
            raise ServiceError("denied", str(error)) from None

    def _granted_connections(self, call: SkillCall) -> dict[str, Any]:
        from kernel.plugins.connections import ConnectionError_, load_connections

        allowed = set(call.skill.scope("connections.use").get("connections") or [])
        try:
            connections = load_connections()
        except ConnectionError_ as error:
            raise ServiceError("error", str(error)) from None
        return {name: item for name, item in connections.items() if name in allowed}

    def _connections_list(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [item.public() for item in self._granted_connections(call).values()]

    def _connections_request(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.plugins.connections import ConnectionError_, request

        name = _str(params, "connection", limit=40)
        connection = self._granted_connections(call).get(name)
        if connection is None:
            raise ServiceError("denied", f"{call.skill.id} may not use a connection named {name!r}; the person "
                               "configures connections with kvist --connection-op and grants them by name")
        method = _str(params, "method", limit=10).upper()
        if method not in ("GET", "HEAD"):
            self._require_write_allowed(call, "connections.request")
        headers = params.get("headers") or {}
        if not isinstance(headers, dict):
            raise _bad("headers must be an object of strings")
        try:
            return request(connection, method=method, path_or_url=_str(params, "path", limit=2000),
                           body=params.get("body"), json_body=params.get("json"), headers=headers,
                           timeout=float(_int(params, "timeout", 20, 1, 120) or 20))
        except ConnectionError_ as error:
            raise ServiceError("denied", str(error)) from None

    # --- ui --------------------------------------------------------------

    def _label(self, call: SkillCall) -> str:
        return call.skill.manifest.label(self._agent._state.response_language or "en")

    def _ui_notify(self, call: SkillCall, params: dict[str, Any]) -> None:
        text = redact_text(_str(params, "text", limit=300))
        self._agent._ui.update_live(user_message=f"{self._label(call)}: {text}", status="skill",
                                    step=getattr(self._agent, "_current_step", 0), progress=self._agent._state.progress,
                                    plan_step="skill")

    def _ui_message(self, call: SkillCall, params: dict[str, Any]) -> None:
        markdown = redact_text(_str(params, "markdown", limit=50_000))
        printer = getattr(self._agent._ui, "print_skill_message", None)
        if printer is not None:
            printer(call.skill.id, self._label(call), markdown)
        else:
            self._agent._ui.print_user_markdown(f"**{self._label(call)}:** {markdown}")

    def _ui_error(self, call: SkillCall, params: dict[str, Any]) -> None:
        self._agent._ui.print_error(f"{self._label(call)}: {redact_text(_str(params, 'text', limit=2000))}")

    def _ui_stream_start(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        stream_id = f"us_{uuid.uuid4().hex[:10]}"
        call.streams[stream_id] = {"text": ""}
        return {"stream_id": stream_id}

    def _ui_stream_append(self, call: SkillCall, params: dict[str, Any]) -> None:
        stream = call.streams.get(_str(params, "stream_id", limit=40))
        if stream is None:
            raise _bad("unknown stream")
        stream["text"] += _str(params, "text", limit=20_000)
        if len(stream["text"]) > 200_000:
            raise _bad("a streamed message is at most 200000 characters")
        self._agent._ui.update_live(user_message=f"{self._label(call)}: {stream['text'][-120:]}", status="skill",
                                    step=getattr(self._agent, "_current_step", 0),
                                    progress=self._agent._state.progress, plan_step="skill")

    def _ui_stream_end(self, call: SkillCall, params: dict[str, Any]) -> None:
        stream = call.streams.pop(_str(params, "stream_id", limit=40), None)
        if stream is None:
            raise _bad("unknown stream")
        if stream["text"].strip():
            self._ui_message(call, {"markdown": stream["text"]})

    def _interactive(self) -> bool:
        return bool(getattr(self._agent.config, "interactive", False))

    def _ui_choose(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        question = redact_text(_str(params, "question", limit=1000))
        options = _str_list(params, "options", limit=20)
        if not options:
            raise _bad("options must not be empty")
        if not self._interactive():
            return {"status": "no_interactive_user", "value": None}
        pairs = [(option, option) for option in options]
        if params.get("allow_skip"):
            pairs.append((_SKIP, "Пропустить" if self._ru() else "Skip"))
        answer = self._ask(lambda ui: ui.ask_choice(f"{self._label(call)}: {question}", pairs))
        if answer is None:
            return {"status": "cancelled", "value": None}
        if answer == _SKIP:
            return {"status": "skipped", "value": None}
        return {"status": "answered", "value": answer}

    def _ui_ask(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        question = redact_text(_str(params, "question", limit=1000))
        if not self._interactive():
            return {"status": "no_interactive_user", "value": None}
        default = _str(params, "default", required=False, limit=2000)
        answer = self._ask(lambda ui: ui.prompt_text(f"{self._label(call)}: {question}", current_value=default))
        if answer is None:
            return {"status": "cancelled", "value": None}
        if not answer.strip() and params.get("allow_skip"):
            return {"status": "skipped", "value": None}
        return {"status": "answered", "value": answer}

    def _ui_confirm(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        question = redact_text(_str(params, "question", limit=1000))
        details = redact_text(_str(params, "details", required=False, limit=8000))
        if not self._interactive():
            return {"status": "no_interactive_user", "approved": False}
        approved = self._ask(lambda ui: ui.confirm(f"{self._label(call)}: {question}", details))
        return {"status": "answered", "approved": bool(approved)}

    def _ask(self, ask: Callable[[Any], Any]) -> Any:
        control = self._agent.control
        control.awaiting_input(True)
        try:
            return ask(self._agent._ui)
        finally:
            control.awaiting_input(False)

    def _ui_present(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        caption = redact_text(_str(params, "caption", required=False, limit=500))
        if params.get("attachment_id"):
            attachment = self._attachment(call, _str(params, "attachment_id", limit=40))
            path, name, mime = self.attachments.original_path(attachment.id), attachment.name, attachment.mime
        elif params.get("artifact_id"):
            artifact = self.artifacts.get(_str(params, "artifact_id", limit=40))
            path, name, mime = Path(artifact.path), artifact.name, artifact.mime
        else:
            raise _bad("attachment_id or artifact_id is required")
        self._present(call, path, name, mime, caption)
        return {"shown": True, "path": str(path)}

    def _present(self, call: SkillCall, path: Path, name: str, mime: str, caption: str) -> None:
        payload = {"skill": call.skill.id, "name": name, "mime": mime, "path": str(path), "caption": caption}
        if mime.startswith("image/"):
            payload["preview"] = _preview_data_url(path)
        printer = getattr(self._agent._ui, "print_artifact", None)
        if printer is not None:
            printer(payload)
        else:
            self._agent._ui.print_result(f"{caption or name}: {path}")

    def _ru(self) -> bool:
        return self._agent._state.response_language == "ru"

    # --- sessions and memory ---------------------------------------------

    def _workspace(self) -> Path:
        return Path(self._agent.executor.workspace)

    def _sessions_list(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        return list_sessions(self._workspace())

    def _sessions_read(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return read_session(self._workspace(), _str(params, "session", limit=200),
                            before=_int(params, "before", None, 1, 10**9), limit=_int(params, "limit", 40, 1, 200) or 40)

    def _sessions_search(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return search_sessions(self._workspace(), _str(params, "query", limit=500),
                               limit=_int(params, "limit", 20, 1, 100) or 20)

    def _memory_search(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        kinds = tuple(_str_list(params, "kinds", required=False, limit=len(MEMORY_KINDS))) or MEMORY_KINDS
        return self.memory.search(_str(params, "query", required=False, limit=1000), kinds=kinds,
                                  include_inactive=bool(params.get("include_inactive")),
                                  limit=_int(params, "limit", 10, 1, 50) or 10)

    def _memory_get(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self.memory.get(_str(params, "id", limit=40))

    def _memory_explain(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self.memory.explain(_str(params, "id", limit=40))

    def _memory_list(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        return self.memory.search("", include_inactive=bool(params.get("include_inactive")),
                                  limit=_int(params, "limit", 50, 1, 200) or 50)

    def _memory_remember(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        # A skill never speaks for the person: its records are what it observed or inferred.
        basis = _str(params, "basis", required=False, limit=20) or "observed"
        if basis not in ("observed", "inferred"):
            raise ServiceError("denied", "a skill remembers what it observed or inferred; only the person confirms")
        return self.memory.remember(
            _str(params, "content", limit=4000), kind=_str(params, "kind", required=False, limit=20) or "project_fact",
            trust=basis, actor=f"skill:{call.skill.id}", topic=_str(params, "topic", required=False, limit=200),
            files=_str_list(params, "files", required=False),
            sources=[Source(origin=f"skill:{call.skill.id}", session=call.session or "", run_id=call.run_id or "")],
        )

    def _memory_update(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        files = _str_list(params, "files", required=False) if params.get("files") is not None else None
        expected_version = _int(params, "expected_version", None, 1, 10**9)
        if expected_version is None:
            raise _bad("expected_version is required: the version you read, so a concurrent change is not lost")
        return self.memory.update(
            _str(params, "id", limit=40), actor=f"skill:{call.skill.id}", expected_version=expected_version,
            reason=_str(params, "reason", limit=1000),
            content=params.get("content") if isinstance(params.get("content"), str) else None,
            topic=params.get("topic") if isinstance(params.get("topic"), str) else None,
            files=files, status=params.get("status") if isinstance(params.get("status"), str) else None,
        )

    def _memory_link(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self.memory.link(_str(params, "source_id", limit=40), _str(params, "target_id", limit=40),
                                _str(params, "relation", limit=40), actor=f"skill:{call.skill.id}",
                                reason=_str(params, "reason", required=False, limit=1000))

    def _memory_forget(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        # One wrong record is a skill's business; wiping memory is not, so a task may forget only a few.
        key = f"{call.skill.id}:{call.run_id}"
        if self._memory_forgets.get(key, 0) >= MAX_SKILL_FORGETS_PER_TASK:
            raise ServiceError("denied", f"a skill forgets at most {MAX_SKILL_FORGETS_PER_TASK} records per task; "
                               "the person can forget more with /memory forget")
        result = self.memory.forget(_str(params, "id", limit=40), actor=f"skill:{call.skill.id}")
        self._memory_forgets[key] = self._memory_forgets.get(key, 0) + 1
        return result

    # --- storage ---------------------------------------------------------

    def _storage(self, call: SkillCall) -> SkillStorage:
        return SkillStorage(self._workspace(), call.skill.id, call.skill.version)

    def _owner_key(self, call: SkillCall, scope: str) -> str:
        return {"call": call.call_id, "task": call.run_id, "session": call.session, "workspace": "workspace"}.get(scope, "")

    def _storage_get(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        scope = _str(params, "scope", required=False, limit=20) or "workspace"
        return self._storage(call).get(scope, self._owner_key(call, scope), _str(params, "key", limit=200))

    def _storage_set(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        scope = _str(params, "scope", required=False, limit=20) or "workspace"
        expected = _int(params, "expected_version", None, 0, 10**12)
        return self._storage(call).set(scope, self._owner_key(call, scope), _str(params, "key", limit=200),
                                       params.get("value"), expected,
                                       ttl_seconds=_int(params, "ttl_seconds", None, 1, MAX_TTL_SECONDS))

    def _storage_delete(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        scope = _str(params, "scope", required=False, limit=20) or "workspace"
        return {"deleted": self._storage(call).delete(scope, self._owner_key(call, scope), _str(params, "key", limit=200))}

    def _storage_keys(self, call: SkillCall, params: dict[str, Any]) -> list[str]:
        scope = _str(params, "scope", required=False, limit=20) or "workspace"
        return self._storage(call).keys(scope, self._owner_key(call, scope))

    # --- runtime and events ----------------------------------------------

    def _runtime_status(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        control = self._agent.control
        limit = min(call.tool.timeout_seconds, call.skill.manifest.limits["call_timeout_seconds"])
        return {"state": control.state, "paused": control.pause_requested(), "cancelled": control.cancel_requested(),
                "deadline_seconds": max(0.0, round(limit - (time.time() - call.started), 1))}

    def _runtime_check(self, call: SkillCall, params: dict[str, Any]) -> None:
        # The host already answers every request with paused/cancelled once the
        # task is interrupted; reaching here means the call may go on.
        return None

    def _runtime_wait(self, call: SkillCall, params: dict[str, Any]) -> None:
        seconds = params.get("seconds", 1)
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
            raise _bad("seconds must be a number")
        if self._agent.control.wait(min(max(float(seconds), 0.0), 5.0)):
            code = "paused" if self._agent.control.pause_requested() else "cancelled"
            raise ServiceError(code, f"the task was {code}")

    def _runtime_progress(self, call: SkillCall, params: dict[str, Any]) -> None:
        self._ui_notify(call, {"text": _str(params, "text", limit=300)})

    def _runtime_register_resource(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        label = redact_text(_str(params, "label", limit=120))
        kind = _str(params, "kind", required=False, limit=40) or "logical"
        details = params.get("details") if isinstance(params.get("details"), dict) else {}
        owner = Owner(session=call.session, run_id=call.run_id, kind="skill",
                      skill=f"{call.skill.id}@{call.skill.version}")
        resource = self._agent.resources.register("logical", f"{call.skill.id}: {label}", owner,
                                                  details={"kind": kind, **redact_value(details)})
        return {"resource_id": resource.id}

    def _runtime_release_resource(self, call: SkillCall, params: dict[str, Any]) -> None:
        resource_id = _str(params, "resource_id", limit=40)
        resource = self._agent.resources.get(resource_id)
        if resource is None or resource.owner.skill != f"{call.skill.id}@{call.skill.version}":
            raise ServiceError("denied", f"{resource_id} is not a resource of {call.skill.id}")
        self._agent.resources.stop(resource_id)

    def _events_poll(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        after = _int(params, "after", 0, 0, 10**12) or 0
        timeout = params.get("timeout", 0)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise _bad("timeout must be a number")
        return self._manager.poll_events(call.skill.id, after, min(max(float(timeout), 0.0), 5.0), self._agent.control)

    # --- helpers ---------------------------------------------------------

    def _helpers(self):
        helpers = getattr(self._agent, "helpers", None)
        if helpers is None:
            raise ServiceError("unavailable", "helpers are not available in this session")
        return helpers

    def _agents_start(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._helpers().start(
            _str(params, "task", limit=20000), role=_str(params, "role", required=False, limit=20) or None,
            context=_str(params, "context", required=False, limit=20000),
            attachments=_str_list(params, "attachments", required=False, limit=8), started_by=f"skill:{call.skill.id}")

    def _agents_message(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._helpers().message(_str(params, "agent_id", limit=40), _str(params, "text", limit=20000))

    def _agents_update(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._helpers().update(_str(params, "agent_id", limit=40), _str(params, "task", limit=20000))

    def _agents_status(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._helpers().status(_str(params, "agent_id", limit=40))

    def _agents_wait(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        timeout = params.get("timeout", 60)
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)):
            raise _bad("timeout must be a number")
        return self._helpers().wait(_str(params, "agent_id", limit=40), min(max(float(timeout), 0.0), 600.0))

    def _agents_inspect(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._helpers().inspect(_str(params, "agent_id", limit=40), _int(params, "cursor", 0, 0, 10**9) or 0,
                                       _int(params, "limit", 20, 1, 100) or 20)

    def _agents_result(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._helpers().result(_str(params, "agent_id", limit=40))

    def _agents_stop(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        return self._helpers().stop(_str(params, "agent_id", limit=40), by="agent")

    # --- environment, settings, permissions ------------------------------

    def _environment_info(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.plugins.manifest import PLUGIN_API_VERSION

        agent = self._agent
        state = agent._state
        return {
            "os": sys.platform,
            "os_detail": state.system_os,
            "shell": state.system_shell,
            "workspace": Path(agent.executor.workspace).name,
            "project": state.system_project,
            "role": state.current_agent_role,
            "read_only": bool(state.read_only_goal or state.current_agent_role == "reader"),
            "plan_phase": state.plan_phase,
            "plugin_api": PLUGIN_API_VERSION,
            "models": {"fast": agent.config.fast_model, "smart": agent.config.smart_model,
                       "provider": agent.config.provider},
            "frontend": {"interactive": self._interactive(),
                         "images": callable(getattr(agent._ui, "print_artifact", None))},
            "language": state.response_language or "en",
        }

    def _settings_get(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        schema = call.skill.manifest.settings or {"type": "object", "properties": {}}
        defaults = {name: spec.get("default") for name, spec in schema.get("properties", {}).items()
                    if "default" in spec}
        stored = self._storage(call).get("workspace", "workspace", "__settings__")["value"]
        values = {**defaults, **(stored if isinstance(stored, dict) else {})}
        return {"values": values, "schema": schema}

    def _permissions_current(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        granted = dict(call.skill.grant.capabilities) if call.skill.grant else {}
        return {"granted": granted, "for_this_task": dict(call.skill.task_grants),
                "denied_this_task": sorted(call.skill.denied_requests)}

    def _permissions_request(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        capability = _str(params, "capability", limit=60)
        if capability not in caps.CAPABILITIES:
            raise _bad(f"unknown capability {capability}")
        reason = redact_text(_str(params, "reason", limit=500))
        scope = params.get("scope") if isinstance(params.get("scope"), dict) else {}
        if set(scope) - caps.CAPABILITIES[capability].scope_keys:
            raise _bad(f"{capability} scope may only have {sorted(caps.CAPABILITIES[capability].scope_keys)}")
        key = f"{capability}:{json.dumps(scope, sort_keys=True)}"
        if call.skill.allows(capability) and not scope:
            return {"granted": True, "status": "already_granted"}
        if key in call.skill.denied_requests:
            return {"granted": False, "status": "denied_earlier"}
        if not self._interactive():
            return {"granted": False, "status": "no_interactive_user"}
        language = self._agent._state.response_language or "en"
        title = (f"{self._label(call)} просит доступ на эту задачу" if language == "ru"
                 else f"{self._label(call)} asks for access for this task")
        body = f"{caps.describe(capability, scope, language)}\n\n{reason}"
        approved = bool(self._ask(lambda ui: ui.confirm(title, body)))
        if not approved:
            call.skill.denied_requests.add(key)
            return {"granted": False, "status": "denied"}
        call.skill.task_grants[capability] = scope
        if call.skill.host is not None and capability in ("network.internet", "network.task_services") \
                and call.skill.host.broker is not None:
            policy = call.skill.host.broker.policy
            policy.allow_services = policy.allow_services or capability == "network.task_services"
            policy.domains = list(dict.fromkeys([*policy.domains, *scope.get("domains", [])]))
        return {"granted": True, "status": "granted"}

    # --- artifacts, log, display, network --------------------------------

    def _artifacts_save(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        kind = _str(params, "kind", required=False, limit=40) or "file"
        with self._from_exchange(call, _str(params, "path", limit=500)) as source:
            artifact = self.artifacts.save_file(
                source, name=_str(params, "name", required=False, limit=200) or source.name, kind=kind,
                caption=redact_text(_str(params, "caption", required=False, limit=500)),
                origin=self._origin(call),
            )
        return artifact.public()

    def _artifacts_list(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [item.public() for item in self.artifacts.list(call.run_id)
                if item.origin.get("skill") == call.skill.id]

    def _origin(self, call: SkillCall) -> dict[str, Any]:
        return {"skill": call.skill.id, "version": call.skill.version, "call_id": call.call_id,
                "run_id": call.run_id, "session": call.session}

    def _log_write(self, call: SkillCall, params: dict[str, Any]) -> None:
        level = _str(params, "level", required=False, limit=10) or "info"
        call.skill.log.append(f"{level}: {redact_text(_str(params, 'text', limit=4000))}")

    def _display_info(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        host = call.skill.host
        if host is None or host.screen is None:
            raise ServiceError("unavailable", "this skill has no virtual screen")
        return {"display": ":99", "size": self._agent_screen_size(), "viewer_url": host.screen.url,
                "state": host.screen.state}

    # --- host (the person's own desktop) ---------------------------------

    def _visualize_validate(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.skills_bundled.visualize.renderer import validate_page

        errors = validate_page(params.get("page"))
        return {"valid": not errors, "errors": errors}

    def _visualize_show(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.skills_bundled.visualize.renderer import render_page, validate_page

        page = params.get("page")
        errors = validate_page(page)
        if errors:
            return {"valid": False, "errors": errors, "opened": False}
        should_open = params.get("open_browser", True)
        if should_open and not call.skill.allows("host.open"):
            raise ServiceError("denied", "visualize was not granted host.open")
        if should_open:
            self._count_host_open(call)
        artifact = self.artifacts.save_bytes(
            render_page(page).encode("utf-8"), name="visualization.html", kind="visualization",
            caption=page["title"], origin=self._origin(call),
        )
        result = {"valid": True, "artifact": artifact.public(), "opened": False}
        if should_open:
            try:
                host_actions.open_visualization(Path(artifact.path))
                result["opened"] = True
            except ServiceError as error:
                result["open_error"] = error.message
        return result

    def _host_open_url(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        url = _str(params, "url", limit=2000)
        # An address is the one thing that leaves for a site the page owner reads, so a secret in it is refused.
        if redact_text(url) != url:
            raise ServiceError("denied", "the address contains what looks like a secret, so it is not opened")
        self._count_host_open(call)
        host_actions.open_url(url)
        self._ui_notify(call, {"text": f"{'открыто' if self._ru() else 'opened'} {url[:200]}"})
        return {"opened": url}

    def _host_open_app(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        self._count_host_open(call)
        name = host_actions.open_app(_str(params, "name", limit=200))
        self._ui_notify(call, {"text": f"{'запущено' if self._ru() else 'started'} {name}"})
        return {"started": name}

    def _count_host_open(self, call: SkillCall) -> None:
        opened = self._host_opens.get(call.run_id, 0) + 1
        if opened > MAX_HOST_OPENS_PER_RUN:
            raise ServiceError("denied", f"at most {MAX_HOST_OPENS_PER_RUN} addresses or applications are opened "
                               "in one task; ask the person before opening more")
        self._host_opens[call.run_id] = opened

    def _host_windows(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        return [{**item, "title": redact_text(str(item["title"]))} for item in host_actions.windows()]

    def _host_focus(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        host_actions.focus_window(_str(params, "window", limit=40))
        return {"focused": True}

    def _host_close(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        target = host_actions.window(_str(params, "window", limit=40))
        if not self._interactive():
            raise ServiceError("denied", "closing a window needs the person's answer and nobody is here to ask")
        title = redact_text(str(target["title"]))[:200]
        question = (f"Закрыть окно «{target['app']}: {title}»? Несохранённое может пропасть." if self._ru()
                    else f"Close the window “{target['app']}: {title}”? Unsaved work may be lost.")
        if not self._ask(lambda ui: ui.confirm(f"{self._label(call)}: {question}", "")):
            return {"closed": False, "reason": "the person declined"}
        host_actions.close_window(str(target["id"]))
        return {"closed": True}

    def _agent_screen_size(self) -> str:
        return getattr(self._manager, "screen_size", "1280x800")

    def _display_screenshot(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        from kernel.plugins.screen import FramebufferError, frame_png

        host = call.skill.host
        if host is None or host.screen is None:
            raise ServiceError("unavailable", "this skill has no virtual screen")
        try:
            data = frame_png(host.screen.fb_path)
        except FramebufferError as error:
            raise ServiceError("unavailable", str(error)) from None
        name = _str(params, "name", required=False, limit=100) or "screen.png"
        artifact = self.artifacts.save_bytes(data, name=name, kind="screenshot", origin=self._origin(call))
        attachment = self.attachments.add_bytes(
            data, name, session=call.session, max_bytes=20 * 1024 * 1024,
            origin={"kind": "browser_snapshot", **self._origin(call)},
        )
        return {"artifact": artifact.public(), "attachment_id": attachment.id, "width": attachment.width,
                "height": attachment.height}

    def _network_proxy(self, call: SkillCall, params: dict[str, Any]) -> dict[str, Any]:
        host = call.skill.host
        if host is None or host.broker is None:
            raise ServiceError("unavailable", "this skill has no network access")
        return {"url": "http://127.0.0.1:8899", "allowed_domains": list(host.broker.policy.domains),
                "task_services": host.broker.policy.allow_services}

    def _network_services(self, call: SkillCall, params: dict[str, Any]) -> list[dict[str, Any]]:
        if not call.skill.allows("network.task_services"):
            raise ServiceError("denied", f"{call.skill.id} may not reach the task's servers")
        return [{"port": service.port, "label": service.label, "url": f"http://localhost:{service.port}/"}
                for service in self._agent.executor.task_services.list()]

    # --- exchange directory ----------------------------------------------

    # The runner can write the exchange directory, so it may replace any entry
    # below the root with a symlink at any moment. Every operation the core does
    # there walks from a descriptor of the root with O_NOFOLLOW on each component
    # and never reuses a checked path, so a swap is refused instead of followed.

    def _exchange_root(self, call: SkillCall) -> Path:
        host = call.skill.host
        if host is None or host.exchange_dir is None:
            raise ServiceError("unavailable", "the skill has no exchange directory")
        return host.exchange_dir

    def _to_exchange(self, call: SkillCall, source: Path, name: str) -> str:
        root = self._exchange_root(call)
        target_name = f"{uuid.uuid4().hex[:8]}-{Path(name).name}"
        try:
            inbox = _open_inbox(root)
        except OSError:
            raise ServiceError("denied", f"/kvist/exchange/{_EXCHANGE_IN} is not a directory; the core writes only "
                               "into a real directory of the exchange") from None
        try:
            target = os.open(target_name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                             0o600, dir_fd=inbox)
        finally:
            os.close(inbox)
        with open(source, "rb") as reader, os.fdopen(target, "wb") as writer:
            shutil.copyfileobj(reader, writer)
        return f"/kvist/exchange/{_EXCHANGE_IN}/{target_name}"

    @contextmanager
    def _from_exchange(self, call: SkillCall, sandbox_path: str) -> Iterator[Path]:
        """A core-private copy of a file the skill wrote in the exchange, removed after use."""
        root = self._exchange_root(call)
        prefix = "/kvist/exchange/"
        relative = PurePosixPath(sandbox_path[len(prefix):] if sandbox_path.startswith(prefix) else sandbox_path)
        parts = relative.parts
        if not parts or relative.is_absolute() or any(part in (".", "..") for part in parts):
            raise ServiceError("denied", f"{sandbox_path} is not a file of the exchange directory")
        try:
            source = _open_exchange_file(root, parts)
        except FileNotFoundError:
            raise _bad(f"{sandbox_path} does not exist in the exchange directory") from None
        except OSError:
            raise ServiceError("denied", f"{sandbox_path} is not a file of the exchange directory") from None
        holder = root.parent / "received" / uuid.uuid4().hex
        with os.fdopen(source, "rb") as reader:
            holder.mkdir(parents=True, mode=0o700)
            try:
                copy = holder / parts[-1]
                _copy_regular_file(reader, copy, sandbox_path)
                yield copy
            finally:
                shutil.rmtree(holder)


_DIRECTORY_FLAGS = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_inbox(root: Path) -> int:
    """A descriptor of the exchange's ``in`` directory, made if missing; OSError when it is anything else."""
    parent = os.open(root, _DIRECTORY_FLAGS)
    try:
        try:
            os.mkdir(_EXCHANGE_IN, 0o700, dir_fd=parent)
        except FileExistsError:
            # An earlier call or the runner made it; the open below accepts only a real directory.
            pass
        return os.open(_EXCHANGE_IN, _DIRECTORY_FLAGS, dir_fd=parent)
    finally:
        os.close(parent)


def _open_exchange_file(root: Path, parts: tuple[str, ...]) -> int:
    """A read descriptor of ``root/parts``, reached without following any symlink; OSError otherwise."""
    directory = os.open(root, _DIRECTORY_FLAGS)
    try:
        for part in parts[:-1]:
            child = os.open(part, _DIRECTORY_FLAGS, dir_fd=directory)
            os.close(directory)
            directory = child
        # O_NONBLOCK: opening a FIFO left in the exchange must not wait for a writer.
        return os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, dir_fd=directory)
    finally:
        os.close(directory)


def _copy_regular_file(reader: BinaryIO, target: Path, label: str) -> None:
    """Copy an open regular file of at most the exchange limit to a new ``target``; ServiceError otherwise."""
    if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
        raise _bad(f"{label} is not a regular file")
    copied = 0
    with open(target, "xb") as writer:
        while chunk := reader.read(1024 * 1024):
            copied += len(chunk)
            if copied > _EXCHANGE_OUT_LIMIT:
                raise _bad(f"{label} is larger than {_EXCHANGE_OUT_LIMIT // (1024 * 1024)} MiB")
            writer.write(chunk)


def _preview_data_url(path: Path, max_side: int = 640) -> str:
    import io

    from PIL import Image, UnidentifiedImageError

    try:
        with Image.open(path) as image:
            image = image.convert("RGB")
            image.thumbnail((max_side, max_side))
            output = io.BytesIO()
            image.save(output, format="JPEG", quality=75)
    except (OSError, UnidentifiedImageError):
        return ""
    return "data:image/jpeg;base64," + base64.b64encode(output.getvalue()).decode("ascii")


def _parse_json_value(text: str) -> tuple[Any, str | None]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("\n", 1)[1] if "\n" in stripped else ""
        stripped = stripped.rsplit("```", 1)[0]
    try:
        return json.loads(stripped), None
    except json.JSONDecodeError as error:
        return None, f"not valid JSON ({error.msg})"


def _limit_kwargs(model: str, max_tokens: int) -> dict[str, int]:
    name = model.lower().split("/")[-1]
    key = "max_completion_tokens" if name.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")) else "max_tokens"
    return {key: max_tokens}


def _request_kwargs(agent: Any, model: str, messages: list[dict[str, Any]], max_tokens: int, stream: bool) -> dict[str, Any]:
    from kernel.llm.reasoning_effort import merge_reasoning_kwargs

    kwargs: dict[str, Any] = {"model": model, "messages": messages, "timeout": agent.config.api_timeout,
                              **_limit_kwargs(model, max_tokens)}
    if stream:
        kwargs["stream"] = True
    merge_reasoning_kwargs(kwargs, agent.config.provider, agent.config.reasoning_effort)
    return kwargs


def _complete(agent: Any, model: str, messages: list[dict[str, Any]], max_tokens: int, *, owner: str) -> tuple[str, dict[str, int]]:
    from kernel.agent.agent_model import (
        _create_completion_with_reasoning_fallback,
        _emit_usage,
        _should_retry_model_error,
        _throttle_request_rate,
        safe_extract_completion_content,
    )

    attempts = max(1, int(agent.config.max_retries) + 1)
    started = time.monotonic()
    for attempt in range(attempts):
        if agent.control.interrupted():
            raise ServiceError("paused" if agent.control.pause_requested() else "cancelled", "the task was stopped")
        _throttle_request_rate(agent)
        try:
            reply = _create_completion_with_reasoning_fallback(agent, _request_kwargs(agent, model, messages, max_tokens, False))
            break
        except Exception as error:  # noqa: BLE001 - provider SDKs raise many types; classified below
            if attempt + 1 >= attempts or not _should_retry_model_error(error):
                raise ServiceError("model_error", redact_text(f"{type(error).__name__}: {error}")[:500]) from None
            time.sleep(agent.config.retry_base_delay * (2 ** attempt))
    text = safe_extract_completion_content(reply) or ""
    usage = getattr(reply, "usage", None)
    input_tokens = int(getattr(usage, "prompt_tokens", 0) or 0) or sum(agent._count_tokens(m["content"]) for m in messages)
    output_tokens = int(getattr(usage, "completion_tokens", 0) or 0) or agent._count_tokens(text)
    _emit_usage(agent, model=model, input_tokens=input_tokens, output_tokens=output_tokens,
                kind=f"skill:{owner}", duration_seconds=time.monotonic() - started)
    return text, {"input_tokens": input_tokens, "output_tokens": output_tokens}


def _stream(agent: Any, model: str, messages: list[dict[str, Any]], max_tokens: int, *, owner: str):
    from kernel.agent.agent_model import (
        _create_completion_with_reasoning_fallback,
        _emit_usage,
        _throttle_request_rate,
        safe_extract_stream_delta,
    )

    started = time.monotonic()
    _throttle_request_rate(agent)
    try:
        stream = _create_completion_with_reasoning_fallback(agent, _request_kwargs(agent, model, messages, max_tokens, True))
    except Exception as error:  # noqa: BLE001 - provider SDKs raise many types
        raise ServiceError("model_error", redact_text(f"{type(error).__name__}: {error}")[:500]) from None
    produced: list[str] = []
    for chunk in stream:
        if agent.control.interrupted():
            close = getattr(stream, "close", None)
            if callable(close):
                close()
            break
        delta = safe_extract_stream_delta(chunk)
        text = getattr(delta, "content", None) if delta is not None else None
        if text:
            produced.append(text)
            yield text
    output = "".join(produced)
    _emit_usage(agent, model=model, input_tokens=sum(agent._count_tokens(m["content"]) for m in messages),
                output_tokens=agent._count_tokens(output), kind=f"skill:{owner}",
                duration_seconds=time.monotonic() - started)
