# -*- coding: utf-8 -*-
"""In-process slash commands over ``--frontend stdio-multi``.

Two layers, both without a network or a real provider: the transport
(``command`` in, dialogs as ``input_request``, ``command_result`` out) driven
with scripted handlers, and the real ``main.STDIO_COMMANDS`` handlers driven
through an ``EventStreamUI`` with a HOME redirected to a temp directory.
"""
from __future__ import annotations

import json
import threading
import time

import pytest

import event_stdio
import main
from agent_state import AgentConfig
from event_session import EventSession
from event_stdio import CommandContext, CommandResult
from event_stream_ui import EventStreamUI
from frontend_protocol import FrontendChannel, InputRequestEvent
from tests.test_event_stdio_multi import _Collector, _ScriptedReader


class _Agent:
    """Fake agent that records the role each goal was submitted with."""

    def __init__(self) -> None:
        self.config = AgentConfig()
        self.current_session = "default"
        self.goals: list[tuple[str, str | None]] = []
        self.release = threading.Event()
        self.release.set()
        self.own_checkpoint = False
        self.holder: str | None = None
        self.resumed = 0

    def _peek_checkpoint(self):
        return {"goal": "g"} if self.own_checkpoint else None

    def _find_resumable_session(self):
        return self.holder

    def resume(self) -> str:
        self.resumed += 1
        self.release.wait(3.0)
        return "resumed"

    def request_cancel(self) -> None:
        pass

    def run(self, user_goal: str, routed_role: str | None = None) -> str:
        self.goals.append((user_goal, routed_role))
        self.release.wait(3.0)
        return f"done {user_goal}"


def _serve(commands, agent=None):
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    agent = agent or _Agent()
    session = EventSession(channel, agent)
    reader = _ScriptedReader()
    out = _Collector()
    thread = threading.Thread(
        target=event_stdio.serve_session_multi,
        kwargs={
            "session": session,
            "out_stream": out,
            "in_reader": reader,
            "commands": commands,
            "ui": ui,
        },
        daemon=True,
    )
    thread.start()
    return agent, reader, out, thread


def _command(name: str, *args: str) -> str:
    return json.dumps({"type": "command", "name": name, "args": list(args)})


def _result(out: _Collector, name: str) -> dict:
    return out.wait_for(lambda e: e["type"] == "command_result" and e["name"] == name)


def _finish(reader: _ScriptedReader, thread: threading.Thread) -> None:
    reader.eof()
    thread.join(timeout=3.0)
    assert not thread.is_alive()


# --- transport ---------------------------------------------------------------


def test_command_dialog_streams_as_input_request_then_command_result():
    def pick(ctx: CommandContext) -> CommandResult:
        chosen = ctx.ui.choose_option("Pick", [{"value": "a", "label": "A"}])
        return CommandResult(True, f"picked {chosen}", {"picked": chosen})

    agent, reader, out, thread = _serve({"pick": pick})

    reader.push(_command("pick"))
    request = out.wait_for(lambda e: e["type"] == "input_request")
    assert request["kind"] == "choice"
    reader.push(json.dumps({"request_id": request["request_id"], "value": "a"}))

    result = _result(out, "pick")
    assert result == {
        "type": "command_result",
        "name": "pick",
        "ok": True,
        "message": "picked a",
        "effects": {"picked": "a"},
    }
    _finish(reader, thread)
    types = [e["type"] for e in out.events()]
    assert types == ["hello", "input_request", "command_result"]
    assert agent.goals == []


def test_command_name_is_case_and_slash_insensitive():
    agent, reader, out, thread = _serve({"ping": lambda ctx: CommandResult(True, "pong")})
    reader.push(_command("/PING"))
    assert _result(out, "ping")["message"] == "pong"
    _finish(reader, thread)


def test_unknown_command_is_refused_and_process_survives():
    agent, reader, out, thread = _serve({})
    reader.push(_command("nope"))
    result = _result(out, "nope")
    assert result["ok"] is False
    assert "Unknown command" in result["message"]

    reader.push("still alive")
    out.wait_for(lambda e: e["type"] == "turn_end")
    assert agent.goals == [("still alive", None)]
    _finish(reader, thread)


@pytest.mark.parametrize(
    "line",
    [
        json.dumps({"type": "command"}),
        json.dumps({"type": "command", "name": "  "}),
        json.dumps({"type": "command", "name": "x", "args": "coder"}),
        json.dumps({"type": "command", "name": "x", "args": [1]}),
    ],
)
def test_malformed_command_is_refused_not_treated_as_a_goal(line):
    agent, reader, out, thread = _serve({"x": lambda ctx: CommandResult(True, "ran")})
    reader.push(line)
    result = out.wait_for(lambda e: e["type"] == "command_result")
    assert result["ok"] is False
    _finish(reader, thread)
    assert agent.goals == []


