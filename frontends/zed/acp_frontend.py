# -*- coding: utf-8 -*-
"""ACP (Agent Client Protocol) frontend for Kvist: the ``kvist --acp`` server.

Zed hosts coding agents as **ACP External Agents** — a program speaking
JSON-RPC 2.0 on stdio, registered under ``agent_servers`` in Zed's
``settings.json``. This module is that program. It is a transport, exactly
like ``event_stdio.py``: it owns the wire and delegates every decision to the
existing ``LocalAgent``. There is no second agent loop and no copy of business
logic.

Wire contract: stdout carries JSON-RPC only. Every diagnostic goes to stderr,
and ``build_agent``'s own prints are redirected there for the whole session so
a stray ``print`` cannot corrupt the stream.

Each ACP session is one Kvist named session, so a thread can be listed and
reopened after a restart; :func:`acp_session_id` derives the opaque id.

Secrets never travel over ACP: form elicitation is forbidden for credentials
by the protocol, so a missing key is answered with terminal setup
(``kvist --acp --login``) or a clear error — never a form field.
"""
from __future__ import annotations

import asyncio
import hashlib
import os
import queue
import sys
import threading
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from acp import helpers, schema
from acp.core import DEFAULT_STDIO_BUFFER_LIMIT_BYTES
from acp.core import run_agent as _acp_run_agent
from acp.exceptions import RequestError
from acp.interfaces import Agent, Client
from acp.utils import normalize_result

from acp_events import AcpEventTranslator
from acp_stdio import stdio_streams

# Stable protocol version this agent implements. ``InitializeRequest`` may
# carry a date string (Zed sends one); the SDK coerces it to an int before it
# reaches ``initialize``.
ACP_PROTOCOL_VERSION = 1

AGENT_NAME = "kvist"

# ``session/set_config_option`` ids. Stable wire identifiers, so they stay
# English even though the labels are localized.
CONFIG_ROLE = "role"
CONFIG_EFFORT = "effort"
CONFIG_VERBOSE = "verbose"
CONFIG_STYLE_AGENTS = "style_agents"
CONFIG_STYLE_CHAT = "style_chat"
CONFIG_ADVANCED = "advanced"

ROLE_CHOICES = ("auto", "coder", "reader", "devops", "chat", "universal")
EFFORT_CHOICES = ("false", "low", "medium", "high", "xhigh", "max")

# Advanced settings menu options (open terminal dialogs)
ADVANCED_MODELS = "models"
ADVANCED_PROVIDER = "provider"
ADVANCED_API_SETTINGS = "api-settings"
ADVANCED_CUSTOM_PROVIDER = "custom-provider"
ADVANCED_CHOICES = (
    ADVANCED_MODELS,
    ADVANCED_PROVIDER,
    ADVANCED_API_SETTINGS,
    ADVANCED_CUSTOM_PROVIDER,
)

# Extra directories beyond the session cwd that read-only actions may enter.
_MAX_ADDITIONAL_DIRS = 8

# The command a user runs to configure provider credentials without a TUI.
LOGIN_COMMAND = "kvist --acp --login"

# Stable v1 methods the SDK's router does not serve as-is (see
# :func:`_install_extra_routes`); the wire names are kept in one place.
_METHOD_SESSION_DELETE = "session/delete"
_METHOD_SESSION_CLOSE = "session/close"
_METHOD_SESSION_RESUME = "session/resume"


# --- Session identity --------------------------------------------------------


def acp_session_id(workspace: Path | str, name: str) -> str:
    """Opaque, restart-stable ACP id for one Kvist session.

    Derived from the normalized workspace path and the Kvist session name, so
    an id survives a process restart without a side registry and two workspaces
    can never produce the same id for different conversations. The client
    treats the whole string as opaque; neither the workspace nor the name is
    recoverable from it.

    :param workspace: absolute session working directory.
    :param name: Kvist session name inside that workspace.
    :returns: ``k1_`` + 40 lowercase hex characters.
    """
    root = str(Path(workspace).expanduser().resolve())
    digest = hashlib.sha256()
    digest.update(root.encode("utf-8"))
    digest.update(b"\x00")
    digest.update(name.encode("utf-8"))
    return "k1_" + digest.hexdigest()[:40]


def resolve_session_name(workspace: Path | str, session_id: str) -> str | None:
    """Find the Kvist session name an ACP id names inside ``workspace``.

    Walks that workspace's session list and recomputes each candidate id, so a
    client cannot address a session it was not given and a crafted id cannot
    escape into another workspace.

    :param workspace: absolute session working directory.
    :param session_id: the opaque ACP session id.
    :returns: the Kvist session name, or None when the id names nothing here.
    """
    from kernel.session_store import SessionStore

    try:
        names = SessionStore(workspace).listing().get("sessions") or []
    except Exception:
        return None
    for name in names:
        if isinstance(name, str) and acp_session_id(workspace, name) == session_id:
            return name
    return None


# --- Working directory validation -------------------------------------------


def normalize_cwd(raw: str) -> Path:
    """Validate an ACP ``cwd`` into an absolute existing directory.

    ACP supplies the working directory; ``kvist --acp`` never invents one and
    never creates a directory the client named. A relative, missing, or
    non-directory path is an error the client can show the user.

    :param raw: the client's ``cwd`` string.
    :returns: the resolved absolute directory.
    :raises ValueError: when the path is not an absolute existing directory.
    """
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("cwd is required")
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise ValueError(f"cwd must be absolute: {raw!r}")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise ValueError(f"cwd is not accessible: {raw!r}") from error
    if not resolved.is_dir():
        raise ValueError(f"cwd is not a directory: {raw!r}")
    return resolved


def normalize_additional_directories(
    values: list[str] | None, workspace: Path
) -> tuple[Path, ...]:
    """Validate extra read-only roots; anything invalid is rejected loudly.

    ``SafeExecutor(read_roots=...)`` already confines these to read-only
    access, so accepting them is safe once each one is an absolute existing
    directory. An invalid entry raises rather than being skipped — silently
    narrowing a workspace would make a later read fail in a way the user
    cannot explain.

    :param values: additional directory paths from the client.
    :param workspace: the session's own working directory.
    :returns: the validated extra roots, workspace itself excluded.
    :raises ValueError: when an entry is not an absolute existing directory.
    """
    if not values:
        return ()
    if len(values) > _MAX_ADDITIONAL_DIRS:
        raise ValueError(f"at most {_MAX_ADDITIONAL_DIRS} additional directories")
    roots: list[Path] = []
    for raw in values:
        root = normalize_cwd(raw)
        if root == workspace:
            continue
        roots.append(root)
    return tuple(roots)


# --- Interactive requests ----------------------------------------------------


