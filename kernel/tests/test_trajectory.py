# -*- coding: utf-8 -*-
from __future__ import annotations

import json
from types import SimpleNamespace

from kernel.agent import agent_model, agent_runtime, trajectory
from kernel.tests.conftest import FakeLLMClient


def _raw(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _recording(agent_factory, tmp_path, monkeypatch, **config):
    root = tmp_path / "trajectories"
    monkeypatch.setenv(trajectory.TRAJECTORY_DIR_ENV, str(root))
    agent = agent_factory(**config)
    agent._run_id = "run1"
    return agent, root / trajectory.session_folder(agent) / "run1.jsonl"


class _StreamingReasoningClient:
    def __init__(self):
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        deltas = [
            SimpleNamespace(content=None, reasoning_content="Read it "),
            SimpleNamespace(content=None, reasoning_content="first."),
            SimpleNamespace(content='<tool_call>{"name": "read_file"}</tool_call>', reasoning_content=None),
        ]
        return [SimpleNamespace(choices=[SimpleNamespace(delta=delta)]) for delta in deltas]


def test_nothing_is_recorded_without_the_variable(agent_factory, tmp_path):
    agent = agent_factory(stream=False, cache_enabled=False)
    agent._run_id = "run1"

    agent._call_model("m", "system", "user", usage_kind="step", json_mode=False)

    assert getattr(agent, "_trajectory", None) is None
    assert not list(tmp_path.rglob("*.jsonl"))


def test_text_call_keeps_the_exact_prompt_and_the_streamed_reasoning(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch, stream=True, verbose=False, cache_enabled=False)
    agent.client = _StreamingReasoningClient()

    content = agent._call_model("m", "the system", "the task", usage_kind="step", json_mode=False)

    [call] = [record for record in trajectory.read_trajectory(path) if record["type"] == "call"]
    assert call["protocol"] == "text"
    assert call["kind"] == "step"
    assert call["messages"] == [
        {"role": "system", "content": "the system"},
        {"role": "user", "content": "the task"},
    ]
    assert call["response"] == {"content": content, "reasoning": "Read it first."}


def test_non_streaming_reasoning_is_kept(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch, stream=False, cache_enabled=False)
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="answer", reasoning_content="because"),
        )])
    )))

    agent._call_model("m", "system", "user", usage_kind="step", json_mode=False)

    [call] = [record for record in trajectory.read_trajectory(path) if record["type"] == "call"]
    assert call["response"]["reasoning"] == "because"


def test_a_message_sent_again_is_stored_once(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch, stream=False, cache_enabled=False)
    agent.client = FakeLLMClient(content="ok")

    agent._call_model("m", "same system", "first", usage_kind="step", json_mode=False)
    agent._call_model("m", "same system", "second", usage_kind="step", json_mode=False)

    stored = [record["message"] for record in _raw(path) if record["type"] == "message"]
    assert [message["content"] for message in stored] == ["same system", "first", "second"]
    calls = [record for record in trajectory.read_trajectory(path) if record["type"] == "call"]
    assert [call["messages"][1]["content"] for call in calls] == ["first", "second"]


def test_secrets_are_redacted_in_the_record(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch, stream=False, cache_enabled=False)
    agent.client = FakeLLMClient(content="ok")
    secret = "sk-proj-" + "a" * 24

    agent._call_model("m", "system", f"the key is {secret}", usage_kind="step", json_mode=False)

    assert secret not in path.read_text(encoding="utf-8")


def test_native_turn_keeps_reasoning_and_tool_calls(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch, stream=False)
    call = SimpleNamespace(id="c1", function=SimpleNamespace(name="read_file", arguments='{"path": "a.txt"}'))
    agent.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kwargs: SimpleNamespace(choices=[SimpleNamespace(
            message=SimpleNamespace(content="", reasoning_content="look first", tool_calls=[call]),
            finish_reason="tool_calls",
        )], usage=None)
    )))
    messages = [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}]
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": {"type": "object"}}}]

    agent_model._request_native_turn(agent, "m", messages, tools)

    [record] = [record for record in trajectory.read_trajectory(path) if record["type"] == "call"]
    assert record["protocol"] == "native"
    assert record["tools"] == tools
    assert [message["role"] for message in record["messages"]] == ["system", "user"]
    assert record["response"]["reasoning"] == "look first"
    assert record["response"]["tool_calls"] == [{"id": "c1", "name": "read_file", "arguments": '{"path": "a.txt"}'}]