def test_command_during_a_running_turn_is_refused_without_disturbing_it():
    agent, reader, out, thread = _serve({"ping": lambda ctx: CommandResult(True, "pong")})
    agent.release.clear()

    reader.push("long goal")
    deadline = time.time() + 3.0
    while not agent.goals and time.time() < deadline:
        time.sleep(0.01)
    reader.push(_command("ping"))
    refused = _result(out, "ping")
    assert refused["ok"] is False
    assert "running" in refused["message"]

    agent.release.set()
    out.wait_for(lambda e: e["type"] == "turn_end")
    reader.push(_command("ping"))
    out.wait_for(lambda e: e["type"] == "command_result" and e["ok"] is True)
    _finish(reader, thread)


def test_a_command_ends_with_command_result_not_turn_end():
    agent, reader, out, thread = _serve({"ping": lambda ctx: CommandResult(True, "pong")})
    reader.push(_command("ping"))
    _result(out, "ping")
    _finish(reader, thread)
    assert "turn_end" not in [e["type"] for e in out.events()]


def test_handler_can_change_the_role_of_following_goals():
    def set_role(ctx: CommandContext) -> CommandResult:
        ctx.routed_role = ctx.args[0]
        return CommandResult(True, "ok", {"role": ctx.args[0]})

    agent, reader, out, thread = _serve({"role": set_role})
    reader.push("before")
    out.wait_for(lambda e: e["type"] == "turn_end")
    reader.push(_command("role", "coder"))
    assert _result(out, "role")["effects"] == {"role": "coder"}
    reader.push("after")
    out.wait_for(lambda e: e["type"] == "turn_end" and [x["type"] for x in out.events()].count("turn_end") == 2)
    _finish(reader, thread)
    assert agent.goals == [("before", None), ("after", "coder")]


def test_handler_crash_becomes_a_failed_result_and_the_process_continues():
    def boom(ctx: CommandContext) -> CommandResult:
        raise OSError("disk on fire")

    agent, reader, out, thread = _serve({"boom": boom})
    reader.push(_command("boom"))
    result = _result(out, "boom")
    assert result["ok"] is False
    assert "disk on fire" in result["message"]

    reader.push("next")
    out.wait_for(lambda e: e["type"] == "turn_end")
    _finish(reader, thread)


def test_secret_answer_never_travels_outbound():
    typed = "sk-supersecretvalue1234567890abcdef"

    def ask(ctx: CommandContext) -> CommandResult:
        value = ctx.ui.prompt_secret("Key", current_value="old-secret-value")
        assert value == typed
        return CommandResult(True, f"stored {value}", {"echo": value})

    agent, reader, out, thread = _serve({"ask": ask})
    reader.push(_command("ask"))
    request = out.wait_for(lambda e: e["type"] == "input_request")
    assert request["kind"] == "secret"
    assert request["current_value"] == ""
    reader.push(json.dumps({"request_id": request["request_id"], "value": typed}))
    result = _result(out, "ask")
    _finish(reader, thread)

    assert result["ok"] is True
    assert not any(typed in line or "old-secret-value" in line for line in out.raw())


def test_stdin_eof_during_a_dialog_cancels_it_and_ends_the_process():
    seen: list = []

    def ask(ctx: CommandContext) -> CommandResult:
        seen.append(ctx.ui.prompt_text("Name?"))
        return CommandResult(True, "done")

    agent, reader, out, thread = _serve({"ask": ask})
    reader.push(_command("ask"))
    out.wait_for(lambda e: e["type"] == "input_request")
    _finish(reader, thread)
    assert seen == [None]


# --- real handlers -----------------------------------------------------------


@pytest.fixture
def home(tmp_path, monkeypatch):
    home_dir = tmp_path / "home"
    home_dir.mkdir()
    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.delenv("AGENT_REASONING_EFFORT", raising=False)
    return home_dir


def _run_handler(name, agent, args=(), answers=(), rebuild=None):
    """Run ``main.STDIO_COMMANDS[name]`` answering each dialog in order.

    Returns ``(result, requests, ctx)``; ``answers`` are values, or callables
    taking the request event.
    """
    channel = FrontendChannel()
    ui = EventStreamUI(channel)
    session = EventSession(channel, agent)
    ctx = CommandContext(session, ui, rebuild, None, list(args))
    box: list[CommandResult] = []
    worker = threading.Thread(
        target=lambda: box.append(main.STDIO_COMMANDS[name](ctx)), daemon=True
    )
    worker.start()

    requests: list[InputRequestEvent] = []
    pending = list(answers)
    deadline = time.time() + 5.0
    while worker.is_alive() and time.time() < deadline:
        event = channel.get(timeout=0.02)
        if not isinstance(event, InputRequestEvent):
            continue
        requests.append(event)
        answer = pending.pop(0)
        value = answer(event) if callable(answer) else answer
        if event.kind == "confirm":
            channel.respond(event.request_id, approved=value)
        else:
            channel.respond(event.request_id, value=value)
    worker.join(timeout=1.0)
    assert box, "handler did not finish"
    return box[0], requests, ctx