@dataclass(slots=True)
class _PendingInput:
    """An input request the agent is blocked on, mirrored to the ACP client."""

    request_id: str
    kind: str
    title: str
    body: str
    options: list[dict] | None
    subject: dict | None
    remember_pattern: str | None


class AcpInputBridge:
    """Answer Kvist input requests through ACP permission / elicitation.

    Kvist blocks the agent worker thread on ``FrontendChannel.request``; this
    bridge runs on the asyncio side of the same channel, so it can await the
    client and then release the waiter. Every path releases the waiter — an
    unsupported interaction refuses instead of hanging the agent.

    Secrets are never requested through form elicitation: the protocol forbids
    it, so a ``secret`` request is refused.
    """

    def __init__(
        self,
        client: Client | None,
        session_id: str,
        client_capabilities: schema.ClientCapabilities | None,
    ) -> None:
        self._client = client
        self._session_id = session_id
        self._capabilities = client_capabilities

    @property
    def supports_form(self) -> bool:
        """Whether the client advertised form elicitation."""
        elicitation = getattr(self._capabilities, "elicitation", None)
        return getattr(elicitation, "form", None) is not None

    def describe(self, event: Any) -> _PendingInput:
        """Copy an input request event into the bridge's own record."""
        options = getattr(event, "options", None)
        subject = getattr(event, "subject", None)
        return _PendingInput(
            request_id=str(event.request_id),
            kind=str(event.kind),
            title=str(event.title),
            body=str(getattr(event, "body", "") or ""),
            options=list(options) if isinstance(options, list) else None,
            subject=dict(subject) if isinstance(subject, dict) else None,
            remember_pattern=getattr(event, "remember_pattern", None),
        )

    async def handle(self, pending: _PendingInput, respond: Callable[..., None]) -> None:
        """Answer one input request and release the agent's waiter."""
        if pending.kind == "confirm":
            await self._handle_confirm(pending, respond)
            return
        if pending.kind == "secret":
            respond(value=None, approved=False)
            return
        if pending.kind in ("choice", "text") and self.supports_form:
            await self._handle_form(pending, respond)
            return
        if pending.kind == "choice" and len(pending.options or []) == 2:
            await self._handle_two_way_choice(pending, respond)
            return
        respond(value=None, approved=False)

    async def _handle_two_way_choice(
        self, pending: _PendingInput, respond: Callable[..., None]
    ) -> None:
        """Ask a two-option question as permission buttons.

        A client with no form elicitation can still show permission options, and
        the questions with exactly two answers ("plan first or start right
        away", "accept the plan or not", "keep going or finish") read as one
        affirmative and one negative button. The first option is the affirmative.
        """
        client = self._client
        options = [item for item in (pending.options or []) if isinstance(item, dict)]
        if client is None or len(options) != 2:
            respond(value=None, approved=False)
            return
        values = [str(item.get("value", "")) for item in options]
        permission_options = [
            schema.PermissionOption(
                option_id=values[0],
                name=str(options[0].get("label") or values[0]),
                kind="allow_once",
            ),
            schema.PermissionOption(
                option_id=values[1],
                name=str(options[1].get("label") or values[1]),
                kind="reject_once",
            ),
        ]
        try:
            response = await client.request_permission(
                self._session_id, self._tool_call_for(pending), permission_options
            )
        except Exception:
            respond(value=None, approved=False)
            return
        outcome = getattr(response, "outcome", None)
        if not isinstance(outcome, schema.AllowedOutcome) or outcome.option_id not in values:
            respond(value=None, approved=False)
            return
        respond(value=outcome.option_id, approved=True)

    async def _handle_confirm(
        self, pending: _PendingInput, respond: Callable[..., None]
    ) -> None:
        client = self._client
        if client is None:
            respond(value=None, approved=False)
            return
        options = self._permission_options(pending)
        try:
            response = await client.request_permission(
                self._session_id, self._tool_call_for(pending), options
            )
        except Exception:
            respond(value=None, approved=False)
            return
        outcome = getattr(response, "outcome", None)
        if not isinstance(outcome, schema.AllowedOutcome):
            respond(value=None, approved=False)
            return
        chosen = next(
            (item for item in options if item.option_id == outcome.option_id), None
        )
        if chosen is None:
            respond(value=None, approved=False)
            return
        approved = chosen.kind in ("allow_once", "allow_always")
        # ``remember`` only when Kvist handed us a saveable pattern. An
        # intrinsically dangerous command carries none (see
        # ``EventApprovalGate.confirm_command``), so "always" is never offered
        # for one and can never be persisted for one.
        remember = chosen.kind == "allow_always" and bool(pending.remember_pattern)
        respond(value=None, approved=approved, remember=remember)

    def _permission_options(
        self, pending: _PendingInput
    ) -> list[schema.PermissionOption]:
        options = [
            schema.PermissionOption(
                option_id="allow-once", name="Allow once", kind="allow_once"
            ),
            schema.PermissionOption(
                option_id="reject-once", name="Reject", kind="reject_once"
            ),
        ]
        if pending.remember_pattern:
            options.insert(
                1,
                schema.PermissionOption(
                    option_id="allow-always", name="Allow always", kind="allow_always"
                ),
            )
        return options

    def _tool_call_for(self, pending: _PendingInput) -> schema.ToolCallUpdate:
        subject = pending.subject or {}
        title = pending.title
        raw_input: Any = None
        kind = "other"
        if subject.get("kind") == "command":
            command = str(subject.get("command", "") or "")
            reason = str(subject.get("reason", "") or "")
            title = command or title
            kind = "execute"
            raw_input = {"command": command, "reason": reason}
        elif subject.get("kind") == "overwrite":
            title = f"Overwrite {subject.get('path', '')}"
            kind = "edit"
            raw_input = {"path": subject.get("path"), "diff": subject.get("diff")}
        return schema.ToolCallUpdate(
            tool_call_id=f"perm-{pending.request_id}",
            title=title,
            kind=kind,  # type: ignore[arg-type]
            status="pending",  # type: ignore[arg-type]
            raw_input=raw_input,
        )

    async def _handle_form(
        self, pending: _PendingInput, respond: Callable[..., None]
    ) -> None:
        client = self._client
        if client is None:
            respond(value=None, approved=False)
            return
        field_name = "value"
        requested = self._form_schema(pending, field_name)
        message = pending.title
        if pending.body:
            message = f"{pending.title}\n\n{pending.body}"
        try:
            result = await client.create_elicitation(
                message,
                mode=schema.ElicitationFormSessionMode(
                    session_id=self._session_id,
                    tool_call_id=f"elic-{pending.request_id}",
                    requested_schema=requested,
                ),
            )
        except Exception:
            respond(value=None, approved=False)
            return
        if getattr(result, "action", None) != "accept":
            respond(value=None, approved=False)
            return
        content = getattr(result, "content", None) or {}
        value = content.get(field_name)
        respond(value=None if value is None else str(value), approved=True)

    def _form_schema(
        self, pending: _PendingInput, field_name: str
    ) -> schema.ElicitationSchema:
        if pending.kind == "choice":
            entries = [
                option
                for option in (pending.options or [])
                if isinstance(option, dict) and option.get("value") is not None
            ]
            names = [str(option["value"]) for option in entries]
            default = names[0] if names else None
            # `enum` shows raw values; `one_of` carries a title per value, which a
            # question like "Plan first (safer)" needs to be readable.
            titled = [
                schema.EnumOption(
                    const=str(option["value"]),
                    title=str(option.get("label") or option["value"]),
                )
                for option in entries
            ]
            return schema.ElicitationSchema(
                type="object",
                properties={
                    field_name: schema.ElicitationStringPropertySchema(
                        type="string",
                        title=pending.title,
                        one_of=titled or None,
                        default=default,
                    )
                },
                required=[field_name],
            )
        return schema.ElicitationSchema(
            type="object",
            properties={
                field_name: schema.ElicitationStringPropertySchema(
                    type="string", title=pending.title
                )
            },
            required=[field_name],
        )