def test_an_unwritable_folder_costs_the_record_not_the_call(agent_factory, tmp_path, monkeypatch):
    blocker = tmp_path / "not-a-folder"
    blocker.write_text("x", encoding="utf-8")
    monkeypatch.setenv(trajectory.TRAJECTORY_DIR_ENV, str(blocker))
    agent = agent_factory(stream=False, cache_enabled=False)
    agent._run_id = "run1"
    said: list[str] = []
    agent._ui.debug_print = said.append
    agent.client = FakeLLMClient(content="ok")

    assert agent._call_model("m", "system", "one", usage_kind="step", json_mode=False) == "ok"
    assert agent._call_model("m", "system", "two", usage_kind="step", json_mode=False) == "ok"

    assert len([line for line in said if line.startswith("[trajectory]")]) == 1


def test_run_outcome_tells_a_stop_from_a_finished_run(agent_factory):
    agent = agent_factory()
    assert agent_runtime._trajectory_outcome(agent, True, None) == "done"
    assert agent_runtime._trajectory_outcome(agent, False, RuntimeError("x")) == "error"
    agent._state.stopped_by_user = True
    assert agent_runtime._trajectory_outcome(agent, False, None) == "stopped"


class _PickingUI:
    def __init__(self, answer):
        self.answer = answer
        self.asked: list[tuple[str, list[str]]] = []

    def choose_option(self, title, options, current_value="", help_text=""):
        self.asked.append((title, [option["value"] for option in options]))
        return self.answer


def _record_a_run(agent, run_id):
    agent._run_id = run_id
    trajectory.note_run_start(agent, "goal", 1)


def test_rating_moves_the_sessions_runs_and_rating_again_moves_them_back(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch)
    root = tmp_path / "trajectories"
    folder = trajectory.session_folder(agent)
    _record_a_run(agent, "run1")
    _record_a_run(agent, "run2")

    assert trajectory.rate_session(agent, "good") == root / "good" / folder
    assert sorted(file.name for file in (root / "good" / folder).iterdir()) == ["run1.jsonl", "run2.jsonl"]
    assert not (root / folder).exists()

    trajectory.rate_session(agent, "bad")
    assert sorted(file.name for file in (root / "bad" / folder).iterdir()) == ["run1.jsonl", "run2.jsonl"]
    assert not (root / "good" / folder).exists()


def test_a_run_continued_after_rating_joins_its_rated_file(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch)
    _record_a_run(agent, "run1")
    target = trajectory.rate_session(agent, "good")

    trajectory.note_run_start(agent, "goal", 5)
    assert trajectory.session_label(agent) == ("good", 2)
    trajectory.rate_session(agent, "good")

    starts = [record["start_step"] for record in _raw(target / "run1.jsonl")]
    assert starts == [1, 5]


def test_rate_dialog_offers_good_or_bad_and_files_the_session(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch)
    _record_a_run(agent, "run1")
    ui = _PickingUI("good")

    ok, text = trajectory.rate_dialog(agent, ui, ru=True)

    assert ok and "хороший пример" in text
    assert ui.asked == [("Оценка сессии для обучения", ["good", "bad"])]
    assert trajectory.session_label(agent) == ("good", 1)


def test_rate_dialog_left_without_an_answer_moves_nothing(agent_factory, tmp_path, monkeypatch):
    agent, path = _recording(agent_factory, tmp_path, monkeypatch)
    _record_a_run(agent, "run1")

    assert trajectory.rate_dialog(agent, _PickingUI(None), ru=False) == (True, "")
    assert trajectory.session_label(agent) == ("", 1)


def test_rate_dialog_says_why_it_cannot_rate(agent_factory, tmp_path, monkeypatch):
    agent = agent_factory()
    ui = _PickingUI("good")

    ok, text = trajectory.rate_dialog(agent, ui, ru=False)
    assert not ok and trajectory.TRAJECTORY_DIR_ENV in text

    monkeypatch.setenv(trajectory.TRAJECTORY_DIR_ENV, str(tmp_path / "empty"))
    ok, text = trajectory.rate_dialog(agent, ui, ru=False)
    assert not ok and "Nothing is recorded" in text
    assert ui.asked == []
