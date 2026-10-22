# -*- coding: utf-8 -*-
"""The ACP transport: wire, sessions, prompt content, config, permissions.

These run the real ``acp.core.run_agent`` over a socket pair and speak JSON-RPC
to it, so the SDK's routing, request models and serialization are exercised
alongside Kvist's handler code. No LLM is called: the agent is built through an
injected ``build_agent_fn``.
"""
from __future__ import annotations

import asyncio
import json
import socket
import sys
import time
from types import SimpleNamespace

import pytest
from acp import helpers, schema
from acp.core import run_agent

from acp_events import AcpEventTranslator
from acp_frontend import (
    ACP_ONLY_COMMANDS,
    AcpInputBridge,
    KvistAcpAgent,
    _install_extra_routes,
    acp_session_id,
    available_commands,
    build_config_options,
    extract_prompt,
    normalize_cwd,
    parse_slash_command,
    resolve_session_name,
    terminal_launch_argv,
)


@pytest.fixture(autouse=True)
def configured_provider(monkeypatch):
    """A configured provider is what unlocks ``session/new``; no key is read."""
    monkeypatch.setenv("AGENT_LLM_PROVIDER", "openai")


# --- Test doubles -------------------------------------------------------------


class FakeConfig:
    """The AgentConfig fields the transport reads for the config options."""

    def __init__(self, role="coder", effort="medium", verbose=False) -> None:
        self.role_selection = role
        self.reasoning_effort = effort
        self.verbose = verbose


class FakeAgent:
    """A LocalAgent stand-in: records the goal and answers without an LLM."""

    def __init__(self, config: FakeConfig, workspace=None) -> None:
        self.config = config
        self.workspace = workspace
        self.current_session = None
        self.goals: list[str] = []
        self.cancelled = 0

    def new_session(self):
        from kernel.session.session_store import SessionStore

        self.current_session = SessionStore(self.workspace).new()["active_session"]
        return self.current_session

    def switch_session(self, name):
        self.current_session = name

    def run(self, goal, routed_role=None):
        self.goals.append(goal)
        return f"answered: {goal}"

    def request_cancel(self):
        self.cancelled += 1

    def resume(self):
        return None


class CancellingAgent(FakeAgent):
    """Blocks in ``run`` until cancelled, so a cancel can be observed."""

    def run(self, goal, routed_role=None):
        self.goals.append(goal)
        while not self.cancelled:
            time.sleep(0.01)
        return "stopped"


def fake_build_agent(config: FakeConfig | None = None):
    """A ``build_agent`` stand-in that never touches a provider."""
    resolved = config or FakeConfig()

    def build(executor, **kwargs):
        return FakeAgent(resolved, executor.workspace), resolved, []

    return build


# --- JSON-RPC client harness --------------------------------------------------


class Wire:
    """One connected ACP client speaking JSON-RPC to a KvistAcpAgent."""

    def __init__(self, build_agent_fn=None) -> None:
        self.agent: KvistAcpAgent | None = None
        self.session_id: str | None = None
        self.notifications: list[dict] = []
        self._id = 0
        self._build_agent_fn = build_agent_fn or fake_build_agent()

    async def __aenter__(self) -> "Wire":
        _install_extra_routes()
        self._socks = socket.socketpair()
        for sock in self._socks:
            sock.setblocking(False)
        self._agent_reader, self._agent_writer = await asyncio.open_connection(
            sock=self._socks[0]
        )
        self.reader, self.writer = await asyncio.open_connection(sock=self._socks[1])
        self.agent = KvistAcpAgent(build_agent_fn=self._build_agent_fn)
        self.server = asyncio.create_task(
            run_agent(self.agent, self._agent_writer, self._agent_reader)
        )
        return self

    async def __aexit__(self, *exc) -> None:
        self.writer.close()
        try:
            await asyncio.wait_for(self.server, timeout=5)
        except (asyncio.TimeoutError, Exception):
            self.server.cancel()

    async def call(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        request_id = self._id
        self.writer.write(
            (
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": request_id,
                        "method": method,
                        "params": params or {},
                    }
                )
                + "\n"
            ).encode()
        )
        await self.writer.drain()
        while True:
            try:
                line = await asyncio.wait_for(self.reader.readline(), timeout=15)
            except asyncio.TimeoutError as error:
                raise AssertionError(
                    f"no reply to {method} in 15s; messages received meanwhile: "
                    f"{json.dumps(self.notifications, default=str)[:4000]}"
                ) from error
            assert line, "agent closed the connection"
            message = json.loads(line)
            if message.get("id") == request_id:
                return message
            self.notifications.append(message)

    async def notify(self, method: str, params: dict | None = None) -> None:
        self.writer.write(
            (
                json.dumps({"jsonrpc": "2.0", "method": method, "params": params or {}})
                + "\n"
            ).encode()
        )
        await self.writer.drain()

    async def initialize(self, terminal: bool = True) -> dict:
        return await self.call(
            "initialize",
            {
                "protocolVersion": 1,
                "clientCapabilities": {
                    "fs": {"readTextFile": False, "writeTextFile": False},
                    "terminal": terminal,
                    "elicitation": {"form": {}},
                    "session": {"configOptions": {"boolean": {}}},
                },
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        )

    async def open_session(self, cwd: str) -> str:
        response = await self.call(
            "session/new",
            {"cwd": cwd, "mcpServers": [], "additionalDirectories": []},
        )
        assert "result" in response, response
        self.session_id = response["result"]["sessionId"]
        return self.session_id

    def _chunk_text(self, session_update: str) -> list[str]:
        return [
            update["params"]["update"]["content"].get("text", "")
            for update in self.notifications
            if update.get("method") == "session/update"
            and update["params"]["update"].get("sessionUpdate") == session_update
        ]

    def agent_text(self) -> list[str]:
        return self._chunk_text("agent_message_chunk")

    def user_text(self) -> list[str]:
        return self._chunk_text("user_message_chunk")


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, timeout=30))