def _saved(key):
    from user_config import load_user_config

    return load_user_config().get(key)


def test_role_wizard_saves_and_reroutes(home, agent_factory):
    agent = agent_factory()
    result, requests, ctx = _run_handler("role", agent, answers=["coder"])

    assert [r.kind for r in requests] == ["choice"]
    assert {o["value"] for o in requests[0].options} == set(main._ROLE_CHOICES)
    assert result.ok and result.effects == {"role": "coder"}
    assert ctx.routed_role == "coder"
    assert _saved("role_selection") == "coder"


def test_role_argument_skips_the_dialog_and_auto_clears_the_route(home, agent_factory):
    agent = agent_factory()
    result, requests, ctx = _run_handler("role", agent, args=["AUTO"])
    assert requests == []
    assert result.effects == {"role": "auto"} and ctx.routed_role is None


def test_role_rejects_an_unknown_name_and_a_cancelled_dialog_changes_nothing(home, agent_factory):
    agent = agent_factory()
    result, _, _ = _run_handler("role", agent, args=["wizard"])
    assert result.ok is False

    result, _, ctx = _run_handler("role", agent, answers=[None])
    assert result.ok and result.effects == {"cancelled": True}
    assert ctx.routed_role is None
    assert _saved("role_selection") is None


def test_effort_wizard_applies_and_persists(home, agent_factory):
    agent = agent_factory()
    result, requests, _ = _run_handler("effort", agent, answers=["high"])
    assert requests[0].current_value == "medium"
    assert result.ok and result.effects["effort"] == "high"
    assert agent.config.reasoning_effort == "high"
    assert _saved("reasoning_effort") == "high"


def test_effort_rejects_an_unknown_level(home, agent_factory):
    result, _, _ = _run_handler("effort", agent_factory(), args=["ludicrous"])
    assert result.ok is False


def test_plan_toggles_and_takes_an_explicit_value(home, agent_factory):
    agent = agent_factory()
    result, _, _ = _run_handler("plan", agent)
    assert result.effects == {"plan": False}
    assert agent.config.skip_planning is True
    assert _saved("planning_enabled") is False

    result, _, _ = _run_handler("plan", agent, args=["true"])
    assert result.effects == {"plan": True}
    assert agent.config.skip_planning is False

    assert _run_handler("plan", agent, args=["maybe"])[0].ok is False


def test_verbose_toggles_the_runtime_flag_only(home, agent_factory):
    agent = agent_factory()
    result, _, _ = _run_handler("verbose", agent, args=["true"])
    assert result.effects == {"verbose": True}
    assert agent.config.verbose is True and agent._ui.verbose is True
    assert _run_handler("verbose", agent, args=["false"])[0].effects == {"verbose": False}


def test_style_wizard_asks_mode_then_style(home, agent_factory):
    from user_config import get_style_options

    agent = agent_factory()
    name = list(get_style_options("agents"))[1]
    result, requests, _ = _run_handler("style", agent, answers=["agents", name])

    assert [r.title for r in requests][0] == "Style for which mode?"
    assert result.ok
    assert result.effects["style"]["type"] == "agents"
    assert result.effects["style"]["name"] == name
    assert result.effects["style"]["applied_to_session"] is True
    assert _saved("styles")["agents"] == name


def test_style_arguments_validate_the_name(home, agent_factory):
    result, _, _ = _run_handler("style", agent_factory(), args=["agents", "No-Such-Style"])
    assert result.ok is False


def test_session_new_switch_and_delete_by_arguments(home, agent_factory):
    agent = agent_factory()
    original = agent.current_session
    # An untouched session is never written, so it could not be switched back to.
    agent._session_history.append({"goal": "g", "role": "chat", "result": "r"})
    agent.save_session()

    result, requests, _ = _run_handler("session", agent, args=["new", "alpha"])
    assert requests == []
    assert result.ok and result.effects["session"] == "alpha"
    assert result.effects["changed"] is True
    assert agent.current_session == "alpha"

    result, _, _ = _run_handler("session", agent, args=["switch", original])
    assert result.ok and agent.current_session == original

    result, _, _ = _run_handler("session", agent, args=["delete", original])
    assert result.ok is False and "active" in result.message

    result, _, _ = _run_handler("session", agent, args=["delete", "alpha"])
    assert result.ok and "alpha" not in result.effects["sessions"]

    result, _, _ = _run_handler("session", agent, args=["switch", "ghost"])
    assert result.ok is False