# --- Per-session runtime -----------------------------------------------------


class _EventBridge:
    """Thread-safe handoff from the Kvist worker thread to the asyncio loop.

    One per ACP session: two sessions running at once must never interleave
    their events.
    """

    def __init__(self) -> None:
        self._queue: queue.Queue = queue.Queue()

    def push(self, item: Any) -> None:
        self._queue.put(item)

    async def pop(self) -> Any:
        while True:
            try:
                return self._queue.get_nowait()
            except queue.Empty:
                pass
            await asyncio.sleep(0.005)


@dataclass(slots=True)
class KvistAcpSession:
    """One ACP session: its Kvist agent, event stream, and translator."""

    session_id: str
    name: str
    workspace: Path
    session: Any
    ui: Any
    translator: AcpEventTranslator
    bridge: _EventBridge = field(default_factory=_EventBridge)
    config_options: list[Any] = field(default_factory=list)
    closed: bool = False

    @property
    def agent(self):
        """The wrapped LocalAgent."""
        return self.session.agent


def _pump_events(session: KvistAcpSession, start: Callable[[], None]) -> None:
    """Forward one session's Kvist events onto its bridge until the run ends.

    ``start`` only submits the work; the events themselves come from
    ``EventSession.events()``, whose end of stream is "worker finished and the
    channel is drained" — the same turn boundary ``event_stdio`` uses. Input
    requests are tagged so the asyncio side can answer them over ACP rather
    than translating them into a session update.
    """
    try:
        start()
        for event in session.session.events():
            if getattr(event, "type", "") == "input_request":
                session.bridge.push(("input", event))
                continue
            session.bridge.push(event)
    except Exception as error:
        session.bridge.push(_error_event(str(error)))
    finally:
        session.bridge.push(None)