def seed_history(workspace, name, turns):
    """Write a real session journal so ``session/load`` has something to replay.

    ``turns`` is a list of ``(prompt, answer)`` pairs, written through
    ``SessionJournal`` — the same code path a live turn uses.
    """
    from kernel.protocol.frontend_protocol import MessageEvent
    from kernel.session.session_events import SessionJournal
    from kernel.session.session_store import SessionStore, get_journal_path

    store = SessionStore(workspace)
    store.create(name)
    journal = SessionJournal(
        workspace, lambda: get_journal_path(store, name), lambda m: None
    )
    for prompt, answer in turns:
        journal.begin_turn(prompt)
        journal.emit(MessageEvent(answer))
        journal.end_turn()
    return store


# --- Wire / handshake ---------------------------------------------------------


def test_initialize_reports_only_implemented_capabilities():
    async def scenario():
        async with Wire() as wire:
            result = (await wire.initialize())["result"]

            assert result["protocolVersion"] == 1
            assert result["agentInfo"]["name"] == "kvist"
            assert result["agentInfo"]["version"]
            capabilities = result["agentCapabilities"]
            assert capabilities["auth"] is not None
            assert capabilities["loadSession"] is True

            assert capabilities["promptCapabilities"] == {
                "image": False,
                "audio": False,
                "embeddedContext": True,
            }

            # Nothing is advertised that does not work end to end.
            assert capabilities.get("nes") is None
            assert capabilities.get("mcpCapabilities") is None
            assert capabilities.get("providers") is None

    run(scenario())


def test_initialize_offers_terminal_auth():
    async def scenario():
        async with Wire() as wire:
            methods = (await wire.initialize())["result"]["authMethods"]

            assert [m["type"] for m in methods] == ["terminal"]
            assert methods[0]["args"] == ["--login"]

    run(scenario())


def test_unknown_method_returns_method_not_found():
    async def scenario():
        async with Wire() as wire:
            response = await wire.call("does/not/exist", {})

            assert response["error"]["code"] == -32601

    run(scenario())


def test_authenticate_refuses_an_unknown_method():
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            response = await wire.call("authenticate", {"methodId": "password"})

            assert response["error"]["code"] == -32602

    run(scenario())


def test_authenticate_accepts_terminal():
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            response = await wire.call("authenticate", {"methodId": "terminal"})

            assert "result" in response, response

    run(scenario())


def test_session_delete_is_routed_despite_the_sdk_gap():
    """0.12.x defines DeleteSessionRequest but never registers the method."""
    _install_extra_routes()
    import acp.agent.connection as connection

    router = connection.build_agent_router(
        KvistAcpAgent(build_agent_fn=fake_build_agent())
    )

    assert "session/delete" in router._requests


# --- Session lifecycle --------------------------------------------------------


def test_session_ids_are_opaque_and_unique_per_workspace(tmp_path):
    first = acp_session_id(tmp_path, "chat-1")
    other_dir = tmp_path / "other"
    other_dir.mkdir()

    assert first == acp_session_id(tmp_path, "chat-1")
    assert first != acp_session_id(other_dir, "chat-1")
    assert first != acp_session_id(tmp_path, "chat-2")
    assert first.startswith("k1_")
    assert "chat-1" not in first
    assert str(tmp_path) not in first


def test_a_session_id_from_another_workspace_does_not_resolve(tmp_path):
    from kernel.session.session_store import SessionStore

    SessionStore(tmp_path).create("chat-1")
    foreign = tmp_path / "foreign"
    foreign.mkdir()

    assert resolve_session_name(tmp_path, acp_session_id(tmp_path, "chat-1")) == "chat-1"
    assert resolve_session_name(foreign, acp_session_id(tmp_path, "chat-1")) is None


def test_cwd_must_be_an_absolute_existing_directory(tmp_path):
    assert normalize_cwd(str(tmp_path)) == tmp_path.resolve()

    with pytest.raises(ValueError):
        normalize_cwd("relative/path")
    with pytest.raises(ValueError):
        normalize_cwd(str(tmp_path / "missing"))
    with pytest.raises(ValueError):
        normalize_cwd("")


