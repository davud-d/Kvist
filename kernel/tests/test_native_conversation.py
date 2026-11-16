# -*- coding: utf-8 -*-
"""The conversation of a native run on disk: written as it grows, continued by /resume, gone with its session.

A run is driven through its real step functions against a fake Anthropic SDK,
and a second agent plays the process that resumes it.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from anthropic.types import RedactedThinkingBlock, TextBlock, ThinkingBlock, ToolUseBlock

from kernel.agent import agent_model, agent_runtime
from kernel.agent.native_tools import calls_from_turn
from kernel.llm.anthropic_wire import THINKING_KEY
from kernel.protocol.protocol import ModelResponse
from kernel.session import session_events
from kernel.session.agent_logging import get_native_path
from kernel.session.durable_json import JsonStorageError
from kernel.session.session_events import read_native_conversation, write_native_messages
from kernel.session.session_store import SessionStore
from kernel.tests.test_anthropic_thinking import MODEL, OPAQUE, OTHER_MODEL, SIGNATURE, _agent

RUN = "run-1"
THOUGHT = "plan the read"
BLOCKS = [
    {"type": "thinking", "thinking": THOUGHT, "signature": SIGNATURE},
    {"type": "redacted_thinking", "data": OPAQUE},
]


def _reply(thought: str = THOUGHT, *, call: bool = True) -> Any:
    content: list[Any] = [
        ThinkingBlock(type="thinking", thinking=thought, signature=SIGNATURE),
        RedactedThinkingBlock(type="redacted_thinking", data=OPAQUE),
        TextBlock(type="text", text="Looking."),
    ]
    if call:
        content.append(ToolUseBlock(type="tool_use", id="toolu_1", name="read_file", input={"path": "a.py"}))
    return SimpleNamespace(content=content, stop_reason="tool_use" if call else "end_turn", usage=None)


def _run_agent(tmp_path, *, model: str = MODEL, provider: str = "anthropic"):
    agent, sdk = _agent(tmp_path, stream=False, provider=provider)
    agent._run_id = RUN
    agent._state.native_tools = True
    agent._state.response_language = "en"
    agent._state.selected_action_model = model
    agent._set_environment_facts()
    return agent, sdk


def _step(agent, sdk, step: int, *, thought: str = THOUGHT):
    """One native step as the run loop takes it: the model call, then the turn's calls and their results."""
    sdk.replies.append(_reply(thought))
    turn = agent_runtime._call_native_turn(agent, "task", step)
    response = ModelResponse(text=turn.content.strip(), calls=calls_from_turn(turn))
    agent_runtime._run_turn(agent, "task", step, response, turn)
    return turn


def _interrupted_run(tmp_path, **kwargs):
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    agent, sdk = _run_agent(tmp_path, **kwargs)
    _step(agent, sdk, 1)
    return agent


def _file(tmp_path: Path) -> Path:
    return tmp_path / ".kvist" / "sessions" / "default.native.jsonl"


def _records(tmp_path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in _file(tmp_path).read_text(encoding="utf-8").splitlines()]


def _resumed(tmp_path, **kwargs):
    """A fresh agent for the same run, as a new process after a crash, asked for its next step."""
    agent, sdk = _run_agent(tmp_path, **kwargs)
    sdk.replies.append(_reply(call=False))
    agent_runtime._call_native_turn(agent, "task", 2)
    return agent, sdk.requests[0]


def _assistant(request: dict[str, Any]) -> dict[str, Any]:
    return next(message for message in request["messages"] if message["role"] == "assistant")


# ── writing ────────────────────────────────────────────────────────────────


