"""The model's offer of best-of in the classic terminal: a yes starts the best-of turn after the offering turn."""
from __future__ import annotations

import io
import json
import subprocess

import pytest
from rich.console import Console

from bestof_turns import run_follow_up_turn
from kernel.agent.agent import LocalAgent
from kernel.agent.agent_state import AgentConfig
from kernel.bestof.turns import TurnResult
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullPerfLogger
from terminal_banner import TerminalBannerUI

TASK = "rewrite the storage layer so reads no longer block writes"
REASON = "Two designs fit and they break in different ways."


class AnsweringUI(TerminalBannerUI):
    """The terminal's UI with the person's answer to the offer scripted."""

    def __init__(self, answer, console):
        super().__init__(console=console)
        self.answer = answer
        self.asked: list[str] = []

    def ask_choice(self, title, options):
        self.asked.append(title)
        return self.answer


@pytest.fixture(autouse=True)
def _text_protocol(monkeypatch):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


@pytest.fixture
def repo(tmp_path, tmp_path_factory, monkeypatch):
    config = tmp_path_factory.mktemp("gitconfig") / "empty"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    root = tmp_path / "repo"
    root.mkdir()
    (root / "a.txt").write_text("hello\n", encoding="utf-8")
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
        subprocess.run(["git", *command], cwd=root, check=True, capture_output=True)
    return root


def _agent(repo, answer, follow_up=True):
    console = Console(file=io.StringIO(), force_terminal=True, color_system=None, width=100)
    ui = AnsweringUI(answer, console)
    config = AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0, cache_enabled=False, stream=False)
    agent = LocalAgent(client=FakeLLMClient(), executor=SafeExecutor(repo), config=config, ui=ui, console=console)
    agent._perf_logger = NullPerfLogger()
    agent.bestof_follow_up = follow_up
    offer = f'<tool_call>{json.dumps({"name": "offer_best_of", "arguments": {"reason": REASON, "count": 3}})}</tool_call>'
    agent._call_action_model = lambda prompt: offer if not ui.asked else "Carried on."
    return agent, ui, console


def _fake_runner(seen):
    def run(agent, question):
        seen.append((agent._state.user_goal, question))
        return TurnResult(True, "best-of: done / best-of: готово", "## best-of\n\nfake result")

    return run


def test_a_yes_in_the_classic_terminal_starts_the_best_of_turn_with_the_persons_text(repo, monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _fake_runner(seen))
    agent, ui, console = _agent(repo, "best_of")

    result = agent.run(TASK, routed_role="coder")
    assert seen == []
    run_follow_up_turn(agent, console)

    assert result.startswith("Starting best-of: 3 attempts")
    assert seen == [(f"/best-of 3 {TASK}", f"3 {TASK}")]
    assert len(ui.asked) == 1 and "Roughly 4 times" in ui.asked[0]
    assert "fake result" in console.file.getvalue()


def test_the_follow_up_runs_once(repo, monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _fake_runner(seen))
    agent, _ui, console = _agent(repo, "best_of")
    agent.run(TASK, routed_role="coder")

    run_follow_up_turn(agent, console)
    run_follow_up_turn(agent, console)

    assert len(seen) == 1


def test_a_no_starts_nothing(repo, monkeypatch):
    seen: list[tuple[str, str]] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _fake_runner(seen))
    agent, _ui, console = _agent(repo, "go")

    result = agent.run(TASK, routed_role="coder")
    run_follow_up_turn(agent, console)

    assert result == "Carried on."
    assert seen == []


def test_a_terminal_that_cannot_start_the_follow_up_turns_the_offer_away(repo):
    agent, ui, _console = _agent(repo, "best_of", follow_up=False)
    prompts: list[str] = []
    offer = f'<tool_call>{json.dumps({"name": "offer_best_of", "arguments": {"reason": REASON}})}</tool_call>'
    agent._call_action_model = lambda prompt: (prompts.append(prompt), offer if len(prompts) == 1 else "Carried on.")[1]

    result = agent.run(TASK, routed_role="coder")

    assert ui.asked == []
    assert "cannot start best-of after an answer" in prompts[1]
    assert result == "Carried on."