def test_a_relative_cwd_is_refused_rather_than_created(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            response = await wire.call(
                "session/new",
                {
                    "cwd": "relative/dir",
                    "mcpServers": [],
                    "additionalDirectories": [],
                },
            )

            assert "error" in response
            assert not (tmp_path / "relative").exists()

    run(scenario())


def test_new_session_starts_its_own_conversation(tmp_path):
    """A launch must not plan against the previous launch's history."""
    seed_history(tmp_path, "old-session", [("earlier work", "earlier answer")])

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            name = resolve_session_name(tmp_path, session_id)

            assert name != "old-session"
            # The old conversation is not replayed into the new thread.
            assert wire.user_text() == []
            assert wire.agent_text() == []

    run(scenario())


def test_session_list_survives_a_restart(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            response = await wire.call("session/list", {"cwd": str(tmp_path)})

            listed = response["result"]["sessions"]
            assert [item["sessionId"] for item in listed] == [session_id]
            assert listed[0]["cwd"] == str(tmp_path)

    run(scenario())


def test_session_list_does_not_leak_another_workspace(tmp_path):
    foreign = tmp_path / "foreign"
    foreign.mkdir()

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            await wire.open_session(str(tmp_path))

            response = await wire.call("session/list", {"cwd": str(foreign)})

            assert response["result"]["sessions"] == []

    run(scenario())


def test_load_replays_the_journal_into_session_updates(tmp_path):
    seed_history(tmp_path, "chat-1", [("do the thing", "here is the answer")])

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            response = await wire.call(
                "session/load",
                {
                    "cwd": str(tmp_path),
                    "sessionId": acp_session_id(tmp_path, "chat-1"),
                    "mcpServers": [],
                    "additionalDirectories": [],
                },
            )

            assert "result" in response, response
            assert wire.user_text() == ["do the thing"]
            assert "here is the answer" in "".join(wire.agent_text())

    run(scenario())


def test_load_does_not_show_the_same_answer_twice(tmp_path):
    """A streamed answer is journaled once; replay must not emit it twice."""
    seed_history(tmp_path, "chat-1", [("", "Answer")])

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            await wire.call(
                "session/load",
                {
                    "cwd": str(tmp_path),
                    "sessionId": acp_session_id(tmp_path, "chat-1"),
                    "mcpServers": [],
                    "additionalDirectories": [],
                },
            )

            assert wire.agent_text().count("Answer") == 1

    run(scenario())


def test_load_keeps_the_order_of_a_multi_turn_conversation(tmp_path):
    seed_history(
        tmp_path,
        "chat-1",
        [("first question", "first answer"), ("second question", "second answer")],
    )

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            await wire.call(
                "session/load",
                {
                    "cwd": str(tmp_path),
                    "sessionId": acp_session_id(tmp_path, "chat-1"),
                    "mcpServers": [],
                    "additionalDirectories": [],
                },
            )

            assert wire.user_text() == ["first question", "second question"]
            assert wire.agent_text() == ["first answer", "second answer"]

    run(scenario())


def test_load_of_an_unknown_session_reports_resource_not_found(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            response = await wire.call(
                "session/load",
                {
                    "cwd": str(tmp_path),
                    "sessionId": "k1_" + "0" * 40,
                    "mcpServers": [],
                    "additionalDirectories": [],
                },
            )

            assert response["error"]["code"] == -32002

    run(scenario())


def test_close_then_prompt_reports_the_session_is_gone(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))

            await wire.call("session/close", {"sessionId": session_id})
            response = await wire.call(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": "hi"}]},
            )

            assert response["error"]["code"] == -32002

    run(scenario())


def test_delete_removes_the_session_from_the_list(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))

            deleted = await wire.call("session/delete", {"sessionId": session_id})
            assert "result" in deleted, deleted

            listed = await wire.call("session/list", {"cwd": str(tmp_path)})
            ids = [item["sessionId"] for item in listed["result"]["sessions"]]
            # The deleted thread is gone. Deleting the active session leaves
            # the workspace on a fresh one rather than with no session at all.
            assert session_id not in ids
            assert len(ids) == 1

    run(scenario())


def test_delete_works_on_the_workspace_active_session(tmp_path):
    """Zed's "delete thread" removes the thread you are looking at.

    Kvist's store refuses to delete the active session so its marker can never
    point at a deleted one; the transport moves the marker aside first rather
    than refusing the client's request.
    """
    from kernel.session.session_store import SessionStore

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            name = resolve_session_name(tmp_path, session_id)
            assert SessionStore(tmp_path).current_session == name

            deleted = await wire.call("session/delete", {"sessionId": session_id})

            assert "result" in deleted, deleted
            assert resolve_session_name(tmp_path, session_id) is None

    run(scenario())


def test_delete_a_session_that_was_only_listed(tmp_path):
    """`session/delete` carries no cwd, so a listed thread must still resolve."""
    seed_history(tmp_path, "chat-1", [("hi", "hello")])

    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            await wire.call("session/list", {"cwd": str(tmp_path)})

            deleted = await wire.call(
                "session/delete", {"sessionId": acp_session_id(tmp_path, "chat-1")}
            )

            assert "result" in deleted, deleted

    run(scenario())


def test_delete_of_an_unknown_session_reports_resource_not_found(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            await wire.call("session/list", {"cwd": str(tmp_path)})

            response = await wire.call("session/delete", {"sessionId": "k1_" + "0" * 40})

            assert response["error"]["code"] == -32002

    run(scenario())


def test_close_is_served_even_though_the_sdk_gates_it():
    """A capability Kvist advertises must never answer "method not found"."""
    import acp.agent.connection as connection

    _install_extra_routes()
    router = connection.build_agent_router(
        KvistAcpAgent(build_agent_fn=fake_build_agent())
    )

    assert "session/close" in router._requests
    assert router._requests["session/close"].warn_unstable is False
    assert router._requests["session/resume"].warn_unstable is False
    # Fork stays out: Kvist does not implement it and does not advertise it.
    assert router._requests["session/fork"].warn_unstable is True


def test_cancel_stops_the_turn_and_leaves_the_process_healthy(tmp_path):
    agent_holder: dict = {}

    def build(executor, **kwargs):
        config = FakeConfig()
        agent = CancellingAgent(config, executor.workspace)
        agent_holder["agent"] = agent
        return agent, config, []

    async def scenario():
        async with Wire(build_agent_fn=build) as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))

            prompt = asyncio.create_task(
                wire.call(
                    "session/prompt",
                    {"sessionId": session_id, "prompt": [{"type": "text", "text": "long job"}]},
                )
            )
            await asyncio.sleep(0.1)
            await wire.notify("session/cancel", {"sessionId": session_id})

            response = await asyncio.wait_for(prompt, timeout=15)

            assert response["result"]["stopReason"] == "cancelled"
            assert agent_holder["agent"].cancelled >= 1

            # The process stays usable for the next turn.
            follow_up = await wire.call(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": "next"}]},
            )
            assert "result" in follow_up, follow_up

    run(scenario())