def test_session_wizard_creates_a_session_through_dialogs(home, agent_factory):
    agent = agent_factory()
    result, requests, _ = _run_handler("session", agent, answers=["__new__", "beta"])
    assert [r.kind for r in requests] == ["choice", "text"]
    assert result.ok and agent.current_session == "beta"


def test_new_starts_a_session_and_keeps_the_old_one(home, agent_factory):
    agent = agent_factory()
    agent.new_session("keep-me")
    agent._session_history.append({"goal": "g", "role": "chat", "result": "r"})
    agent.save_session()
    result, _, _ = _run_handler("new", agent)
    assert result.ok and result.effects["changed"] is True
    assert "keep-me" in result.effects["sessions"]


def test_rename_sets_the_title_and_blocks_autogeneration(home, agent_factory):
    agent = agent_factory()
    calls = []
    agent._start_title_generation = lambda *args: calls.append(args)

    result, _, _ = _run_handler("rename", agent, args=["My", "custom", "title"])

    assert result.ok
    assert result.effects["title"] == "My custom title"
    assert agent._session_title == "My custom title"

    agent._add_to_session_history("do something", "coder", "done")
    assert calls == []


def test_rename_without_a_title_is_refused(home, agent_factory):
    agent = agent_factory()
    result, _, _ = _run_handler("rename", agent, args=[])
    assert result.ok is False


def test_models_wizard_rebuilds_the_agent_and_persists(home, agent_factory, monkeypatch):
    agent = agent_factory()
    options = [{"value": "gpt-5.4", "label": "a"}, {"value": "gpt-5.4-mini", "label": "b"}]
    monkeypatch.setattr(main, "_model_picker_options", lambda provider: (options, False))
    replacement = agent_factory()
    calls: list[dict] = []

    def rebuild(provider_override=None, models_override=None):
        calls.append({"provider": provider_override, "models": models_override})
        replacement.config.fast_model = models_override["fast"]
        replacement.config.smart_model = models_override["smart"]
        replacement.config.planner_model = models_override["planner"]
        return replacement, replacement.config, ["a warning"]

    result, requests, ctx = _run_handler(
        "models", agent, answers=["gpt-5.4-mini", "gpt-5.4", "gpt-5.4"], rebuild=rebuild
    )

    assert len(requests) == 3
    assert calls == [
        {
            "provider": "openai",
            "models": {"fast": "gpt-5.4-mini", "smart": "gpt-5.4", "planner": "gpt-5.4"},
        }
    ]
    assert result.ok
    assert result.effects["models"]["fast"] == "gpt-5.4-mini"
    assert result.effects["warnings"] == ["a warning"]
    assert ctx.session.agent is replacement
    assert _saved("llm_models")["openai"]["fast"] == "gpt-5.4-mini"


def test_models_refuses_an_invalid_selection_without_rebuilding(home, agent_factory):
    def rebuild(**kwargs):
        raise AssertionError("must not rebuild")

    result, _, _ = _run_handler(
        "models", agent_factory(), args=["", "gpt-5.4", "gpt-5.4"], rebuild=rebuild
    )
    assert result.ok is False


def test_models_cancel_and_bad_usage(home, agent_factory, monkeypatch):
    monkeypatch.setattr(
        main, "_model_picker_options", lambda provider: ([{"value": "gpt-5.4", "label": "a"}], False)
    )
    result, _, _ = _run_handler("models", agent_factory(), answers=[None])
    assert result.effects == {"cancelled": True}
    assert _run_handler("models", agent_factory(), args=["only-one"])[0].ok is False


def test_changes_reports_an_empty_journal(home, agent_factory):
    result, _, _ = _run_handler("changes", agent_factory())
    assert result.ok and isinstance(result.message, str)


def test_bug_saves_a_report_and_returns_its_path(home, agent_factory):
    agent = agent_factory()
    result, _, _ = _run_handler("bug", agent, args=["crashed", "on", "save"])

    assert result.ok
    saved_path = home / ".kvist" / "bug-reports"
    assert saved_path.is_dir()
    reports = [p for p in saved_path.iterdir() if not p.name.startswith(".")]
    assert len(reports) == 1
    assert "crashed on save" in reports[0].read_text(encoding="utf-8")
    assert str(reports[0]) in result.message


def test_every_registered_command_has_a_handler_with_the_shared_signature():
    assert set(main.STDIO_COMMANDS) == {
        "role", "effort", "plan", "verbose", "style", "session", "new", "rename", "models",
        "changes", "provider", "change-api", "del-provider", "full-reset", "settings-api",
        "strime", "revert", "bug",
    }
    for handler in main.STDIO_COMMANDS.values():
        assert callable(handler)


