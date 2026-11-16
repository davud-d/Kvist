"""``/council`` and ``/best-of`` in the classic terminal: commands that run as a turn (UI_API.md section 8c)."""
from __future__ import annotations

import io
import re
from types import SimpleNamespace

from rich.console import Console

from bestof_turns import run_turn_goal
from kernel.agent.agent import LocalAgent
from kernel.agent.agent_state import AgentConfig
from kernel.bestof.turns import TurnResult
from kernel.sandbox.executor import SafeExecutor
from kernel.tests.conftest import FakeLLMClient, NullPerfLogger
from terminal_banner import TerminalBannerUI


def _console() -> Console:
    # A terminal that draws no colours: the UI renders for a terminal and the text can be read back.
    return Console(file=io.StringIO(), force_terminal=True, color_system=None, width=100)


def _shown(console: Console) -> str:
    return console.file.getvalue()


def _agent(tmp_path, console=None, client=None):
    console = console or _console()
    config = AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0, cache_enabled=False, stream=False)
    agent = LocalAgent(client=client or FakeLLMClient(content="chat reply"), executor=SafeExecutor(tmp_path), config=config,
                       ui=TerminalBannerUI(console=console), console=console)
    agent._perf_logger = NullPerfLogger()
    return agent, console


def _runner(seen, final="## council\n\nThe chosen decision: use security", ok=True):
    def run(agent, question):
        seen.append(question)
        return TurnResult(ok, "council: decision chosen", final)

    return run


def test_a_line_that_is_not_a_turn_command_is_left_to_the_loop(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _runner(seen))
    agent, console = _agent(tmp_path)
    for line in ("fix the bug", "/role coder", "/councillor x", "see /council later"):
        assert run_turn_goal(agent, line, console) is False
    assert seen == [] and _shown(console) == ""


def test_a_council_runs_as_a_turn_and_its_final_answer_is_the_terminals_usual_output(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _runner(seen))
    agent, console = _agent(tmp_path)

    assert run_turn_goal(agent, "/council which is simpler?", console) is True

    assert seen == ["which is simpler?"]
    shown = _shown(console)
    assert "Final Answer" in shown and "The chosen decision: use security" in shown
    # The decision is in the history, so the next request knows it, and the agent answers one right away.
    assert agent._session_history[-1]["goal"] == "/council which is simpler?"
    assert agent.run("thanks, go on") == "chat reply"


def test_a_best_of_line_reaches_its_runner_with_the_count_in_the_question(tmp_path, monkeypatch):
    seen: list[str] = []
    # The core's list of turn commands names best-of once it has it; the terminal asks that list, not a copy.
    monkeypatch.setattr("kernel.bestof.turns.TURN_COMMANDS", ("council", "best-of"))
    monkeypatch.setattr("kernel.bestof.turns._GOAL", re.compile(r"/(council|best-of)(?:\s+(.*))?", re.IGNORECASE | re.DOTALL))
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _runner(seen))
    agent, console = _agent(tmp_path)

    assert run_turn_goal(agent, "/best-of 3 fix the bug", console) is True

    assert seen == ["3 fix the bug"]


def test_a_bare_command_says_how_to_use_it_and_runs_nothing(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _runner(seen))
    agent, console = _agent(tmp_path)

    assert run_turn_goal(agent, "/council", console) is True

    assert "Usage: /council <question>" in _shown(console)
    assert seen == []


def test_ctrl_c_stops_the_turn_and_the_session_stays_usable(tmp_path, monkeypatch):
    def interrupted(agent, question):
        raise KeyboardInterrupt

    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: interrupted)
    agent, console = _agent(tmp_path)
    cancelled = []
    monkeypatch.setattr(agent, "request_cancel", lambda: cancelled.append(True))

    assert run_turn_goal(agent, "/council which?", console) is True

    assert cancelled == [True]
    assert "Agent run stopped by you" in _shown(console)
    assert agent.run("and now an ordinary request") == "chat reply"


def test_a_failed_turn_is_reported_and_the_session_stays_open(tmp_path, monkeypatch):
    def failing(agent, question):
        raise RuntimeError("the members could not start")

    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: failing)
    agent, console = _agent(tmp_path)

    assert run_turn_goal(agent, "/council which?", console) is True

    assert "Task failed safely: the members could not start" in _shown(console)
    assert agent.run("still here") == "chat reply"


def test_a_closed_session_answers_with_its_refusal_and_no_work_is_done(tmp_path, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _runner(seen))
    agent, console = _agent(tmp_path)
    monkeypatch.setattr(agent, "_refuse_closed_session", lambda: "This conversation is over.")

    assert run_turn_goal(agent, "/council which?", console) is True

    assert "This conversation is over." in _shown(console)
    assert seen == []


def test_the_pause_key_of_the_screen_pauses_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr("kernel.bestof.turns.turn_runner", lambda name: _runner([]))
    agent, console = _agent(tmp_path)
    screen = SimpleNamespace(pause_handler=None)

    run_turn_goal(agent, "/council which?", console, screen)

    assert screen.pause_handler == agent.request_pause


# --- the decision of a best-of run --------------------------------------------------------------------


def test_a_decision_shows_its_whole_verdict_then_asks_the_ordinary_choice():
    console = _console()
    ui = TerminalBannerUI(console=console)
    asked = []
    ui.attach_screen(SimpleNamespace(ask_choice=lambda title, options: asked.append((title, options)) or "accept"))

    answer = ui.ask_decision("Best-of result", [("accept", "Accept e1"), ("discard", "Discard")],
                             body="Verdict\nwinner: e1 (tests passed)\n| e2 | finalist |", purpose="bestof_decision", run_id="r1")

    assert answer == "accept"
    assert asked == [("Best-of result", [("accept", "Accept e1"), ("discard", "Discard")])]
    for line in ("Verdict", "winner: e1 (tests passed)", "| e2 | finalist |"):
        assert line in _shown(console)


def test_a_patch_in_a_decision_is_shown_as_a_diff_block():
    console = _console()
    ui = TerminalBannerUI(console=console)
    ui.attach_screen(SimpleNamespace(ask_choice=lambda title, options: "diff"))

    ui.ask_decision("Patch of e1", [("accept", "Accept")], body="--- a/x\n+++ b/x\n@@ -1 +1 @@\n-old\n+new")

    shown = _shown(console)
    assert "@@ -1 +1 @@" in shown and "-old" in shown and "+new" in shown


def test_without_a_terminal_a_decision_is_left_open_not_taken_for_the_person(monkeypatch):
    console = _console()
    ui = TerminalBannerUI(console=console)
    monkeypatch.setattr("terminal_banner.sys.stdin", SimpleNamespace(isatty=lambda: False))

    answer = ui.ask_decision("Best-of result", [("accept", "Accept e1"), ("discard", "Discard")], body="winner: e1")

    assert answer is None
    assert "winner: e1" in _shown(console)