# --- Prompt content -----------------------------------------------------------


def test_extract_prompt_keeps_markdown_and_order(tmp_path):
    blocks = [helpers.text_block("# Title"), helpers.text_block("second **bit**")]

    text, attached = extract_prompt(blocks, tmp_path)

    assert text == "# Title\n\nsecond **bit**"
    assert attached == []


def test_extract_prompt_names_attached_workspace_files(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("print(1)\n", encoding="utf-8")

    blocks = [
        helpers.text_block("look at this"),
        helpers.resource_link_block("app.py", str(tmp_path / "src" / "app.py")),
    ]

    text, attached = extract_prompt(blocks, tmp_path)

    assert attached == ["src/app.py"]
    assert "Attached files:" in text
    assert "src/app.py" in text


def test_extract_prompt_ignores_resources_outside_the_workspace(tmp_path):
    outside = tmp_path.parent / "outside.txt"

    blocks = [helpers.resource_link_block("outside.txt", str(outside))]

    text, attached = extract_prompt(blocks, tmp_path)

    assert attached == []
    assert text == ""


def test_slash_commands_are_picked_out_of_prompt_text():
    assert parse_slash_command("/role coder") == ("role", ["coder"])
    assert parse_slash_command("  /effort high  ") == ("effort", ["high"])
    assert parse_slash_command("/ROLE coder") == ("role", ["coder"])
    assert parse_slash_command("fix the bug") is None
    assert parse_slash_command("/") is None


# --- Config options -----------------------------------------------------------


def test_config_options_cover_role_effort_and_verbose():
    options = build_config_options(
        role="coder",
        effort="high",
        verbose=False,
        style="Senior-dev",
        allow_boolean=True,
    )

    option_ids = [option.id for option in options]
    assert "role" in option_ids
    assert "effort" in option_ids
    assert "planning" not in option_ids
    assert "verbose" in option_ids
    assert "style" in option_ids
    assert "style_agents" not in option_ids and "style_chat" not in option_ids
    assert "advanced" in option_ids

    role_option = next(opt for opt in options if opt.id == "role")
    effort_option = next(opt for opt in options if opt.id == "effort")
    verbose_option = next(opt for opt in options if opt.id == "verbose")

    assert role_option.category == "mode"
    assert effort_option.category == "thought_level"
    assert verbose_option.type == "boolean"


def test_verbose_degrades_to_a_select_without_boolean_support():
    options = build_config_options(
        role="coder",
        effort="high",
        verbose=True,
        style="Senior-dev",
        allow_boolean=False,
    )

    verbose = next(opt for opt in options if opt.id == "verbose")
    assert verbose.type == "select"
    assert verbose.current_value == "true"


def test_unknown_role_or_effort_falls_back_to_a_real_choice():
    options = build_config_options(
        role="nonsense",
        effort="nonsense",
        verbose=False,
        style="Senior-dev",
        allow_boolean=True,
    )

    role_option = next(opt for opt in options if opt.id == "role")
    effort_option = next(opt for opt in options if opt.id == "effort")
    assert role_option.current_value == "auto"
    assert effort_option.current_value == "medium"


def test_set_config_option_updates_the_session(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))

            response = await wire.call(
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": "role",
                    "value": "reader",
                    "type": "select",
                },
            )

            assert "result" in response, response
            updated = {
                option["id"]: option["currentValue"]
                for option in response["result"]["configOptions"]
            }
            assert updated["role"] == "reader"

    run(scenario())


def test_set_config_option_rejects_an_unknown_value(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))

            response = await wire.call(
                "session/set_config_option",
                {
                    "sessionId": session_id,
                    "configId": "role",
                    "value": "root",
                    "type": "select",
                },
            )

            assert response["error"]["code"] == -32602

    run(scenario())