def test_real_handlers_over_the_wire_change_the_role_of_the_next_goal(home):
    agent, reader, out, thread = _serve(main.STDIO_COMMANDS)

    reader.push(_command("role"))
    request = out.wait_for(lambda e: e["type"] == "input_request")
    assert request["title"] == "Select Agent Role"
    reader.push(json.dumps({"request_id": request["request_id"], "value": "devops"}))
    result = _result(out, "role")
    assert result["effects"] == {"role": "devops"}

    reader.push("deploy it")
    out.wait_for(lambda e: e["type"] == "turn_end")
    _finish(reader, thread)
    assert agent.goals == [("deploy it", "devops")]


# --- the remaining commands --------------------------------------------------


def test_home_is_isolated_for_every_config_writing_test(home):
    from pathlib import Path

    from user_config import get_user_config_path

    assert Path.home() == home
    assert home in get_user_config_path().parents


def _rebuilder(replacement, calls, error=None):
    def rebuild(provider_override=None, models_override=None, interactive=False):
        calls.append({"provider": provider_override, "interactive": interactive})
        if error is not None:
            raise error
        return replacement, replacement.config, ["w"]

    return rebuild


def test_provider_wizard_switches_through_an_interactive_rebuild(home, agent_factory):
    replacement = agent_factory(provider="anthropic")
    calls: list[dict] = []
    result, requests, ctx = _run_handler(
        "provider",
        agent_factory(),
        answers=["anthropic"],
        rebuild=_rebuilder(replacement, calls),
    )
    assert requests[0].title == "Select LLM Provider"
    assert calls == [{"provider": "anthropic", "interactive": True}]
    assert result.ok and result.effects["provider"] == "anthropic"
    assert result.effects["changed"] is True and result.effects["warnings"] == ["w"]
    assert ctx.session.agent is replacement


def test_provider_unchanged_cancelled_unknown_and_failed_build(home, agent_factory):
    def never(**kwargs):
        raise AssertionError("must not rebuild")

    same = _run_handler("provider", agent_factory(), answers=["openai"], rebuild=never)[0]
    assert same.ok and same.effects == {"provider": "openai", "changed": False}
    cancelled = _run_handler("provider", agent_factory(), answers=[None], rebuild=never)[0]
    assert cancelled.effects == {"cancelled": True}
    unknown = _run_handler("provider", agent_factory(), args=["nope"], rebuild=never)[0]
    assert unknown.ok is False

    calls: list[dict] = []
    failed, _, ctx = _run_handler(
        "provider",
        agent_factory(),
        args=["deepseek"],
        rebuild=_rebuilder(None, calls, RuntimeError("no key")),
    )
    assert failed.ok is False and "no key" in failed.message
    assert ctx.session.agent.config.provider == "openai"


def test_provider_can_create_a_custom_provider_through_dialogs(home, agent_factory):
    replacement = agent_factory(provider="custom:my-llm")
    calls: list[dict] = []
    answers = [
        main.CUSTOM_PROVIDER_PLACEHOLDER,  # picker
        "my-llm",  # id
        "openai",  # protocol
        "http://localhost:8080/v1",  # url
        "no",  # streaming
        "custom-secret-key",  # api key (secret)
        "My LLM",  # label
        "one",  # model mode
        "m1",  # model id
        "yes",  # create it
    ]
    result, requests, _ = _run_handler(
        "provider", agent_factory(), answers=answers, rebuild=_rebuilder(replacement, calls)
    )
    assert result.ok and calls == [{"provider": "custom:my-llm", "interactive": True}]
    assert "custom-secret-key" not in json.dumps([r.to_dict() for r in requests])
    assert "custom-secret-key" not in json.dumps(result.effects) + result.message
    assert "my-llm" in (_saved("custom_providers") or {})


def test_change_api_saves_the_secret_and_never_echoes_it(home, agent_factory):
    replacement = agent_factory()
    calls: list[dict] = []
    secret = "sk-test-secret-value"
    result, requests, _ = _run_handler(
        "change-api", agent_factory(), answers=[secret], rebuild=_rebuilder(replacement, calls)
    )
    assert [r.kind for r in requests] == ["secret"]
    assert result.ok and result.effects["key_saved"] is True
    assert calls == [{"provider": "openai", "interactive": True}]
    assert _saved("openai_api_key") == secret
    wire = json.dumps([r.to_dict() for r in requests]) + json.dumps(result.effects) + result.message
    assert secret not in wire


def test_change_api_empty_answer_keeps_the_key_and_skips_the_rebuild(home, agent_factory):
    def never(**kwargs):
        raise AssertionError("must not rebuild")

    result, _, _ = _run_handler("change-api", agent_factory(), answers=[""], rebuild=never)
    assert result.effects == {"cancelled": True}
    assert _saved("openai_api_key") is None


def test_change_api_reports_a_failed_apply_but_keeps_the_saved_key(home, agent_factory):
    result, _, _ = _run_handler(
        "change-api",
        agent_factory(),
        answers=["sk-x"],
        rebuild=_rebuilder(None, [], RuntimeError("rejected")),
    )
    assert result.ok is False and "rejected" in result.message
    assert result.effects == {"key_saved": True, "restart_required": True}
    assert _saved("openai_api_key") == "sk-x"