class KvistAcpAgent:
    """ACP agent implementation backed by Kvist's ``LocalAgent``.

    Structural ``acp.Agent``: the SDK's router calls these methods by name. One
    instance serves the whole process; each ACP session owns its own
    ``EventSession`` so two sessions can never share events, cwd, approvals or
    cancellation.
    """

    def __init__(self, *, build_agent_fn: Callable | None = None) -> None:
        self._build_agent_fn = build_agent_fn
        self._client: Client | None = None
        self._capabilities: schema.ClientCapabilities | None = None
        self._sessions: dict[str, KvistAcpSession] = {}
        # Workspaces this connection has been told about. `session/delete`
        # carries only a session id, so the workspace has to be found among
        # the ones the client named earlier in this process.
        self._workspaces: dict[str, Path] = {}
        self._lock = threading.Lock()

    # -- connection --------------------------------------------------------

    def on_connect(self, conn: Client) -> None:
        self._client = conn

    # -- initialize --------------------------------------------------------

    async def initialize(
        self,
        protocol_version: int,
        client_capabilities: schema.ClientCapabilities | None = None,
        client_info: schema.Implementation | None = None,
        **kwargs: Any,
    ) -> schema.InitializeResponse:
        _ = (client_info, kwargs)
        self._capabilities = client_capabilities
        offered = (
            protocol_version
            if isinstance(protocol_version, int)
            else ACP_PROTOCOL_VERSION
        )
        negotiated = ACP_PROTOCOL_VERSION if offered >= ACP_PROTOCOL_VERSION else offered
        return schema.InitializeResponse(
            protocol_version=negotiated,
            agent_info=schema.Implementation(name=AGENT_NAME, version=_kvist_version()),
            agent_capabilities=self._agent_capabilities(),
            auth_methods=[
                schema.TerminalAuthMethod(
                    id="terminal",
                    name="Set up Kvist in a terminal",
                    description=(
                        "Runs `kvist --acp --login` in a terminal so an API key can "
                        "be entered without sending it over this connection."
                    ),
                    args=["--login"],
                    type="terminal",
                )
            ],
        )

    def _agent_capabilities(self) -> schema.AgentCapabilities:
        """Only what is actually implemented end to end.

        No NES, no MCP, no providers, no image or audio prompts: those paths do
        not work, so they are not advertised.
        """
        return schema.AgentCapabilities(
            load_session=True,
            prompt_capabilities=schema.PromptCapabilities(
                image=False, audio=False, embedded_context=True
            ),
            session_capabilities=schema.SessionCapabilities(
                list=schema.SessionListCapabilities(),
                delete=schema.SessionDeleteCapabilities(),
                close=schema.SessionCloseCapabilities(),
                resume=schema.SessionResumeCapabilities(),
                additional_directories=schema.SessionAdditionalDirectoriesCapabilities(),
            ),
            auth=schema.AgentAuthCapabilities(),
        )

    # -- authentication ----------------------------------------------------

    async def authenticate(
        self, method_id: str, **kwargs: Any
    ) -> schema.AuthenticateResponse | None:
        _ = kwargs
        if method_id != "terminal":
            raise RequestError.invalid_params(
                {"method_id": method_id, "reason": "unknown auth method"}
            )
        # The terminal method is how Zed runs setup; the key never crosses this
        # connection. An empty success keeps the client usable — the next
        # prompt reports the missing key and names the command to run.
        return schema.AuthenticateResponse()

    # -- session lifecycle -------------------------------------------------

    async def new_session(
        self,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> schema.NewSessionResponse:
        _ = (mcp_servers, kwargs)
        self._require_provider()
        session = self._open_session(cwd, additional_directories, session_id=None)
        return schema.NewSessionResponse(
            session_id=session.session_id,
            config_options=session.config_options,
            modes=self._mode_state(session),
        )

    async def load_session(
        self,
        cwd: str,
        session_id: str,
        mcp_servers: list[Any] | None = None,
        additional_directories: list[str] | None = None,
        **kwargs: Any,
    ) -> schema.LoadSessionResponse | None:
        _ = (mcp_servers, kwargs)
        session = self._open_session(cwd, additional_directories, session_id=session_id)
        await self._replay_history(session)
        return schema.LoadSessionResponse(
            config_options=session.config_options,
            modes=self._mode_state(session),
        )

    async def list_sessions(
        self, cwd: str | None = None, cursor: str | None = None, **kwargs: Any
    ) -> schema.ListSessionsResponse:
        _ = (kwargs, cursor)
        if not cwd:
            return schema.ListSessionsResponse(sessions=[])
        workspace = normalize_cwd(cwd)
        self._remember_workspace(workspace)
        from kernel.session_store import SessionStore

        items = SessionStore(workspace).listing().get("session_items") or []
        sessions: list[schema.SessionInfo] = []
        for item in items:
            name = str(item.get("name", "") or "")
            if not name:
                continue
            updated = item.get("updated_at")
            title = item.get("title")
            sessions.append(
                schema.SessionInfo(
                    session_id=acp_session_id(workspace, name),
                    cwd=str(workspace),
                    title=str(title) if title else None,
                    updated_at=str(updated) if updated else None,
                )
            )
        return schema.ListSessionsResponse(sessions=sessions)

    async def delete_session(
        self, session_id: str, **kwargs: Any
    ) -> schema.DeleteSessionResponse:
        _ = kwargs
        workspace = self._known_workspace(session_id)
        if workspace is None:
            raise RequestError.resource_not_found(session_id)
        name = resolve_session_name(workspace, session_id)
        if name is None:
            raise RequestError.resource_not_found(session_id)
        from kernel.session_store import SessionStore

        # Stop using it before removing it, so no turn is mid-run on a session
        # that is about to disappear.
        self._forget(session_id)
        store = SessionStore(workspace)
        if name == getattr(store, "current_session", None):
            # Kvist refuses to delete the workspace's active session so the
            # active marker can never point at a deleted one. The client named
            # this thread and it is no longer running, so move the marker to a
            # fresh session first and then delete the named one. Leaving an
            # empty auto-named session drops it, which is the end state the
            # client asked for either way.
            store.new()
        if resolve_session_name(workspace, session_id) is not None:
            store.delete(name)
        return schema.DeleteSessionResponse()

    async def close_session(
        self, session_id: str, **kwargs: Any
    ) -> schema.CloseSessionResponse | None:
        _ = kwargs
        self._forget(session_id)
        return schema.CloseSessionResponse()

    async def resume_session(
        self,
        session_id: str,
        cwd: str,
        additional_directories: list[str] | None = None,
        mcp_servers: list[Any] | None = None,
        **kwargs: Any,
    ) -> schema.ResumeSessionResponse:
        _ = (mcp_servers, kwargs)
        session = self._open_session(cwd, additional_directories, session_id=session_id)
        return schema.ResumeSessionResponse(
            config_options=session.config_options,
            modes=self._mode_state(session),
        )

    async def cancel(self, session_id: str, **kwargs: Any) -> None:
        _ = kwargs
        session = self._sessions.get(session_id)
        if session is None:
            return
        session.translator.note_user_cancel()
        session.session.cancel()

    # -- prompt ------------------------------------------------------------

    async def prompt(
        self, session_id: str, prompt: list[Any], **kwargs: Any
    ) -> schema.PromptResponse:
        _ = kwargs
        session = self._require(session_id)
        if session.session.is_running():
            raise RequestError.invalid_request(
                {"reason": "a turn is already running on this session"}
            )
        text, attachments = extract_prompt(prompt, session.workspace)
        session.translator.workspace_root = str(session.workspace)

        command = parse_slash_command(text)
        if command is not None:
            await self._run_slash_command(session, command)
        else:
            goal = text if not attachments else f"{text}\n\n{attachments}"
            await self._run_events(session, lambda: session.session.submit(goal))
        return schema.PromptResponse(
            stop_reason=session.translator.stop_reason(),  # type: ignore[arg-type]
            usage=self._usage_model(session),
        )

    async def _run_events(
        self, session: KvistAcpSession, start: Callable[[], None]
    ) -> None:
        """Drive one run: forward events, answer input requests, end on close."""
        session.translator.begin_turn()
        worker = threading.Thread(
            target=_pump_events, args=(session, start), daemon=True
        )
        worker.start()
        try:
            while True:
                item = await session.bridge.pop()
                if item is None:
                    break
                if isinstance(item, tuple) and item and item[0] == "input":
                    await self._answer_input(session, item[1])
                    continue
                await self._send(session, item)
        finally:
            session.translator.end_turn()

    async def _send(self, session: KvistAcpSession, event: Any) -> None:
        client = self._client
        if client is None:
            return
        for update in session.translator.translate(event):
            try:
                await client.session_update(session.session_id, update.update)
            except Exception:
                # A dropped notification must not end the turn: the turn's
                # result still arrives in the prompt response.
                pass

    async def _answer_input(self, session: KvistAcpSession, event: Any) -> None:
        bridge = AcpInputBridge(self._client, session.session_id, self._capabilities)
        pending = bridge.describe(event)

        def respond(
            *,
            value: str | None = None,
            approved: bool | None = None,
            remember: bool = False,
        ) -> None:
            session.session.respond(
                pending.request_id, value=value, approved=approved, remember=remember
            )

        await bridge.handle(pending, respond)

    async def _run_slash_command(
        self, session: KvistAcpSession, command: tuple[str, list[str]]
    ) -> None:
        name, args = command
        if name == _COMMAND_TERMINAL:
            await self._run_terminal_command(session)
            return
        handler = _stdio_commands().get(name)
        if handler is None:
            session.translator.begin_turn()
            await self._send(session, _error_event(f"Unknown command: {name}"))
            session.translator.end_turn()
            return
        from kernel.event_stdio import CommandContext, CommandResult, run_command_handler

        context = CommandContext(
            session=session.session,
            ui=session.ui,
            rebuild=None,
            routed_role=None,
            args=args,
        )
        await self._run_events(
            session,
            lambda: session.session.submit_call(
                lambda: run_command_handler(handler, context)
            ),
        )
        result = session.session.result
        if isinstance(result, CommandResult) and result.message:
            await self._send(session, _message_event(result.message))

    async def _run_terminal_command(self, session: KvistAcpSession) -> None:
        """Ask the client for a terminal running the Kvist CLI.

        Creating a terminal is a client capability, so this only works when the
        client advertised one. Without it the command reports the launch line
        instead of pretending a terminal opened.
        """
        client = self._client
        can_open = bool(getattr(self._capabilities, "terminal", False))
        message: str
        if client is None or not can_open:
            command, args = terminal_launch_argv()
            line = " ".join([command, *args])
            message = (
                "This client cannot open a terminal for the agent. "
                f"Run it yourself: {line}"
            )
        else:
            command, args = terminal_launch_argv()
            try:
                created = await client.create_terminal(
                    session.session_id,
                    command,
                    args,
                    cwd=str(session.workspace),
                )
            except Exception as error:
                message = f"Could not open a terminal: {error}"
            else:
                message = f"Terminal opened (id {created.terminal_id})."
        session.translator.begin_turn()
        await self._send(session, _message_event(message))
        session.translator.end_turn()

    async def _replay_history(self, session: KvistAcpSession) -> None:
        """Replay a stored session onto the client as session updates.

        Journal events are already the display protocol, so they map one to
        one. A streamed answer is journaled once as its final snapshot, so the
        translator's snapshot buffer sees a single body and emits it exactly
        once — never as both a stream and a commit.
        """
        client = self._client
        if client is None:
            return
        from kernel.session_store import SessionStore

        try:
            page = SessionStore(session.workspace).history(session.name, limit=500)
        except Exception:
            return
        translator = AcpEventTranslator()
        translator.workspace_root = str(session.workspace)
        translator.begin_turn()
        for entry in page.get("events") or []:
            payload = entry.get("event") if isinstance(entry, dict) else None
            if not isinstance(payload, dict):
                continue
            for update in translator.translate(_dict_event(payload)):
                try:
                    await client.session_update(session.session_id, update.update)
                except Exception:
                    pass
        translator.end_turn()

    # -- configuration -----------------------------------------------------

    async def set_config_option(
        self, config_id: str, session_id: str, value: str | bool, **kwargs: Any
    ) -> schema.SetSessionConfigOptionResponse | None:
        _ = kwargs
        session = self._require(session_id)
        if config_id == CONFIG_ROLE:
            choice = str(value).casefold()
            if choice not in ROLE_CHOICES:
                raise RequestError.invalid_params(
                    {"config_id": config_id, "value": str(value)}
                )
            await self._apply_command(session, "role", [choice])
        elif config_id == CONFIG_EFFORT:
            choice = str(value).casefold()
            if choice not in EFFORT_CHOICES:
                raise RequestError.invalid_params(
                    {"config_id": config_id, "value": str(value)}
                )
            await self._apply_command(session, "effort", [choice])
        elif config_id == CONFIG_VERBOSE:
            choice = (
                "true"
                if value is True
                else "false"
                if value is False
                else str(value).casefold()
            )
            if choice not in ("true", "false"):
                raise RequestError.invalid_params(
                    {"config_id": config_id, "value": str(value)}
                )
            await self._apply_command(session, "verbose", [choice])
        elif config_id == CONFIG_STYLE_AGENTS:
            await self._apply_command(session, "style", ["agents", str(value)])
        elif config_id == CONFIG_STYLE_CHAT:
            await self._apply_command(session, "style", ["chat", str(value)])
        elif config_id == CONFIG_ADVANCED:
            choice = str(value)
            if choice in ADVANCED_CHOICES:
                await self._open_advanced_dialog(session, choice)
            # Advanced menu always resets to empty after selection
        else:
            raise RequestError.invalid_params({"config_id": config_id})
        # The response always carries the complete configuration state.
        session.config_options = self._build_config_options(session)
        return schema.SetSessionConfigOptionResponse(
            config_options=session.config_options
        )

    async def set_session_mode(
        self, session_id: str, mode_id: str, **kwargs: Any
    ) -> schema.SetSessionModeResponse | None:
        """Legacy mode selector: the role, kept in sync with the config option."""
        _ = kwargs
        session = self._require(session_id)
        choice = str(mode_id).casefold()
        if choice not in ROLE_CHOICES:
            raise RequestError.invalid_params({"mode_id": mode_id})
        await self._apply_command(session, "role", [choice])
        session.config_options = self._build_config_options(session)
        return schema.SetSessionModeResponse()

    async def _apply_command(
        self, session: KvistAcpSession, name: str, args: list[str]
    ) -> None:
        """Run a ``STDIO_COMMANDS`` handler so settings persist the usual way.

        Runs on the session's worker thread like a turn does: a handler is free
        to open a dialog, and that dialog has to reach the client through the
        same event pump rather than blocking the asyncio loop.
        """
        from kernel.event_stdio import CommandContext, CommandResult, run_command_handler

        handler = _stdio_commands().get(name)
        if handler is None:
            raise RequestError.method_not_found(name)
        context = CommandContext(
            session=session.session,
            ui=session.ui,
            rebuild=None,
            routed_role=None,
            args=args,
        )
        await self._run_events(
            session,
            lambda: session.session.submit_call(
                lambda: run_command_handler(handler, context)
            ),
        )
        result = session.session.result
        if isinstance(result, CommandResult) and not result.ok:
            raise RequestError.invalid_params(
                {"config_id": name, "reason": result.message}
            )

    async def _open_advanced_dialog(
        self, session: KvistAcpSession, dialog: str
    ) -> None:
        """Open a terminal with an advanced settings dialog.

        Maps the advanced menu choice to a slash command and opens a terminal
        running that command. The terminal UI (classic Python or Ink TUI) handles
        the full interactive dialog, and changes are saved to ``~/.kvist/config.json``
        so the next prompt picks them up.

        The slash command is sent to the terminal as initial input so it runs
        automatically when the terminal opens.

        :param session: the active ACP session.
        :param dialog: one of ADVANCED_CHOICES (models, provider, etc).
        """
        client = self._client
        can_open = bool(getattr(self._capabilities, "terminal", False))

        # Map advanced menu choices to slash commands
        command_map = {
            ADVANCED_MODELS: "models",
            ADVANCED_PROVIDER: "provider",
            ADVANCED_API_SETTINGS: "settings-api",
            ADVANCED_CUSTOM_PROVIDER: "provider",  # will show create option
        }
        slash_command = command_map.get(dialog, "")
        if not slash_command:
            return

        message: str
        if client is None or not can_open:
            command, args = terminal_launch_argv()
            line = " ".join([command, *args])
            message = (
                f"This client cannot open a terminal. Run it yourself:\n{line}\n"
                f"Then type: /{slash_command}"
            )
        else:
            command, args = terminal_launch_argv()
            try:
                created = await client.create_terminal(
                    session.session_id,
                    command,
                    args,
                    cwd=str(session.workspace),
                    initial_input=f"/{slash_command}\n",
                )
            except Exception as error:
                message = f"Could not open terminal: {error}"
            else:
                message = f"Opening /{slash_command} dialog in terminal (id {created.terminal_id})."

        session.translator.begin_turn()
        await self._send(session, _message_event(message))
        session.translator.end_turn()

    def _build_config_options(self, session: KvistAcpSession) -> list[Any]:
        return build_config_options(
            role=self._current_role(session),
            effort=self._current_effort(session),
            verbose=self._current_verbose(session),
            style_agents=self._current_style(session, "agents"),
            style_chat=self._current_style(session, "chat"),
            allow_boolean=self._allows_boolean_config(),
        )

    def _mode_state(self, session: KvistAcpSession) -> schema.SessionModeState:
        role = self._current_role(session)
        return schema.SessionModeState(
            current_mode_id=role,
            available_modes=[
                schema.SessionMode(id=value, name=value.title()) for value in ROLE_CHOICES
            ],
        )

    def _allows_boolean_config(self) -> bool:
        session = getattr(self._capabilities, "session", None)
        config_options = getattr(session, "config_options", None)
        return getattr(config_options, "boolean", None) is not None

    def _current_role(self, session: KvistAcpSession) -> str:
        # The saved selection is the source of truth: `main._stdio_role` writes
        # it and the next prompt routes from it. `AgentConfig.role_selection`
        # is only a build-time seed, so reading it would report a role the run
        # no longer uses after `/role`.
        _ = session
        from kernel.user_config import get_saved_role

        return str(get_saved_role() or "auto").casefold()

    def _current_effort(self, session: KvistAcpSession) -> str:
        configured = getattr(session.agent.config, "reasoning_effort", None)
        return str(configured or "medium").casefold()

    def _current_verbose(self, session: KvistAcpSession) -> bool:
        return bool(getattr(session.agent.config, "verbose", False))

    def _current_style(self, session: KvistAcpSession, style_type: str) -> str:
        from kernel.user_config import get_saved_style

        return str(get_saved_style(style_type))  # type: ignore[arg-type]

    # -- helpers -----------------------------------------------------------

    def _require_provider(self) -> None:
        if has_provider():
            return
        raise RequestError.auth_required(
            {"reason": "no LLM provider is configured", "command": LOGIN_COMMAND}
        )

    def _require(self, session_id: str) -> KvistAcpSession:
        session = self._sessions.get(session_id)
        if session is None:
            raise RequestError.resource_not_found(session_id)
        return session

    def _forget(self, session_id: str) -> None:
        session = self._sessions.pop(session_id, None)
        if session is None:
            return
        session.closed = True
        try:
            session.session.close(timeout=1.0)
        except Exception:
            # Closing a session must not fail the request that asked for it.
            pass

    def _known_workspace(self, session_id: str) -> Path | None:
        """Workspace owning ``session_id``, from a live handle or a prior request.

        A bare session id cannot be inverted into a path, so the lookup falls
        back to the workspaces this connection has already been given. A cold
        process handed only an id has nothing to search and reports the session
        as not found.
        """
        session = self._sessions.get(session_id)
        if session is not None:
            return session.workspace
        for workspace in self._workspaces.values():
            if resolve_session_name(workspace, session_id) is not None:
                return workspace
        return None

    def _remember_workspace(self, workspace: Path) -> None:
        with self._lock:
            self._workspaces[str(workspace)] = workspace

    def _usage_model(self, session: KvistAcpSession) -> schema.Usage | None:
        usage = session.translator.usage
        if not usage.get("total"):
            return None
        return schema.Usage(
            total_tokens=usage["total"],
            input_tokens=usage["input"],
            output_tokens=usage["output"],
        )

    def _open_session(
        self,
        cwd: str,
        additional_directories: list[str] | None,
        *,
        session_id: str | None,
    ) -> KvistAcpSession:
        workspace = normalize_cwd(cwd)
        self._remember_workspace(workspace)
        name: str | None = None
        if session_id is not None:
            name = resolve_session_name(workspace, session_id)
            if name is None:
                raise RequestError.resource_not_found(session_id)
            with self._lock:
                existing = self._sessions.get(session_id)
            if existing is not None:
                return existing
        roots = normalize_additional_directories(additional_directories, workspace)
        runtime = self._build_runtime(workspace, roots, session_name=name)
        translator = AcpEventTranslator()
        translator.workspace_root = str(workspace)
        session = KvistAcpSession(
            session_id=session_id or acp_session_id(workspace, runtime["name"]),
            name=runtime["name"],
            workspace=workspace,
            session=runtime["session"],
            ui=runtime["ui"],
            translator=translator,
        )
        session.config_options = self._build_config_options(session)
        with self._lock:
            self._sessions[session.session_id] = session
        return session

    def _build_runtime(
        self,
        workspace: Path,
        roots: tuple[Path, ...],
        *,
        session_name: str | None,
    ) -> dict[str, Any]:
        """Build the Kvist agent and session for one ACP session.

        Mirrors ``event_stdio.launch_multi`` exactly — same ``SafeExecutor``,
        same ``EventApprovalGate``, same ``build_agent`` call — so an ACP turn
        behaves like a stdio turn. Nothing here reimplements agent behavior.
        """
        from kernel.event_session import EventApprovalGate, EventSession
        from kernel.event_stream_ui import EventStreamUI
        from kernel.executor import SafeExecutor
        from kernel.frontend_protocol import FrontendChannel

        build_agent_fn = self._build_agent_fn or _default_build_agent()
        channel = FrontendChannel()
        ui = EventStreamUI(channel)
        executor = SafeExecutor(
            workspace,
            approvals=EventApprovalGate(channel, mode="ask", workspace=workspace),
            read_roots=roots,
        )
        original_stdin = sys.stdin
        try:
            with open(os.devnull, encoding="utf-8") as null_input, redirect_stdout(
                sys.stderr
            ):
                sys.stdin = null_input
                agent, _config, _warnings = build_agent_fn(
                    executor,
                    max_steps=150,
                    persist_logs=False,
                    verbose=False,
                    ui=ui,
                )
        finally:
            sys.stdin = original_stdin

        if session_name is None:
            from kernel.session_store import SessionStore

            # A new ACP thread starts its own conversation rather than
            # inheriting the previous launch's history.
            session_name = SessionStore(workspace).new()["active_session"]
        agent.current_session = session_name
        return {
            "name": session_name,
            "session": EventSession(channel, agent),
            "ui": ui,
        }


# --- Prompt content ----------------------------------------------------------


def extract_prompt(blocks: list[Any], workspace: Path) -> tuple[str, list[str]]:
    """Flatten ACP prompt blocks into Kvist's goal text.

    Text blocks keep their Markdown and their order. Resource links that point
    inside the workspace are named in an ``Attached files`` section so the model
    knows which files the user attached; nothing is read here and no external
    URI is fetched — a resource link is a pointer the agent may look up through
    its own read boundary, never content this transport injects.

    :param blocks: the ACP prompt content blocks.
    :param workspace: the session's absolute working directory.
    :returns: the goal text and the workspace-relative attached paths.
    """
    texts: list[str] = []
    attached: list[str] = []
    for block in blocks or []:
        kind = getattr(block, "type", None)
        if kind == "text":
            text = str(getattr(block, "text", "") or "")
            if text.strip():
                texts.append(text)
        elif kind == "resource_link":
            path = _workspace_path(str(getattr(block, "uri", "") or ""), workspace)
            if path is not None:
                attached.append(path)
        elif kind == "resource":
            text = getattr(getattr(block, "resource", None), "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text)
    body = "\n\n".join(texts).strip()
    if attached:
        listing = "\n".join(f"- {item}" for item in attached)
        body = f"{body}\n\nAttached files:\n{listing}".strip()
    return body, attached


def _workspace_path(uri: str, workspace: Path) -> str | None:
    """A workspace-relative path for a resource URI, or None when it is outside.

    Only paths that resolve inside the workspace are kept, so an attached
    resource cannot smuggle a traversal or an external document into the
    prompt.

    :param uri: the resource link's URI or path.
    :param workspace: the session's absolute working directory.
    :returns: the workspace-relative path, or None when the URI is not usable.
    """
    if not uri:
        return None
    raw = uri
    if raw.startswith("file://"):
        raw = raw[len("file://") :]
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        candidate = workspace / candidate
    try:
        resolved = candidate.resolve(strict=False)
        relative = resolved.relative_to(workspace.resolve())
    except (OSError, ValueError):
        return None
    return str(relative)


# --- Slash commands ----------------------------------------------------------

_COMMAND_PREFIX = "/"

# ACP-only slash commands. These need the ACP connection itself, so they cannot
# live in ``main.STDIO_COMMANDS``; ``available_commands`` still lists them so
# the advertised set and the executable set stay identical.
_COMMAND_TERMINAL = "terminal"
ACP_ONLY_COMMANDS: dict[str, str] = {
    _COMMAND_TERMINAL: "Open a terminal running the Kvist CLI",
}


def terminal_launch_argv() -> tuple[str, list[str]]:
    """How to start this same Kvist build without ``--acp``.

    Reconstructed from this process rather than looked up on ``PATH``, so a
    frozen binary and a source checkout both relaunch themselves.

    :returns: ``(command, args)`` for a terminal that should run the CLI.
    """
    flags = {"--acp", "--login"}
    rest = [item for item in sys.argv[1:] if item not in flags]
    if getattr(sys, "frozen", False):
        return sys.executable, rest
    return sys.executable, [sys.argv[0], *rest]


def parse_slash_command(text: str) -> tuple[str, list[str]] | None:
    """Split ``/name args`` into a command, or None when it is a normal prompt.

    ACP delivers slash commands as ordinary prompt text, so the agent decides.
    Leading whitespace is ignored and the name is case-insensitive, matching
    ``event_stdio``'s normalization.

    :param text: the prompt text.
    :returns: ``(name, args)`` for a command, or None for a normal prompt.
    """
    stripped = text.strip()
    if not stripped.startswith(_COMMAND_PREFIX):
        return None
    body = stripped[1:].strip()
    if not body:
        return None
    parts = body.split()
    return parts[0].casefold(), parts[1:]


def available_commands() -> list[tuple[str, str]]:
    """Slash commands this transport will intercept, with short descriptions.

    The list is the real handler table plus the ACP-only commands, so a command
    can never be advertised without being executable and an executable command
    cannot go unadvertised.

    :returns: ``(name, description)`` pairs in name order.
    """
    descriptions = {name: f"Kvist /{name}" for name in _stdio_commands()}
    descriptions.update(ACP_ONLY_COMMANDS)
    return sorted(descriptions.items())


def _stdio_commands() -> dict[str, Any]:
    from kernel.main import STDIO_COMMANDS

    return STDIO_COMMANDS


# --- Event shims -------------------------------------------------------------


class _SimpleEvent:
    """Minimal event stand-in for replay and for locally generated messages."""

    def __init__(self, kind: str, **fields: Any) -> None:
        self.type = kind
        for key, value in fields.items():
            setattr(self, key, value)


def _dict_event(payload: dict[str, Any]) -> Any:
    event = _SimpleEvent(str(payload.get("type", "") or ""))
    for key, value in payload.items():
        setattr(event, key, value)
    return event


def _error_event(message: str) -> Any:
    return _SimpleEvent("error", message=message)


def _message_event(message: str) -> Any:
    return _SimpleEvent("message", markdown=message)


# --- Config option rendering -------------------------------------------------


def build_config_options(
    *,
    role: str,
    effort: str,
    verbose: bool,
    style_agents: str,
    style_chat: str,
    allow_boolean: bool,
) -> list[Any]:
    """Render Kvist's session knobs as ACP Session Config Options.

    Categories come from the protocol's reserved set: ``mode`` for the role,
    ``thought_level`` for reasoning effort. Verbose is a boolean when the client
    advertised ``session.configOptions.boolean``; otherwise it degrades to a
    two-value select for older clients.

    :param role: the active role id.
    :param effort: the active reasoning-effort id.
    :param verbose: whether verbose logging is on.
    :param style_agents: the active agent style name.
    :param style_chat: the active chat style name.
    :param allow_boolean: whether the client accepts boolean config options.
    :returns: the complete option list in priority order.
    """
    from kernel.user_config import get_style_options

    role = role if role in ROLE_CHOICES else "auto"
    effort = effort if effort in EFFORT_CHOICES else "medium"

    agents_style_options = get_style_options("agents")
    chat_style_options = get_style_options("chat")

    options: list[Any] = [
        schema.SessionConfigOptionSelect(
            id=CONFIG_ROLE,
            name="Role",
            description="How Kvist routes this request (write access, devops, or chat)",
            category="mode",  # type: ignore[arg-type]
            type="select",
            current_value=role,
            options=[
                schema.SessionConfigSelectOption(value=value, name=value.title())
                for value in ROLE_CHOICES
            ],
        ),
        schema.SessionConfigOptionSelect(
            id=CONFIG_EFFORT,
            name="Reasoning effort",
            description="How much reasoning the model does before answering",
            category="thought_level",  # type: ignore[arg-type]
            type="select",
            current_value=effort,
            options=[
                schema.SessionConfigSelectOption(value=value, name=value)
                for value in EFFORT_CHOICES
            ],
        ),
        schema.SessionConfigOptionSelect(
            id=CONFIG_STYLE_AGENTS,
            name="Agent style",
            description="Tone and habits of coder/reader/devops action loop",
            type="select",
            current_value=style_agents,
            options=[
                schema.SessionConfigSelectOption(value=name, name=name)
                for name in agents_style_options.keys()
            ],
        ),
        schema.SessionConfigOptionSelect(
            id=CONFIG_STYLE_CHAT,
            name="Chat style",
            description="Tone of the chat role's answers",
            type="select",
            current_value=style_chat,
            options=[
                schema.SessionConfigSelectOption(value=name, name=name)
                for name in chat_style_options.keys()
            ],
        ),
    ]

    if allow_boolean:
        options.append(
            schema.SessionConfigOptionBoolean(
                id=CONFIG_VERBOSE,
                name="Verbose logs",
                description="Show detailed step execution logs",
                type="boolean",
                current_value=bool(verbose),
            )
        )
    else:
        options.append(
            schema.SessionConfigOptionSelect(
                id=CONFIG_VERBOSE,
                name="Verbose logs",
                description="Show detailed step execution logs",
                type="select",
                current_value="true" if verbose else "false",
                options=[
                    schema.SessionConfigSelectOption(value="true", name="On"),
                    schema.SessionConfigSelectOption(value="false", name="Off"),
                ],
            )
        )

    # Advanced settings menu — opens terminal dialogs
    options.append(
        schema.SessionConfigOptionSelect(
            id=CONFIG_ADVANCED,
            name="⚙️ Advanced",
            description="Models, provider, API settings (opens terminal)",
            type="select",
            current_value="",
            options=[
                schema.SessionConfigSelectOption(
                    value=ADVANCED_MODELS, name="Choose models..."
                ),
                schema.SessionConfigSelectOption(
                    value=ADVANCED_PROVIDER, name="Change provider..."
                ),
                schema.SessionConfigSelectOption(
                    value=ADVANCED_API_SETTINGS, name="API settings..."
                ),
                schema.SessionConfigSelectOption(
                    value=ADVANCED_CUSTOM_PROVIDER, name="Add custom provider..."
                ),
            ],
        )
    )

    return options


# --- Entry points ------------------------------------------------------------


def _kvist_version() -> str:
    from kernel.version import kvist_version

    return kvist_version()


def _default_build_agent() -> Callable:
    from kernel.main import build_agent

    return build_agent


def has_provider() -> bool:
    """Whether a saved or environment provider can serve a turn.

    :returns: True when a provider is configured in the environment or user config.
    """
    if os.getenv("AGENT_LLM_PROVIDER"):
        return True
    from kernel.user_config import get_saved_provider_or_none

    return get_saved_provider_or_none() is not None


def run_login() -> int:
    """Terminal setup: collect and save provider credentials, then exit.

    This is the flow Zed's terminal auth runs. It never touches the ACP
    connection and never builds a ``LocalAgent`` — building one would write
    ``.kvist/`` state and seed ``KVIST.md``, which is not what a setup command
    is for. Secrets are read from the terminal only, never over ACP.

    :returns: 0 on success, 1 when nothing was configured.
    """
    import getpass

    from kernel.llm_clients import normalize_provider, required_secret_keys_for_provider
    from kernel.main import choose_provider
    from kernel.user_config import ensure_provider_credentials, get_saved_provider_or_none

    current = get_saved_provider_or_none()
    print("Kvist setup — provider credentials", file=sys.stderr)
    print(f"Current provider: {current or '(none)'}", file=sys.stderr)
    provider = choose_provider(current or "openai", False, ui=None)
    if not provider:
        print("No provider selected; nothing was changed.", file=sys.stderr)
        return 1

    normalized = normalize_provider(provider)
    keys = required_secret_keys_for_provider(normalized, ())
    ensure_provider_credentials(
        normalized,
        validation_model="",
        required_keys=keys,
        prompt_secret_fn=getpass.getpass,
    )
    print(f"Provider saved: {normalized}", file=sys.stderr)
    return 0


def _install_extra_routes() -> None:
    """Make the SDK route every method Kvist actually implements.

    Two gaps in ``agent-client-protocol`` 0.12.x would otherwise break methods
    this agent advertises and Zed calls:

    * ``session/delete`` has a request model but no route at all.
    * ``session/close`` and ``session/resume`` are registered with the SDK's
      draft gate, and a gated route answers "method not found" instead of
      running. Kvist runs the stable v1 protocol, where both are ordinary
      methods.

    Each route is registered unconditionally: the SDK's own registration of
    ``session/close`` and ``session/resume`` exists but is gated, so "already
    present" would leave the gate in place. ``session/fork`` stays gated and
    unadvertised: Kvist does not implement it.
    """
    from acp.agent import connection as agent_connection
    from acp.agent.router import build_agent_router as sdk_build_agent_router

    original = getattr(agent_connection, "build_agent_router", None)
    if getattr(original, "_kvist_extra_routes", False):
        return

    def build_agent_router(agent: Agent, use_unstable_protocol: bool = False) -> Any:
        router = sdk_build_agent_router(agent, use_unstable_protocol=use_unstable_protocol)
        for method, model, attr in (
            (_METHOD_SESSION_DELETE, schema.DeleteSessionRequest, "delete_session"),
            (_METHOD_SESSION_CLOSE, schema.CloseSessionRequest, "close_session"),
            (_METHOD_SESSION_RESUME, schema.ResumeSessionRequest, "resume_session"),
        ):
            router.route_request(
                method, model, agent, attr, adapt_result=normalize_result
            )
        return router

    build_agent_router._kvist_extra_routes = True  # type: ignore[attr-defined]
    agent_connection.build_agent_router = build_agent_router


def run_acp(*, build_agent_fn: Callable | None = None) -> int:
    """Serve ACP over stdio until the client closes the connection.

    :param build_agent_fn: overrides ``main.build_agent`` (tests inject a fake).
    :returns: process exit status.
    """
    _install_extra_routes()
    agent = KvistAcpAgent(build_agent_fn=build_agent_fn)
    asyncio.run(_acp_run_agent_with_stdio(agent))
    return 0


async def _acp_run_agent_with_stdio(agent: Agent) -> None:
    """Serve ``agent`` on this process's stdio.

    The streams come from ``acp_stdio`` rather than the SDK so that a client
    capturing the agent's stdout as a plain file is answered instead of
    killing the process at startup. ``run_agent`` takes the writer first and
    the reader second, the opposite of the pair ``stdio_streams`` returns.

    :param agent: the ACP agent to serve.
    """
    reader, writer = await stdio_streams(limit=DEFAULT_STDIO_BUFFER_LIMIT_BYTES)
    try:
        await _acp_run_agent(agent, writer, reader, use_unstable_protocol=False)
    finally:
        # Releases the writer bookkeeping only; the descriptor is the client's.
        writer.close()