def test_set_config_option_never_carries_credentials():
    """API keys are not session configuration; the wire has no place for them."""
    options = build_config_options(
        role="auto",
        effort="medium",
        verbose=False,
        style="Senior-dev",
        allow_boolean=True,
    )
    dumped = json.dumps(
        [option.model_dump(by_alias=True) for option in options]
    ).lower()

    for word in ("key", "token", "secret", "password"):
        assert word not in dumped


def test_advanced_menu_offers_terminal_dialogs():
    """The advanced menu provides options for complex settings via terminal."""
    options = build_config_options(
        role="auto",
        effort="medium",
        verbose=False,
        style="Senior-dev",
        allow_boolean=True,
    )

    advanced = next(opt for opt in options if opt.id == "advanced")
    assert advanced.type == "select"
    assert advanced.current_value == ""  # Always resets to empty

    option_values = [opt.value for opt in advanced.options]
    assert "models" in option_values
    assert "provider" in option_values
    assert "api-settings" in option_values
    assert "custom-provider" in option_values


# --- Permissions and elicitation ----------------------------------------------


def _request(**fields):
    from kernel.protocol.frontend_protocol import InputRequestEvent

    defaults = {
        "request_id": "r1",
        "kind": "confirm",
        "title": "Dangerous command",
        "body": "",
        "options": None,
        "subject": None,
        "remember_pattern": None,
    }
    defaults.update(fields)
    return InputRequestEvent(**defaults)


def _permission_subject_command():
    return {"kind": "command", "command": "rm -rf build", "reason": "clean up"}


class RecordingClient:
    def __init__(self, outcomes) -> None:
        self.outcomes = list(outcomes)
        self.calls: list[tuple] = []

    async def request_permission(self, session_id, tool_call, options, **kwargs):
        self.calls.append((session_id, tool_call, options))
        return schema.RequestPermissionResponse(outcome=self.outcomes.pop(0))


def test_capability_approval_shows_command_and_scope_without_remember():
    client = RecordingClient([
        schema.AllowedOutcome(outcome="selected", option_id="allow-once"),
        schema.AllowedOutcome(outcome="selected", option_id="reject-once"),
    ])
    bridge = AcpInputBridge(client, "s1", None)
    for index, title in enumerate(("Host execution", "Network access")):
        body = 'Argv: ["python", "build.py"]\nDirectory: project\nReason: build the project'
        pending = bridge.describe(_request(title=title, body=body, request_id=str(index)))
        answered = {}
        run(bridge.handle(pending, lambda **kw: answered.update(kw)))
        _, tool_call, options = client.calls[-1]
        assert tool_call.title == title
        assert tool_call.content[0].content.text == body
        assert [option.kind for option in options] == ["allow_once", "reject_once"]
        assert answered["approved"] is (index == 0)
        assert answered["remember"] is False


def test_allow_once_approves_without_saving():
    client = RecordingClient(
        [schema.AllowedOutcome(outcome="selected", option_id="allow-once")]
    )
    bridge = AcpInputBridge(client, "s1", None)
    pending = bridge.describe(
        _request(
            kind="confirm",
            remember_pattern="rm -rf build",
            subject=_permission_subject_command(),
        )
    )
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered == {"value": None, "approved": True, "remember": False}


def test_allow_always_saves_only_a_rememberable_pattern():
    client = RecordingClient(
        [schema.AllowedOutcome(outcome="selected", option_id="allow-always")]
    )
    bridge = AcpInputBridge(client, "s1", None)
    pending = bridge.describe(
        _request(
            kind="confirm",
            remember_pattern="rm -rf build",
            subject=_permission_subject_command(),
        )
    )
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered == {"value": None, "approved": True, "remember": True}


def test_a_dangerous_command_is_never_offered_allow_always():
    """Kvist hands no pattern for an intrinsic DANGEROUS command, so the
    "always" option must not exist — otherwise it could be persisted."""
    client = RecordingClient(
        [schema.AllowedOutcome(outcome="selected", option_id="reject-once")]
    )
    bridge = AcpInputBridge(client, "s1", None)
    pending = bridge.describe(
        _request(
            kind="confirm",
            remember_pattern=None,
            subject=_permission_subject_command(),
        )
    )
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    options = client.calls[0][2]
    assert [option.kind for option in options] == ["allow_once", "reject_once"]
    assert answered["approved"] is False


def test_rejection_fails_closed():
    client = RecordingClient([schema.DeniedOutcome(outcome="cancelled")])
    bridge = AcpInputBridge(client, "s1", None)
    pending = bridge.describe(_request(kind="confirm", remember_pattern="p"))
    answered = {"approved": "unset"}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered["approved"] is False


def test_a_client_failure_fails_closed_rather_than_hanging():
    class ExplodingClient:
        async def request_permission(self, *args, **kwargs):
            raise RuntimeError("client went away")

    bridge = AcpInputBridge(ExplodingClient(), "s1", None)
    pending = bridge.describe(_request(kind="confirm", remember_pattern="p"))
    answered = {"approved": "unset"}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered["approved"] is False