def _custom_agent(agent_factory, **overrides):
    from user_config import save_custom_provider

    save_custom_provider(
        "gw",
        {"label": "Gw", "base_url": "http://x/v1", "api_key": "k", "models": {"fast": "m", "smart": "m", "planner": "m"}},
    )
    return agent_factory(provider="custom:gw", **overrides)


def test_del_provider_needs_a_custom_provider(home, agent_factory):
    result, requests, _ = _run_handler("del-provider", agent_factory(), rebuild=_rebuilder(None, []))
    assert result.ok is False and requests == []


def test_del_provider_confirms_deletes_and_picks_a_replacement(home, agent_factory):
    from user_config import save_role_selection

    save_role_selection("coder")
    agent = _custom_agent(agent_factory)
    replacement = agent_factory(provider="openai")
    calls: list[dict] = []
    result, requests, ctx = _run_handler(
        "del-provider", agent, answers=[True], rebuild=_rebuilder(replacement, calls)
    )
    assert [r.kind for r in requests] == ["confirm"]
    assert calls == [{"provider": None, "interactive": True}]
    assert result.ok and result.effects["deleted"] == "gw" and result.effects["provider"] == "openai"
    assert "gw" not in (_saved("custom_providers") or {})
    assert _saved("llm_provider") is None
    assert _saved("role_selection") == "coder"
    assert ctx.session.agent is replacement


def test_del_provider_declined_deletes_nothing(home, agent_factory):
    for answer in (False, None):
        agent = _custom_agent(agent_factory)
        result, _, _ = _run_handler(
            "del-provider", agent, answers=[answer], rebuild=_rebuilder(None, [])
        )
        assert result.effects == {"cancelled": True}
        assert "gw" in _saved("custom_providers")


def test_del_provider_without_a_replacement_asks_for_a_restart(home, agent_factory):
    agent = _custom_agent(agent_factory)
    result, _, ctx = _run_handler(
        "del-provider",
        agent,
        answers=[True],
        rebuild=_rebuilder(None, [], RuntimeError("No provider was selected.")),
    )
    assert result.ok is False and "No provider was selected" in result.message
    assert result.effects == {"deleted": "gw", "restart_required": True}
    assert ctx.session.agent is agent


def test_full_reset_needs_two_explicit_yeses_then_wipes_the_config(home, agent_factory):
    from user_config import save_role_selection

    save_role_selection("coder")
    replacement = agent_factory()
    calls: list[dict] = []
    result, requests, ctx = _run_handler(
        "full-reset", agent_factory(), answers=[True, True], rebuild=_rebuilder(replacement, calls)
    )
    assert [r.kind for r in requests] == ["confirm", "confirm"]
    assert "Final confirmation" in requests[1].title
    assert result.ok and result.effects["reset"] is True
    assert calls == [{"provider": None, "interactive": True}]
    assert _saved("role_selection") is None
    assert ctx.session.agent is replacement


@pytest.mark.parametrize("answers", [[False], [None], [True, False], [True, None]])
def test_full_reset_is_fail_closed_on_any_non_yes(home, agent_factory, answers):
    from user_config import save_role_selection

    save_role_selection("coder")

    def never(**kwargs):
        raise AssertionError("must not rebuild")

    result, _, _ = _run_handler("full-reset", agent_factory(), answers=answers, rebuild=never)
    assert result.effects == {"cancelled": True}
    assert _saved("role_selection") == "coder"


def test_full_reset_with_no_provider_left_asks_for_a_restart(home, agent_factory):
    result, _, _ = _run_handler(
        "full-reset",
        agent_factory(),
        answers=[True, True],
        rebuild=_rebuilder(None, [], RuntimeError("cancelled")),
    )
    assert result.ok is False
    assert result.effects == {"reset": True, "restart_required": True}


def test_settings_api_edits_in_a_loop_and_reports_the_changed_keys(home, agent_factory):
    agent = agent_factory()
    result, requests, _ = _run_handler(
        "settings-api",
        agent,
        answers=["max_retries", "7", "response_cache", "off", main._API_SETTINGS_DONE],
    )
    assert [r.kind for r in requests] == ["choice", "text", "choice", "choice", "choice"]
    assert result.ok
    assert result.effects["changed_keys"] == ["max_retries", "response_cache"]
    assert result.effects["values"] == {"max_retries": 7, "response_cache": False}
    assert result.effects["restart_required"] is False
    assert _saved("api_settings") == {"max_retries": 7, "response_cache": False}