def test_each_message_is_written_once_as_it_appears(tmp_path):
    agent = _interrupted_run(tmp_path)

    first = _records(tmp_path)
    assert [record["i"] for record in first] == [0, 1, 2, 3]
    assert [record["message"]["role"] for record in first] == ["system", "user", "assistant", "tool"]
    assert {(record["run_id"], record["provider"], record["model"]) for record in first} == {(RUN, "anthropic", MODEL)}

    agent._state.pending_instruction = "also check b.py"
    agent._state.native_tools = True
    agent._state.native_messages.append({"role": "user", "content": "one more"})
    agent_runtime._persist_native_conversation(agent)
    assert [record["i"] for record in _records(tmp_path)] == [0, 1, 2, 3, 4]
    assert _records(tmp_path)[:4] == first


def test_a_thinking_block_is_stored_redacted_with_the_digest_of_the_original(tmp_path):
    _interrupted_run(tmp_path)

    held = _records(tmp_path)[2]["message"][THINKING_KEY]

    assert held["blocks"] == BLOCKS
    assert held["digests"] == [session_events.digest_of(block) for block in BLOCKS]
    assert (held["provider"], held["model"]) == ("anthropic", MODEL)


def test_a_secret_in_a_block_stays_off_the_disk(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    agent, sdk = _run_agent(tmp_path)
    _step(agent, sdk, 1, thought="use sk-abcdefghijklmnopqrstuvwxyz123456 here")

    assert "sk-abcdefghijklmnop" not in _file(tmp_path).read_text(encoding="utf-8")


def test_a_new_run_starts_the_file_over(tmp_path):
    _interrupted_run(tmp_path)
    agent, _sdk = _run_agent(tmp_path)
    agent._run_id = "run-2"
    agent._state.native_messages = [{"role": "system", "content": "s2"}, {"role": "user", "content": "u2"}]

    agent_runtime._persist_native_conversation(agent)

    assert {record["run_id"] for record in _records(tmp_path)} == {"run-2"}
    assert len(_records(tmp_path)) == 2


def test_a_disk_that_fails_costs_the_conversation_never_the_run(tmp_path, monkeypatch):
    agent, sdk = _run_agent(tmp_path)
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    said: list[str] = []
    agent._ui.debug_print = said.append
    calls: list[int] = []

    def failing(*args, **kwargs):
        calls.append(1)
        raise JsonStorageError("disk full")

    monkeypatch.setattr(agent_runtime, "write_native_messages", failing)

    _step(agent, sdk, 1)

    assert len(calls) == 1
    saying = [line for line in said if "[native conversation]" in line]
    assert len(saying) == 1 and "disk full" in saying[0]
    assert agent._state.native_messages[-1]["role"] == "tool"


# ── /resume ────────────────────────────────────────────────────────────────


def test_the_same_run_on_the_same_model_continues_with_its_blocks(tmp_path):
    _interrupted_run(tmp_path)

    agent, request = _resumed(tmp_path)

    assert request["model"] == MODEL and "thinking" in request
    assistant = _assistant(request)
    assert json.dumps(assistant["content"][:2]) == json.dumps(BLOCKS)
    results = [
        block
        for message in request["messages"]
        if message["role"] == "user" and isinstance(message["content"], list)
        for block in message["content"]
        if block["type"] == "tool_result"
    ]
    assert [result["tool_use_id"] for result in results] == ["toolu_1"]
    assert "print(1)" in json.dumps(results)
    assert [message["role"] for message in agent._state.native_messages] == ["system", "user", "assistant", "tool"]


def test_the_first_messages_come_back_not_a_new_runtime_prompt(tmp_path):
    _interrupted_run(tmp_path)
    first_task = _records(tmp_path)[1]["message"]["content"]
    _file(tmp_path).write_text(_file(tmp_path).read_text(encoding="utf-8").replace(json.dumps(first_task)[1:-1], "THE FIRST TASK"), encoding="utf-8")

    agent, _request = _resumed(tmp_path)

    assert agent._state.native_messages[1]["content"] == "THE FIRST TASK"


def test_calls_the_crash_left_unanswered_get_an_honest_result(tmp_path):
    agent = _interrupted_run(tmp_path)
    # The process died while the calls ran: the assistant message is on disk, their results are not.
    lines = _file(tmp_path).read_text(encoding="utf-8").splitlines()
    _file(tmp_path).write_text("\n".join(lines[:3]) + "\n", encoding="utf-8")
    assert agent._state.native_messages[2]["role"] == "assistant"

    _agent_after, request = _resumed(tmp_path)

    result = request["messages"][-1]["content"][0]
    assert result["type"] == "tool_result" and result["tool_use_id"] == "toolu_1"
    assert "interrupted" in json.dumps(result["content"]) and "may or may not have taken effect" in json.dumps(result["content"])
    assert json.dumps(_assistant(request)["content"][:2]) == json.dumps(BLOCKS)


def test_another_model_gets_the_conversation_built_anew(tmp_path):
    _interrupted_run(tmp_path)

    agent, request = _resumed(tmp_path, model=OTHER_MODEL)

    assert SIGNATURE not in json.dumps(request)
    assert [message["role"] for message in request["messages"]] == ["user"]
    assert [message["role"] for message in agent._state.native_messages] == ["system", "user"]


def test_another_provider_gets_the_conversation_built_anew(tmp_path):
    _interrupted_run(tmp_path)

    agent, _request = _resumed(tmp_path, provider="anymodel")

    assert [message["role"] for message in agent._state.native_messages] == ["system", "user"]


def test_another_run_does_not_continue_a_conversation_that_is_not_its_own(tmp_path):
    _interrupted_run(tmp_path)
    agent, sdk = _run_agent(tmp_path)
    agent._run_id = "run-2"
    sdk.replies.append(_reply(call=False))

    agent_runtime._call_native_turn(agent, "task", 1)

    assert SIGNATURE not in json.dumps(sdk.requests[0])
    assert len(sdk.requests[0]["messages"]) == 1


def test_a_block_whose_text_changed_on_disk_is_left_out_and_the_rest_continues(tmp_path):
    _interrupted_run(tmp_path)
    _file(tmp_path).write_text(_file(tmp_path).read_text(encoding="utf-8").replace(THOUGHT, "plan the rewrite"), encoding="utf-8")

    agent, request = _resumed(tmp_path)

    assert _assistant(request)["content"][0]["type"] == "text"
    assert SIGNATURE not in json.dumps(request)
    assert "thinking" not in request  # a tool turn without its block: this step runs without thinking
    assert [message["role"] for message in agent._state.native_messages][:4] == ["system", "user", "assistant", "tool"]


def test_a_block_redaction_changed_is_not_replayed_either(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    agent, sdk = _run_agent(tmp_path)
    _step(agent, sdk, 1, thought="use sk-abcdefghijklmnopqrstuvwxyz123456 here")

    _agent_after, request = _resumed(tmp_path)

    assert _assistant(request)["content"][0]["type"] == "text"


def test_a_series_that_fails_takes_the_turns_before_it_along_and_spares_the_ones_after(tmp_path):
    def message(index: int, role: str, **extra: Any) -> dict[str, Any]:
        return {"role": role, "content": f"m{index}", **extra}

    held = {"provider": "anthropic", "model": MODEL, "blocks": BLOCKS}
    messages = [
        message(0, "system"), message(1, "user"),
        message(2, "assistant", **{THINKING_KEY: held}), message(3, "user"),
        message(4, "assistant", **{THINKING_KEY: held}), message(5, "user"),
        message(6, "assistant", **{THINKING_KEY: held}), message(7, "user"),
    ]
    path = tmp_path / ".kvist" / "sessions" / "default.native.jsonl"
    write_native_messages(path, tmp_path, run_id=RUN, provider="anthropic", model=MODEL, tools="t", messages=messages, start=0)
    lines = path.read_text(encoding="utf-8").splitlines()
    damaged = json.loads(lines[4])
    damaged["message"][THINKING_KEY]["blocks"][0]["thinking"] = "changed"
    lines[4] = json.dumps(damaged)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    restored = read_native_conversation(path, tmp_path, run_id=RUN, provider="anthropic", model=MODEL, tools="t")

    assert [THINKING_KEY in item for item in restored] == [False, False, False, False, False, False, True, False]


def test_a_conversation_the_tool_list_moved_on_from_is_continued_without_blocks(tmp_path, monkeypatch):
    _interrupted_run(tmp_path)
    extra = {"type": "function", "function": {"name": "echo__say", "description": "x", "parameters": {"type": "object", "properties": {}}}}
    agent, sdk = _run_agent(tmp_path)
    monkeypatch.setattr(agent.skills, "tool_definitions", lambda: [extra])
    sdk.replies.append(_reply(call=False))

    agent_runtime._call_native_turn(agent, "task", 2)

    assert SIGNATURE not in json.dumps(sdk.requests[0])
    assert [message["role"] for message in agent._state.native_messages][:4] == ["system", "user", "assistant", "tool"]


def test_a_conversation_that_ended_on_an_answer_is_not_continued(tmp_path):
    agent, _sdk = _run_agent(tmp_path)
    agent._state.native_messages = [
        {"role": "system", "content": "s"}, {"role": "user", "content": "u"}, {"role": "assistant", "content": "done"},
    ]
    agent._state.native_tool_defs = [{"type": "function", "function": {"name": "t"}}]
    agent_runtime._persist_native_conversation(agent)
    fresh, _sdk2 = _run_agent(tmp_path)
    fresh._state.native_tool_defs = agent._state.native_tool_defs

    agent_runtime._restore_native_conversation(fresh)

    assert fresh._state.native_messages == []


def test_a_damaged_tail_costs_only_itself(tmp_path):
    _interrupted_run(tmp_path)
    with _file(tmp_path).open("a", encoding="utf-8") as handle:
        handle.write('{"i":4,"run_id":"run-1","provider":"anth')

    agent, _request = _resumed(tmp_path)

    assert [message["role"] for message in agent._state.native_messages][:4] == ["system", "user", "assistant", "tool"]


def test_a_follow_up_the_conversation_never_heard_is_added_once(tmp_path):
    _interrupted_run(tmp_path)
    agent, sdk = _run_agent(tmp_path)
    agent._state.task_updates = ["also fix b.py"]
    sdk.replies.append(_reply(call=False))

    agent_runtime._call_native_turn(agent, "task", 2)

    texts = [message["content"] for message in agent._state.native_messages if message["role"] == "user"]
    assert sum("also fix b.py" in text for text in texts) == 1


# ── edits of the prefix ────────────────────────────────────────────────────


def test_a_skill_that_adds_tools_mid_run_drops_the_blocks(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    agent, sdk = _run_agent(tmp_path)
    _step(agent, sdk, 1)
    assert THINKING_KEY in agent._state.native_messages[2]
    extra = {"type": "function", "function": {"name": "echo__say", "description": "x", "parameters": {"type": "object", "properties": {}}}}
    monkeypatch.setattr(agent.skills, "tool_definitions", lambda: [extra])
    sdk.replies.append(_reply(call=False))

    agent_runtime._call_native_turn(agent, "task", 2)

    assert all(THINKING_KEY not in message for message in agent._state.native_messages)
    assert SIGNATURE not in json.dumps(sdk.requests[1])
    assert "thinking" not in sdk.requests[1]


def test_blocks_of_the_turns_after_the_change_accumulate_again(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    agent, sdk = _run_agent(tmp_path)
    _step(agent, sdk, 1)
    extra = {"type": "function", "function": {"name": "echo__say", "description": "x", "parameters": {"type": "object", "properties": {}}}}
    monkeypatch.setattr(agent.skills, "tool_definitions", lambda: [extra])

    _step(agent, sdk, 2)

    held = [THINKING_KEY in message for message in agent._state.native_messages if message["role"] == "assistant"]
    assert held == [False, True]


def test_eliding_a_tool_output_drops_the_blocks(tmp_path, monkeypatch):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    agent, sdk = _run_agent(tmp_path)
    _step(agent, sdk, 1)
    monkeypatch.setattr(agent_model, "context_token_limit", lambda _agent: 1)
    sdk.replies.append(_reply(call=False))

    agent_runtime._call_native_turn(agent, "task", 2)

    assert all(THINKING_KEY not in message for message in agent._state.native_messages)
    assert SIGNATURE not in json.dumps(sdk.requests[1])
    assert "output removed" in json.dumps(sdk.requests[1])


# ── size ───────────────────────────────────────────────────────────────────


def test_a_file_over_its_limit_loses_whole_old_exchanges_and_keeps_the_head(tmp_path, monkeypatch):
    monkeypatch.setattr(session_events, "MAX_SESSION_BYTES", 3_000)
    monkeypatch.setattr(session_events, "KEEP_SESSION_BYTES", 1_800)
    held = {"provider": "anthropic", "model": MODEL, "blocks": BLOCKS}
    messages: list[dict[str, Any]] = [{"role": "system", "content": "sys"}, {"role": "user", "content": "task"}]
    path = tmp_path / ".kvist" / "sessions" / "default.native.jsonl"

    def write(start: int) -> None:
        write_native_messages(path, tmp_path, run_id=RUN, provider="anthropic", model=MODEL, tools="t", messages=messages, start=start)

    write(0)
    for number in range(8):
        call = {"id": f"c{number}", "type": "function", "function": {"name": "read_file", "arguments": "{}"}}
        messages += [
            {"role": "assistant", "content": "", "tool_calls": [call], THINKING_KEY: held},
            {"role": "tool", "tool_call_id": f"c{number}", "content": "x" * 300},
        ]
        write(len(messages) - 2)

    restored = read_native_conversation(path, tmp_path, run_id=RUN, provider="anthropic", model=MODEL, tools="t")

    assert path.stat().st_size < 3_000 + 1_000
    assert [message["role"] for message in restored[:2]] == ["system", "user"]
    assert restored[2]["role"] == "assistant" and restored[-1]["tool_call_id"] == "c7"
    assert len(restored) < len(messages)
    assert all(THINKING_KEY not in message for message in restored)  # the prefix they were signed over is gone
    tool_ids = [message["tool_call_id"] for message in restored if message["role"] == "tool"]
    call_ids = [call["id"] for message in restored for call in message.get("tool_calls") or ()]
    assert tool_ids == call_ids


# ── the session's own lifecycle ────────────────────────────────────────────


def _store_with_conversations(tmp_path) -> tuple[SessionStore, dict[str, Path]]:
    store = SessionStore(tmp_path)
    paths: dict[str, Path] = {}
    for name in ("alpha", "beta"):
        store.create(name)
        paths[name] = get_native_path(store, name)
        write_native_messages(
            paths[name], tmp_path, run_id=RUN, provider="anthropic", model=MODEL, tools="t",
            messages=[{"role": "user", "content": "hi"}], start=0,
        )
    return store, paths


def test_deleting_a_session_removes_its_conversation(tmp_path):
    store, paths = _store_with_conversations(tmp_path)

    store.delete("alpha")

    assert not paths["alpha"].exists() and paths["beta"].exists()


def test_resetting_a_session_removes_its_conversation(tmp_path):
    store, paths = _store_with_conversations(tmp_path)

    store.reset()

    assert not paths["beta"].exists() and paths["alpha"].exists()


def test_a_session_created_under_an_old_name_does_not_inherit_its_conversation(tmp_path):
    store, paths = _store_with_conversations(tmp_path)
    store.delete("alpha")
    write_native_messages(
        paths["alpha"], tmp_path, run_id=RUN, provider="anthropic", model=MODEL, tools="t",
        messages=[{"role": "user", "content": "stale"}], start=0,
    )

    store.create("alpha")

    assert not paths["alpha"].exists()


def test_the_file_is_no_session(tmp_path):
    _store_with_conversations(tmp_path)

    assert sorted(SessionStore(tmp_path).listing()["sessions"]) == ["alpha", "beta"]