def test_secrets_are_never_requested_through_form_elicitation():
    class ForbiddenClient:
        async def create_elicitation(self, *args, **kwargs):
            raise AssertionError("secret elicitation must not be attempted")

    bridge = AcpInputBridge(ForbiddenClient(), "s1", schema.ClientCapabilities())
    pending = bridge.describe(_request(kind="secret", title="API key"))
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered["approved"] is False


def test_text_elicitation_is_refused_when_the_client_has_no_form_support():
    class ForbiddenClient:
        async def create_elicitation(self, *args, **kwargs):
            raise AssertionError("must not request a mode the client did not advertise")

    bridge = AcpInputBridge(ForbiddenClient(), "s1", schema.ClientCapabilities())
    pending = bridge.describe(_request(kind="text", title="Name"))
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered["approved"] is False


def test_choice_elicitation_round_trips_through_the_client():
    class AcceptingClient:
        async def create_elicitation(self, message, mode, **kwargs):
            assert isinstance(mode, schema.ElicitationFormSessionMode)
            return schema.AcceptElicitationResponse(
                content={"value": "coder"}, action="accept"
            )

    capabilities = schema.ClientCapabilities(
        elicitation=schema.ElicitationCapabilities(form={})
    )
    bridge = AcpInputBridge(AcceptingClient(), "s1", capabilities)
    pending = bridge.describe(
        _request(
            kind="choice",
            title="Role",
            options=[{"value": "coder"}, {"value": "reader"}],
        )
    )
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered["value"] == "coder"
    assert answered["approved"] is True


_PLAN_OPTIONS = [
    {"value": "plan", "label": "Plan first (safer)"},
    {"value": "start", "label": "Start right away (faster, riskier)"},
]


def test_choice_form_titles_every_option_with_its_label():
    class AcceptingClient:
        async def create_elicitation(self, message, mode, **kwargs):
            titled = mode.requested_schema.properties["value"].one_of
            assert [(item.const, item.title) for item in titled] == [
                ("plan", "Plan first (safer)"),
                ("start", "Start right away (faster, riskier)"),
            ]
            return schema.AcceptElicitationResponse(content={"value": "start"}, action="accept")

    capabilities = schema.ClientCapabilities(
        elicitation=schema.ElicitationCapabilities(form={})
    )
    bridge = AcpInputBridge(AcceptingClient(), "s1", capabilities)
    pending = bridge.describe(_request(kind="choice", title="Plan first?", options=_PLAN_OPTIONS))
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered["value"] == "start"


def test_a_two_option_choice_without_form_support_becomes_permission_buttons():
    client = RecordingClient([schema.AllowedOutcome(outcome="selected", option_id="start")])
    bridge = AcpInputBridge(client, "s1", schema.ClientCapabilities())
    pending = bridge.describe(_request(kind="choice", title="Plan first?", options=_PLAN_OPTIONS))
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    _session, tool_call, options = client.calls[0]
    assert tool_call.title == "Plan first?"
    assert [(item.option_id, item.name, item.kind) for item in options] == [
        ("plan", "Plan first (safer)", "allow_once"),
        ("start", "Start right away (faster, riskier)", "reject_once"),
    ]
    assert answered == {"value": "start", "approved": True}


def test_a_cancelled_permission_choice_gives_no_answer():
    client = RecordingClient([schema.DeniedOutcome(outcome="cancelled")])
    bridge = AcpInputBridge(client, "s1", None)
    pending = bridge.describe(_request(kind="choice", title="Plan first?", options=_PLAN_OPTIONS))
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered == {"value": None, "approved": False}


def test_a_longer_choice_without_form_support_is_still_refused():
    client = RecordingClient([])
    bridge = AcpInputBridge(client, "s1", None)
    options = [{"value": name, "label": name} for name in ("a", "b", "c")]
    pending = bridge.describe(_request(kind="choice", title="Pick", options=options))
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert client.calls == []
    assert answered["approved"] is False


def test_a_declined_elicitation_refuses_the_request():
    class DecliningClient:
        async def create_elicitation(self, message, mode, **kwargs):
            return schema.DeclineElicitationResponse(action="decline")

    capabilities = schema.ClientCapabilities(
        elicitation=schema.ElicitationCapabilities(form={})
    )
    bridge = AcpInputBridge(DecliningClient(), "s1", capabilities)
    pending = bridge.describe(_request(kind="text", title="Name"))
    answered = {}

    run(bridge.handle(pending, lambda **kw: answered.update(kw)))

    assert answered["approved"] is False


def test_replayed_prompts_come_back_as_user_message_chunks():
    """Without this a reloaded thread shows answers with no questions."""
    translator = AcpEventTranslator()

    updates = translator.translate(SimpleNamespace(type="user", text="do the thing"))

    assert [u.kind for u in updates] == ["user_message_chunk"]
    assert updates[0].update.content.text == "do the thing"


# --- /terminal and the advertised command set ---------------------------------


def test_terminal_launch_argv_drops_the_acp_only_flags(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["kvist", "--acp", "--login", "--workspace", "/tmp/w"]
    )
    monkeypatch.setattr(sys, "executable", "/opt/kvist/bin/kvist")
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert terminal_launch_argv() == ("/opt/kvist/bin/kvist", ["--workspace", "/tmp/w"])