def test_settings_api_retries_an_invalid_number_and_flags_dynamic_models(home, agent_factory):
    result, requests, _ = _run_handler(
        "settings-api",
        agent_factory(),
        answers=["max_retries", "not a number", "3", "dynamic_models", "off", None],
    )
    assert "\n\n" in requests[2].help_text  # the validation error is shown on the retry
    assert result.effects["changed_keys"] == ["dynamic_models", "max_retries"]
    assert result.effects["restart_required"] is True


def test_settings_api_reset_all_is_confirmed_and_defaults_to_no(home, agent_factory):
    agent = agent_factory()
    result, requests, _ = _run_handler(
        "settings-api",
        agent,
        answers=["max_retries", "7", main._API_SETTINGS_RESET, "no", main._API_SETTINGS_RESET, "yes", None],
    )
    assert requests[3].current_value == "no"
    assert result.effects["changed_keys"] == ["max_retries"]
    assert not _saved("api_settings")


def test_settings_api_without_edits_changes_nothing(home, agent_factory):
    result, _, _ = _run_handler("settings-api", agent_factory(), answers=[None])
    assert result.ok and result.effects == {"changed_keys": []}


def test_strime_is_only_for_custom_providers(home, agent_factory):
    result, _, _ = _run_handler("strime", agent_factory())
    assert result.ok is False


def test_strime_toggles_and_persists_for_a_custom_provider(home, agent_factory):
    agent = _custom_agent(agent_factory, stream=False)
    result, requests, _ = _run_handler("strime", agent)
    assert requests == []
    assert result.ok and result.effects == {"stream": True}
    assert agent.config.stream is True
    assert _saved("custom_providers")["gw"]["streaming"] is True

    result, _, _ = _run_handler("strime", agent, args=["<false>"])
    assert result.effects == {"stream": False} and agent.config.stream is False
    assert _run_handler("strime", agent, args=["maybe"])[0].ok is False


def _fake_baseline(monkeypatch, paths):
    import change_journal

    monkeypatch.setattr(change_journal, "load_baseline", lambda workspace: {p: {} for p in paths})
    return change_journal


def test_revert_lists_the_files_in_the_confirmation_and_reports_the_outcome(home, agent_factory, monkeypatch):
    journal = _fake_baseline(monkeypatch, ["b.py", "a.py"])
    monkeypatch.setattr(
        journal,
        "revert_workspace",
        lambda workspace: [
            journal.RevertedFile("a.py", "restored"),
            journal.RevertedFile("b.py", "skipped", "changed outside"),
        ],
    )
    result, requests, _ = _run_handler("revert", agent_factory(), answers=[True])
    assert requests[0].kind == "confirm"
    assert requests[0].body.index("a.py") < requests[0].body.index("b.py")
    assert result.ok and "1" in result.message
    assert result.effects == {
        "reverted": [
            {"path": "a.py", "action": "restored", "reason": ""},
            {"path": "b.py", "action": "skipped", "reason": "changed outside"},
        ]
    }


@pytest.mark.parametrize("answer", [False, None])
def test_revert_declined_touches_nothing(home, agent_factory, monkeypatch, answer):
    journal = _fake_baseline(monkeypatch, ["a.py"])

    def never(workspace):
        raise AssertionError("must not revert")

    monkeypatch.setattr(journal, "revert_workspace", never)
    result, _, _ = _run_handler("revert", agent_factory(), answers=[answer])
    assert result.effects == {"cancelled": True}


def test_revert_with_nothing_tracked_or_a_corrupt_journal(home, agent_factory, monkeypatch):
    result, requests, _ = _run_handler("revert", agent_factory())
    assert result.ok and result.effects == {"reverted": []} and requests == []

    import change_journal

    def corrupt(workspace):
        raise change_journal.JournalUnavailableError("bad")

    monkeypatch.setattr(change_journal, "load_baseline", corrupt)
    result, requests, _ = _run_handler("revert", agent_factory())
    assert result.ok is False and requests == []


# --- resume over the wire ----------------------------------------------------


def test_resume_announces_the_session_then_streams_a_turn_and_ends_with_turn_end():
    agent = _Agent()
    agent.holder = "chat-old"
    agent, reader, out, thread = _serve(main.STDIO_COMMANDS, agent)

    reader.push(_command("resume"))
    out.wait_for(lambda e: e["type"] == "turn_end")
    _finish(reader, thread)
    events = out.events()
    assert [e["type"] for e in events] == ["hello", "session", "turn_end"]
    assert events[1] == {"type": "session", "session": "chat-old", "has_checkpoint": True}
    assert agent.resumed == 1 and agent.goals == []


def test_resume_in_the_active_session_names_that_session():
    agent = _Agent()
    agent.own_checkpoint = True
    agent, reader, out, thread = _serve(main.STDIO_COMMANDS, agent)
    reader.push(_command("resume"))
    session_event = out.wait_for(lambda e: e["type"] == "session")
    assert session_event["session"] == "default"
    _finish(reader, thread)