def test_terminal_launch_argv_from_a_source_checkout_names_the_script(monkeypatch):
    monkeypatch.setattr(
        sys, "argv", ["/home/u/Kvist/main.py", "--acp", "--role", "auto"]
    )
    monkeypatch.setattr(sys, "executable", "/home/u/Kvist/.venv/bin/python")
    monkeypatch.delattr(sys, "frozen", raising=False)

    command, args = terminal_launch_argv()

    assert command == "/home/u/Kvist/.venv/bin/python"
    assert args == ["/home/u/Kvist/main.py", "--role", "auto"]


class TerminalRecorder:
    """Stands in for ``Client.create_terminal`` and records the request."""

    def __init__(self, terminal_id: str = "t-9", error: Exception | None = None) -> None:
        self.terminal_id = terminal_id
        self.error = error
        self.calls: list[tuple] = []

    async def __call__(self, session_id, command, args=None, **kwargs):
        self.calls.append((session_id, command, args, kwargs))
        if self.error is not None:
            raise self.error
        return schema.CreateTerminalResponse(terminal_id=self.terminal_id)


def _prompt(session_id: str, text: str) -> dict:
    return {
        "sessionId": session_id,
        "prompt": [{"type": "text", "text": text}],
    }


def test_terminal_command_reports_a_launch_line_when_the_client_cannot_open_one(
    tmp_path,
):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize(terminal=False)
            session_id = await wire.open_session(str(tmp_path))
            recorder = TerminalRecorder()
            wire.agent._client.create_terminal = recorder

            response = await wire.call("session/prompt", _prompt(session_id, "/terminal"))

            assert "result" in response, response
            assert recorder.calls == []
            text = "".join(wire.agent_text())
            assert "cannot open a terminal" in text
            assert sys.executable in text

    run(scenario())


def test_terminal_command_opens_a_terminal_in_the_client(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            recorder = TerminalRecorder()
            wire.agent._client.create_terminal = recorder

            response = await wire.call("session/prompt", _prompt(session_id, "/terminal"))

            assert "result" in response, response
            assert len(recorder.calls) == 1
            called_session, command, args, kwargs = recorder.calls[0]
            assert called_session == session_id
            assert (command, args) == terminal_launch_argv()
            assert "--acp" not in args and "--login" not in args
            assert kwargs.get("cwd") == str(tmp_path)
            assert "t-9" in "".join(wire.agent_text())

    run(scenario())


def test_a_terminal_the_client_refuses_to_open_is_reported_not_hidden(tmp_path):
    async def scenario():
        async with Wire() as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            recorder = TerminalRecorder(error=RuntimeError("no pty here"))
            wire.agent._client.create_terminal = recorder

            response = await wire.call("session/prompt", _prompt(session_id, "/terminal"))

            assert "result" in response, response
            assert len(recorder.calls) == 1
            assert "Could not open a terminal" in "".join(wire.agent_text())

    run(scenario())


def test_every_advertised_command_is_executable():
    """The picker must never offer a command that would fail when chosen."""
    from kernel import main
    advertised = dict(available_commands())
    handlers = set(main.STDIO_COMMANDS)

    assert set(advertised) == handlers | set(ACP_ONLY_COMMANDS)
    assert handlers.isdisjoint(ACP_ONLY_COMMANDS)
    assert all(description.strip() for description in advertised.values())
    assert {"commit-message", "terminal"} <= set(advertised)


def test_a_closed_session_refuses_a_prompt_without_a_model_call(tmp_path):
    """The core's lock holds over ACP too, even for an agent built on another session."""
    from kernel.plan import end_session
    from kernel.agent.agent import LocalAgent
    from kernel.agent.agent_state import AgentConfig
    from kernel.sandbox.executor import SafeExecutor
    from kernel.session.session_store import SessionStore
    from kernel.tests.conftest import FakeLLMClient, NullPerfLogger, NullUI

    seed_history(tmp_path, "chat-1", [("you useless piece of junk", "closed")])
    closer = LocalAgent(
        client=FakeLLMClient(), executor=SafeExecutor(tmp_path), config=AgentConfig(), ui=NullUI()
    )
    closer._session_history = [{"goal": "you useless piece of junk", "role": "coder", "result": "closed"}]
    closer._closure = {"closed_at": 1.0, "closed_msg_id": 1, "language": "en"}
    closer.save_session()
    # The ACP agent is built on the workspace's active session, which is not chat-1.
    SessionStore(tmp_path).create("chat-2")
    client = FakeLLMClient()

    def build(executor, **kwargs):
        config = AgentConfig(persist_logs=False, end_session_enabled=kwargs.get("end_session", False))
        agent = LocalAgent(client=client, executor=executor, config=config, ui=kwargs["ui"])
        agent._perf_logger = NullPerfLogger()
        return agent, config, []

    async def scenario():
        async with Wire(build_agent_fn=build) as wire:
            await wire.initialize()
            session_id = acp_session_id(tmp_path, "chat-1")
            opened = await wire.call(
                "session/resume",
                {"cwd": str(tmp_path), "sessionId": session_id, "mcpServers": [], "additionalDirectories": []},
            )
            assert "result" in opened, opened

            response = await wire.call("session/prompt", _prompt(session_id, "hello again"))

            assert "result" in response, response
            assert end_session.closed_reply("en") in "".join(wire.agent_text())
            # Not "refusal": Zed answers that by truncating the thread back to
            # before the person's message, which would erase this very reply.
            assert response["result"]["stopReason"] == "end_turn"
            assert client.requests == []

    run(scenario())


# --- Session switching ---------------------------------------------------------


def _real_agent_builder(client):
    """``build_agent`` stand-in that returns a real LocalAgent on a shared fake client."""
    from kernel.agent.agent import LocalAgent
    from kernel.agent.agent_state import AgentConfig
    from kernel.tests.conftest import NullPerfLogger, NullUI

    def build(executor, **kwargs):
        config = AgentConfig(
            persist_logs=False,
            api_request_interval=0,
            retry_base_delay=0,
            cache_enabled=False,
        )
        agent = LocalAgent(client=client, executor=executor, config=config, ui=NullUI())
        agent._perf_logger = NullPerfLogger()
        return agent, FakeConfig(role="chat"), []

    return build


def _seed_chat_session(workspace, name, marker):
    """Persist a session document whose chat history carries ``marker``."""
    from kernel.agent.agent import LocalAgent
    from kernel.agent.agent_state import AgentConfig
    from kernel.sandbox.executor import SafeExecutor
    from kernel.tests.conftest import FakeLLMClient, NullPerfLogger, NullUI

    agent = LocalAgent(
        client=FakeLLMClient(),
        executor=SafeExecutor(workspace),
        config=AgentConfig(persist_logs=False),
        ui=NullUI(),
    )
    agent._perf_logger = NullPerfLogger()
    agent.new_session(name)
    agent._chat_history = [
        {"role": "user", "content": f"question {marker}"},
        {"role": "assistant", "content": f"answer {marker}"},
    ]
    agent._session_history = [
        {"role": "coder", "goal": f"goal {marker}", "result": f"result {marker}"}
    ]
    agent.save_session()


def test_load_of_another_session_keeps_the_active_sessions_history_out_of_the_model(tmp_path):
    from kernel.tests.conftest import FakeLLMClient

    _seed_chat_session(tmp_path, "alpha", "ALPHA-MARKER")
    _seed_chat_session(tmp_path, "beta", "BETA-MARKER")
    # The workspace's active session is the one saved last; make it alpha.
    from kernel.session.session_store import SessionStore

    SessionStore(tmp_path).switch("alpha")
    client = FakeLLMClient(content="chat", stream_chunks=["ok"])

    async def scenario():
        async with Wire(build_agent_fn=_real_agent_builder(client)) as wire:
            await wire.initialize()
            response = await wire.call(
                "session/load",
                {
                    "cwd": str(tmp_path),
                    "sessionId": acp_session_id(tmp_path, "beta"),
                    "mcpServers": [],
                    "additionalDirectories": [],
                },
            )
            assert "result" in response, response
            prompt = await wire.call(
                "session/prompt",
                {
                    "sessionId": acp_session_id(tmp_path, "beta"),
                    "prompt": [{"type": "text", "text": "hello there"}],
                },
            )
            assert "result" in prompt, prompt

    run(scenario())

    sent = json.dumps(client.requests, default=str)
    assert "BETA-MARKER" in sent
    assert "ALPHA-MARKER" not in sent


def test_new_session_keeps_the_active_sessions_history_out_of_the_model(tmp_path):
    from kernel.session.session_store import SessionStore
    from kernel.tests.conftest import FakeLLMClient

    _seed_chat_session(tmp_path, "alpha", "ALPHA-MARKER")
    SessionStore(tmp_path).switch("alpha")
    client = FakeLLMClient(content="chat", stream_chunks=["ok"])

    async def scenario():
        async with Wire(build_agent_fn=_real_agent_builder(client)) as wire:
            await wire.initialize()
            session_id = await wire.open_session(str(tmp_path))
            prompt = await wire.call(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": "hello there"}]},
            )
            assert "result" in prompt, prompt

    run(scenario())

    assert client.requests
    assert "ALPHA-MARKER" not in json.dumps(client.requests, default=str)


def test_overwrite_approval_carries_a_before_after_diff_for_zed():
    client = RecordingClient(
        [schema.AllowedOutcome(outcome="selected", option_id="reject-once")]
    )
    bridge = AcpInputBridge(client, "s1", None)
    diff = "--- a/c.py\n+++ b/c.py\n@@ -1,2 +1,2 @@\n import os\n-DEBUG = False\n+DEBUG = True"
    pending = bridge.describe(
        _request(
            title="File overwrite",
            body="The agent wants to overwrite c.py:\n\n" + diff,
            subject={"kind": "overwrite", "path": "c.py", "diff": diff},
        )
    )

    run(bridge.handle(pending, lambda **kw: None))

    _, tool_call, _ = client.calls[-1]
    block = tool_call.content[0]
    assert (block.path, block.old_text, block.new_text) == (
        "c.py",
        "import os\nDEBUG = False",
        "import os\nDEBUG = True",
    )