def test_resume_without_any_checkpoint_is_a_failed_result_and_no_turn():
    agent, reader, out, thread = _serve(main.STDIO_COMMANDS)
    reader.push(_command("resume"))
    result = _result(out, "resume")
    assert result["ok"] is False and "No saved checkpoint to resume" in result["message"]

    reader.push("next goal")
    out.wait_for(lambda e: e["type"] == "turn_end")
    _finish(reader, thread)
    assert [e["type"] for e in out.events()] == ["hello", "command_result", "turn_end"]
    assert agent.resumed == 0 and agent.goals == [("next goal", None)]


def test_a_command_during_a_resumed_turn_is_refused():
    agent = _Agent()
    agent.holder = "old"
    agent.release.clear()
    agent, reader, out, thread = _serve({"ping": lambda ctx: CommandResult(True, "pong")}, agent)
    reader.push(_command("resume"))
    out.wait_for(lambda e: e["type"] == "session")
    reader.push(_command("ping"))
    assert _result(out, "ping")["ok"] is False
    agent.release.set()
    out.wait_for(lambda e: e["type"] == "turn_end")
    _finish(reader, thread)


# --- hello and startup -------------------------------------------------------


def test_hello_carries_effort_planning_and_the_command_names():
    agent, reader, out, thread = _serve({"ping": lambda ctx: CommandResult(True, "pong")})
    hello = out.wait_for(lambda e: e["type"] == "hello")
    assert hello["effort"] == "medium"
    assert hello["planning"] is True
    assert hello["commands"] == ["ping", "resume"]
    _finish(reader, thread)


def test_hello_planning_is_false_when_the_agent_skips_planning():
    from event_stdio import build_session_hello

    agent = _Agent()
    agent.config.skip_planning = True
    assert build_session_hello(agent, commands=["x"]).to_dict()["planning"] is False


def test_planning_preference_and_env_override_the_startup_flag(home, monkeypatch):
    from user_config import save_planning_enabled

    monkeypatch.delenv("AGENT_SKIP_PLANNING", raising=False)
    assert main.resolve_planning_enabled() is True
    save_planning_enabled(False)
    assert main.resolve_planning_enabled() is False
    monkeypatch.setenv("AGENT_SKIP_PLANNING", "0")
    assert main.resolve_planning_enabled() is True
    monkeypatch.setenv("AGENT_SKIP_PLANNING", "1")
    assert main.resolve_planning_enabled() is False


def test_launch_multi_builds_the_agent_with_the_given_planning_flag(home, tmp_path):
    seen: dict = {}

    def fake_build(executor, **kwargs):
        seen.update(kwargs)
        agent = _Agent()
        agent.config.skip_planning = kwargs["skip_planning"]
        return agent, agent.config, []

    reader = _ScriptedReader()
    reader.eof()
    out = _Collector()
    event_stdio.launch_multi(
        str(tmp_path),
        fake_build,
        in_stream=reader,
        out_stream=out,
        provider_override="openai",
        skip_planning=True,
    )
    assert seen["skip_planning"] is True
    assert out.events()[0]["planning"] is False


class _ScriptedUI:
    """Answers each dialog from a list; records titles, like a frontend would."""

    def __init__(self, answers):
        self.answers = list(answers)
        self.titles: list[str] = []

    def _next(self, title):
        self.titles.append(title)
        return self.answers.pop(0)

    def choose_option(self, title, options, current_value="", help_text=""):
        return self._next(title)

    def prompt_text(self, title, current_value="", help_text=""):
        return self._next(title)

    def prompt_secret(self, title, current_value="", help_text=""):
        return self._next(title)


def test_startup_provider_is_asked_through_the_interactive_ui_without_a_tty(home, monkeypatch):
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    ui = _ScriptedUI(["deepseek"])
    assert main.resolve_startup_provider(None, ui) == "deepseek"
    assert ui.titles == ["Select LLM Provider"]
    with pytest.raises(RuntimeError, match="No provider"):
        main.resolve_startup_provider(None, _ScriptedUI([None]))


def test_build_agent_collects_a_missing_secret_through_the_interactive_ui(home, tmp_path, monkeypatch):
    from executor import SafeExecutor

    for name in ("DEEPSEEK_API_KEY", "AGENT_LLM_PROVIDER"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(main, "fetch_available_models", lambda provider, credentials: ({}, "offline"))
    secret = "sk-interactive-secret"
    ui = _ScriptedUI([secret])
    agent, config, _ = main.build_agent(
        SafeExecutor(tmp_path),
        max_steps=5,
        persist_logs=False,
        verbose=False,
        provider_override="deepseek",
        interactive_ui=ui,
    )
    assert config.provider == "deepseek"
    assert len(ui.titles) == 1 and secret not in ui.titles[0]
    assert _saved("deepseek_api_key") == secret
