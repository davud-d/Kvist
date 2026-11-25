# -*- coding: utf-8 -*-
"""The main agent offers best-of and waits for the person's yes (``offer_best_of``)."""
from __future__ import annotations

import json
import re
import subprocess
import threading
from pathlib import Path

import pytest

from kernel.agent import agent_runtime
from kernel.agent.agent import LocalAgent
from kernel.agent.agent_state import AgentConfig
from kernel.bestof import offer, turns
from kernel.bestof.styles import DEFAULT_EXECUTORS, MAX_EXECUTORS, MIN_EXECUTORS
from kernel.bestof.turns import TurnResult, take_follow_up
from kernel.plan import end_session
from kernel.protocol import event_stdio
from kernel.protocol.event_session import EventSession
from kernel.protocol.event_stream_ui import EventStreamUI
from kernel.protocol.frontend_protocol import FrontendChannel
from kernel.protocol.parser import (
    MAX_BESTOF_ATTEMPTS,
    MIN_BESTOF_ATTEMPTS,
    CommandValidationError,
    validate_action_args,
)
from kernel.sandbox.executor import SafeExecutor
from kernel.session.session_usage import UsageTotals
from kernel.tests.conftest import FakeLLMClient, NullPerfLogger, NullUI, make_command
from kernel.tests.test_event_stdio_multi import _Collector, _ScriptedReader
from kernel.tests.test_plan_mode import PlanUI

REASON = "Two designs fit and they break in different ways."
TASK = "rewrite the storage layer so reads no longer block writes"
# Words of the code that the person's question must never use.
INTERNAL_WORDS = re.compile(r"executor|judge|worktree|исполнител|судь", re.IGNORECASE)


@pytest.fixture(autouse=True)
def _text_protocol(monkeypatch):
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


@pytest.fixture
def repo(tmp_path, tmp_path_factory, monkeypatch) -> Path:
    """A git repository with one commit; git sees neither the user's config nor their hooks."""
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


def make_agent(workspace: Path, ui=None, *, language: str = "en", follow_up: bool = True) -> LocalAgent:
    """A coder agent in the middle of the person's message ``TASK``, as ``_run_turn`` leaves it."""
    agent = LocalAgent(
        client=FakeLLMClient(),
        executor=SafeExecutor(workspace),
        config=AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0),
        ui=ui if ui is not None else PlanUI(),
    )
    agent._perf_logger = NullPerfLogger()
    agent.bestof_follow_up = follow_up
    state = agent._state
    state.current_agent_role = "coder"
    state.response_language = language
    state.native_tools = True
    state.native_messages = [{"role": "system", "content": "s"}]
    state.message_id = end_session.next_message_id(agent, from_person=True)
    state.user_goal = TASK
    return agent


def offer_command(count: int | None = None, reason: str = REASON):
    args: dict = {"reason": reason}
    if count is not None:
        args["count"] = count
    return make_command("offer_best_of", args, plan_step="plan")


def add_calls(agent: LocalAgent, *, cost: float | None, input_tokens: int = 1000, output_tokens: int = 200) -> None:
    agent._usage_totals.add_call(
        input_tokens=input_tokens, output_tokens=output_tokens, cache_read_tokens=None, cache_write_tokens=None,
        cost_usd=cost, cost_approx=False,
    )


def second_message(agent: LocalAgent) -> None:
    end_session.next_message_id(agent, from_person=True)


# ---------------------------------------------------------------- when the offer is possible


def test_an_interactive_coder_run_in_a_git_repository_can_offer(repo):
    agent = make_agent(repo)

    assert offer.unavailable_reason(agent) == ""
    assert offer.offer_available(agent)


@pytest.mark.parametrize("role", ["devops", "universal"])
def test_the_other_roles_that_change_files_can_offer_too(repo, role):
    agent = make_agent(repo)
    agent._state.current_agent_role = role

    assert offer.unavailable_reason(agent) == ""


@pytest.mark.parametrize(
    "setup, expected",
    [
        ("reader", "changes files"),
        ("judge", "changes files"),
        ("read_only", "changes files"),
        ("nobody_to_ask", "Nobody can be asked"),
        ("helper", "Nobody can be asked"),
        ("no_follow_up", "cannot start best-of after an answer"),
        ("already_offered", "already offered"),
        ("wake_up", "not the person's own request"),
        ("resumed", "not the person's own request"),
        ("files_changed", "already changed files"),
    ],
)
def test_an_offer_that_cannot_be_made_says_why(repo, setup, expected):
    agent = make_agent(repo, NullUI() if setup == "nobody_to_ask" else PlanUI(choices=["go"]),
                       follow_up=setup != "no_follow_up")
    state = agent._state
    if setup in {"reader", "judge"}:
        state.current_agent_role = setup
    if setup == "read_only":
        state.read_only_goal = True
    if setup == "helper":
        agent.is_helper = True
    if setup == "already_offered":
        offer.offer_best_of(agent, offer_command())
    if setup == "wake_up":
        # A wake-up has a message number of the runtime's own, which is not one of the person's.
        state.message_id = end_session.next_message_id(agent, from_person=False)
    if setup == "resumed":
        state.message_id = 0
    if setup == "files_changed":
        state.files_changed.append("a.txt")

    assert expected in offer.unavailable_reason(agent)
    assert expected in offer.rejection_reason(agent, offer_command())


def test_outside_a_git_repository_the_offer_says_so_in_english(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    agent = make_agent(plain)

    reason = offer.unavailable_reason(agent)

    assert "git repository" in reason and "/" not in reason.replace("git repository.", "")


def test_a_repository_without_a_commit_cannot_offer(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=empty, check=True, capture_output=True)

    assert "no commits" in offer.unavailable_reason(make_agent(empty))


def test_the_runtime_turns_a_refused_offer_away_and_tells_the_model_why(repo):
    agent = make_agent(repo, NullUI())
    registered: list[dict] = []
    agent._register_feedback = lambda step, command, feedback: registered.append(feedback)

    refused = agent_runtime._reject_before_execution(agent, 1, offer_command())

    assert refused is True
    assert registered[0]["ok"] is False and "Nobody can be asked" in registered[0]["error"]


def test_the_runtime_lets_an_available_offer_through(repo):
    agent = make_agent(repo)
    agent._register_feedback = lambda step, command, feedback: pytest.fail("the offer was turned away")

    assert agent_runtime._reject_before_execution(agent, 1, offer_command()) is False


# ---------------------------------------------------------------- the tool list of a run


@pytest.fixture
def seen_tools(monkeypatch):
    """What the model is shown on each native step."""
    import kernel.agent.agent_model as agent_model

    seen: list[list[dict]] = []
    monkeypatch.setattr(agent_model, "call_native_action_model", lambda agent, messages, tools: seen.append(list(tools)))
    return seen


def tool_names(agent: LocalAgent, seen: list[list[dict]]) -> list[str]:
    agent_runtime._call_native_turn(agent, "goal", 1)
    return [tool["function"]["name"] for tool in seen[-1]]


def test_the_tool_is_in_the_set_from_the_first_step_and_stays_after_the_offer(repo, seen_tools):
    agent = make_agent(repo, PlanUI(choices=["go"]))
    before = tool_names(agent, seen_tools)

    offer.offer_best_of(agent, offer_command())
    after = tool_names(agent, seen_tools)

    assert "offer_best_of" in before
    assert after == before


@pytest.mark.parametrize("setup", ["headless", "no_follow_up", "not_git"])
def test_the_tool_is_not_in_the_set_when_the_offer_cannot_be_made_at_the_start(repo, tmp_path, seen_tools, setup):
    plain = tmp_path / "plain"
    plain.mkdir()
    agent = make_agent(plain if setup == "not_git" else repo,
                       NullUI() if setup == "headless" else None, follow_up=setup != "no_follow_up")

    assert "offer_best_of" not in tool_names(agent, seen_tools)


def test_the_reader_role_is_never_given_the_tool(repo, seen_tools):
    agent = make_agent(repo)
    agent._state.current_agent_role = "reader"

    assert "offer_best_of" not in tool_names(agent, seen_tools)


# ---------------------------------------------------------------- what the person is asked


def question(agent: LocalAgent, count: int = DEFAULT_EXECUTORS) -> str:
    return offer.question_text(agent, REASON, count)


@pytest.mark.parametrize("language", ["en", "ru"])
def test_the_question_has_no_internal_words_and_the_reason_comes_first(repo, language):
    agent = make_agent(repo, language=language)
    second_message(agent)
    add_calls(agent, cost=0.5)

    text = question(agent)

    assert text.splitlines()[0] == REASON
    assert not INTERNAL_WORDS.search(text)
    assert not INTERNAL_WORDS.search(" ".join(label for _value, label in offer._options(language, 4)))


def test_the_question_says_what_happens_in_english(repo):
    text = question(make_agent(repo), 3)

    assert "3 independent attempts" in text
    assert "its own copy of the project" in text
    assert "Your files do not change until you accept a result" in text
    assert "Roughly 4 times the cost of an ordinary run: 3 attempts and a check." in text


def test_the_question_says_what_happens_in_russian(repo):
    text = question(make_agent(repo, language="ru"), 4)

    assert "best-of" in text
    assert "4 попытки решить задачу параллельно, каждую в своей копии проекта" in text
    assert "оставить лучшую по итогам проверки" in text
    assert "Твои файлы не меняются, пока ты не примешь результат" in text
    assert "Примерно в 5 раз дороже обычного прогона: 4 попытки и проверка." in text


@pytest.mark.parametrize("count", range(MIN_EXECUTORS, MAX_EXECUTORS + 1))
def test_the_multiple_is_attempts_plus_one_for_every_count(repo, count):
    english = question(make_agent(repo), count)
    russian = question(make_agent(repo, language="ru"), count)

    assert f"Roughly {count + 1} times" in english
    assert f"в {count + 1} раз дороже" in russian
    assert f"{count} попыт" in russian


def test_the_first_message_of_a_session_has_the_multiple_and_no_amount(repo):
    agent = make_agent(repo)
    add_calls(agent, cost=0.9)

    text = question(agent)

    assert "Roughly 5 times" in text
    assert "≈" not in text and "$" not in text


def test_a_session_without_a_measured_call_has_no_amount(repo):
    agent = make_agent(repo)
    second_message(agent)

    assert "≈" not in question(agent)


def test_a_measured_cost_is_the_average_turn_times_attempts_plus_one(repo):
    agent = make_agent(repo)
    second_message(agent)
    add_calls(agent, cost=0.30)
    add_calls(agent, cost=0.30)

    text = question(agent, 4)

    # 0.60 over two messages, times the four attempts and the check.
    assert "That is ≈ $1.50 by the average of this session's past turns." in text


def test_the_amount_is_in_russian_for_a_russian_session(repo):
    agent = make_agent(repo, language="ru")
    second_message(agent)
    add_calls(agent, cost=0.30)

    assert "Это ≈ $0.75 по средним прошлым ходам этой сессии." in question(agent, 4)


def test_without_a_known_price_the_amount_is_tokens_and_not_money(repo):
    agent = make_agent(repo)
    second_message(agent)
    add_calls(agent, cost=None, input_tokens=9000, output_tokens=1000)

    text = question(agent, 4)

    # 10 000 tokens over two messages, times five.
    assert "That is ≈ 25k tokens by the average" in text
    assert "$" not in text


def test_a_tiny_amount_is_not_shown_as_zero_dollars(repo):
    agent = make_agent(repo)
    second_message(agent)
    add_calls(agent, cost=0.0004)

    assert "≈ <$0.01" in question(agent, 2)


def test_a_session_restored_with_totals_but_no_calls_shows_no_amount(repo):
    agent = make_agent(repo)
    second_message(agent)
    agent._usage_totals = UsageTotals()

    assert "≈" not in question(agent)


def test_the_dialog_gets_the_question_and_two_options_in_the_response_language(repo):
    ui = PlanUI(choices=["go"])
    agent = make_agent(repo, ui, language="ru")

    offer.offer_best_of(agent, offer_command(count=3))

    title, options = ui.questions[0]
    assert title == offer.question_text(agent, REASON, 3)
    assert options == [("best_of", "Запустить best-of (3)"), ("go", "Обычный прогон")]


# ---------------------------------------------------------------- the answer


def test_a_yes_ends_the_turn_with_a_short_final_and_leaves_the_task_to_the_next_turn(repo):
    agent = make_agent(repo, PlanUI(choices=["best_of"]))

    step = offer.offer_best_of(agent, offer_command(count=3))

    assert step.ended is True
    assert step.final_message == "Starting best-of: 3 attempts at your task. The result will wait for your decision."
    assert step.feedback["data"]["answer"] == "best_of"


def test_the_final_of_a_yes_is_in_russian_for_a_russian_session(repo):
    agent = make_agent(repo, PlanUI(choices=["best_of"]), language="ru")

    step = offer.offer_best_of(agent, offer_command())

    assert step.final_message.startswith("Запускаю best-of: 4 попытки")


def test_the_task_is_the_persons_own_message_and_the_count_defaults_to_four(repo):
    agent = make_agent(repo, PlanUI(choices=["best_of"]))

    offer.offer_best_of(agent, offer_command(reason="My own summary of the task."))

    assert offer.take_agreed(agent) == (DEFAULT_EXECUTORS, TASK)


def test_the_count_of_the_model_is_taken(repo):
    agent = make_agent(repo, PlanUI(choices=["best_of"]))

    offer.offer_best_of(agent, offer_command(count=6))

    assert offer.take_agreed(agent) == (6, TASK)


def test_a_yes_is_taken_once(repo):
    agent = make_agent(repo, PlanUI(choices=["best_of"]))
    offer.offer_best_of(agent, offer_command())

    assert offer.take_agreed(agent) is not None
    assert offer.take_agreed(agent) is None


def test_the_follow_up_is_the_best_of_command_with_the_count_then_the_task(repo):
    agent = make_agent(repo, PlanUI(choices=["best_of"]))
    offer.offer_best_of(agent, offer_command(count=2))

    assert take_follow_up(agent) == ("best-of", f"2 {TASK}")
    assert take_follow_up(agent) is None


@pytest.mark.parametrize("answer", ["go", None, "something else"])
def test_without_a_yes_the_run_goes_on_and_nothing_starts(repo, answer):
    agent = make_agent(repo, PlanUI(choices=[answer] if answer else []))

    step = offer.offer_best_of(agent, offer_command())

    assert step.ended is False
    assert step.feedback["data"]["answer"] == "go"
    assert "Do the task yourself" in step.feedback["data"]["note"]
    assert take_follow_up(agent) is None


def test_a_declined_offer_is_not_made_again_in_the_same_run_but_is_in_the_next(repo):
    agent = make_agent(repo, PlanUI(choices=["go"]))
    offer.offer_best_of(agent, offer_command())

    assert "already offered" in offer.unavailable_reason(agent)

    agent._reset_run_state()
    state = agent._state
    state.current_agent_role = "coder"
    state.message_id = end_session.next_message_id(agent, from_person=True)
    state.user_goal = TASK
    assert offer.unavailable_reason(agent) == ""


# ---------------------------------------------------------------- the action's arguments


@pytest.mark.parametrize("args", [{"reason": REASON}, {"reason": REASON, "count": 2}, {"reason": REASON, "count": 6}])
def test_valid_arguments_pass(args):
    validate_action_args("offer_best_of", dict(args))


@pytest.mark.parametrize(
    "args",
    [
        {},
        {"reason": " "},
        {"reason": REASON, "count": 1},
        {"reason": REASON, "count": 7},
        {"reason": REASON, "count": "4"},
        {"reason": REASON, "count": 4.0},
        {"reason": REASON, "count": True},
        {"reason": REASON, "extra": "keep me"},
    ],
)
def test_wrong_arguments_are_refused(args):
    with pytest.raises(CommandValidationError):
        validate_action_args("offer_best_of", args)


def test_the_parser_bounds_are_the_bounds_of_the_command():
    assert (MIN_BESTOF_ATTEMPTS, MAX_BESTOF_ATTEMPTS) == (MIN_EXECUTORS, MAX_EXECUTORS)


def test_the_native_tool_has_the_same_bounds_and_asks_only_for_the_reason():
    from kernel.agent.native_tools import tool_definition

    function = tool_definition("offer_best_of")["function"]
    count = function["parameters"]["properties"]["count"]

    assert function["parameters"]["required"] == ["reason"]
    assert (count["minimum"], count["maximum"]) == (MIN_EXECUTORS, MAX_EXECUTORS)


# ---------------------------------------------------------------- inside a run


def call(name: str, **arguments) -> str:
    return f'<tool_call>{json.dumps({"name": name, "arguments": arguments})}</tool_call>'


def scripted_run(agent: LocalAgent, responses: list[str], goal: str = TASK) -> tuple[str, list[str]]:
    prompts: list[str] = []

    def model(prompt: str) -> str:
        prompts.append(prompt)
        return responses[min(len(prompts) - 1, len(responses) - 1)]

    agent._call_action_model = model
    return agent.run(goal, routed_role="coder"), prompts


def fresh_agent(repo: Path, ui) -> LocalAgent:
    agent = LocalAgent(
        client=FakeLLMClient(),
        executor=SafeExecutor(repo),
        config=AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0),
        ui=ui,
    )
    agent._perf_logger = NullPerfLogger()
    agent.bestof_follow_up = True
    return agent


def test_a_yes_in_a_real_run_ends_it_without_touching_a_file_and_hands_the_task_on(repo):
    ui = PlanUI(choices=["best_of"])
    agent = fresh_agent(repo, ui)

    result, prompts = scripted_run(agent, [
        f"Two ways.\n{call('offer_best_of', reason=REASON, count=3)}",
        call("write_file", path="b.txt", content="must never be written\n"),
    ])

    assert result == "Starting best-of: 3 attempts at your task. The result will wait for your decision."
    assert len(prompts) == 1
    assert not (repo / "b.txt").exists()
    assert len(ui.questions) == 1
    assert take_follow_up(agent) == ("best-of", f"3 {TASK}")


def test_a_no_in_a_real_run_carries_on_with_the_task_and_does_not_ask_twice(repo):
    ui = PlanUI(choices=["go"])
    agent = fresh_agent(repo, ui)

    result, prompts = scripted_run(agent, [
        call("offer_best_of", reason=REASON),
        call("offer_best_of", reason=REASON),
        call("write_file", path="b.txt", content="done\n"),
        "Wrote b.txt.",
    ])

    assert (repo / "b.txt").read_text(encoding="utf-8") == "done\n"
    assert result == "Wrote b.txt."
    assert len(ui.questions) == 1
    assert "already offered" in prompts[2]
    assert take_follow_up(agent) is None


def test_the_offer_in_a_headless_run_is_turned_away_and_the_run_goes_on(repo):
    agent = fresh_agent(repo, NullUI())

    result, prompts = scripted_run(agent, [
        call("offer_best_of", reason=REASON),
        call("write_file", path="b.txt", content="done\n"),
        "Wrote b.txt.",
    ])

    assert (repo / "b.txt").exists()
    assert "Nobody can be asked" in prompts[1]
    assert result == "Wrote b.txt."


def test_the_guidance_is_in_the_system_prompt_where_the_tool_is(repo):
    from kernel.agent.agent_model import build_role_system_prompt

    asked = build_role_system_prompt(make_agent(repo), "coder")
    headless = build_role_system_prompt(make_agent(repo, NullUI()), "coder")

    # The action's one line in ALLOWED ACTIONS is in every prompt; the section itself only where the offer can happen.
    assert "when offer_best_of is among your tools" in asked
    assert "When offer_best_of is among your tools" not in headless


# ---------------------------------------------------------------- the follow-up turn over stdio-multi


class Stdio:
    """``serve_session_multi`` over a real agent whose model is scripted; the best-of work itself is a fake runner."""

    def __init__(self, repo: Path, responses: list[str], monkeypatch) -> None:
        self.asked: list[tuple[str, str]] = []

        def runner(agent, question: str) -> TurnResult:
            self.asked.append((agent._state.user_goal, question))
            return TurnResult(True, "best-of: done / best-of: готово", "## best-of\n\nfake result")

        monkeypatch.setattr(turns, "turn_runner", lambda name: runner)
        channel = FrontendChannel()
        self.agent = LocalAgent(
            FakeLLMClient(), SafeExecutor(repo),
            AgentConfig(persist_logs=False, api_request_interval=0, retry_base_delay=0, max_steps=8, stream=False),
            ui=EventStreamUI(channel),
        )
        self.agent._perf_logger = NullPerfLogger()
        calls: list[str] = []

        def model(prompt: str) -> str:
            calls.append(prompt)
            return responses[min(len(calls) - 1, len(responses) - 1)]

        self.agent._call_action_model = model
        self.reader = _ScriptedReader()
        self.out = _Collector()
        self.thread = threading.Thread(
            target=event_stdio.serve_session_multi,
            kwargs={"session": EventSession(channel, self.agent), "out_stream": self.out, "in_reader": self.reader,
                    "routed_role": "coder"},
            daemon=True,
        )
        self.thread.start()

    def answer(self, value: str) -> dict:
        request = self.out.wait_for(lambda event: event["type"] == "input_request")
        self.reader.push(json.dumps({"request_id": request["request_id"], "value": value}))
        return request

    def types(self) -> list[str]:
        return [event["type"] for event in self.out.events() if event["type"] != "task_state"]

    def finish(self) -> None:
        self.reader.eof()
        self.thread.join(5)
        assert not self.thread.is_alive()


def test_the_loop_tells_the_model_it_can_start_the_follow_up_turn(repo, monkeypatch):
    stdio = Stdio(repo, ["fine"], monkeypatch)
    stdio.reader.push("hi")
    stdio.out.wait_for(lambda event: event["type"] == "turn_end")
    stdio.finish()

    assert stdio.agent.bestof_follow_up is True


def test_a_yes_over_stdio_runs_a_best_of_turn_with_the_persons_text_after_the_first_turn_ends(repo, monkeypatch):
    stdio = Stdio(repo, [call("offer_best_of", reason=REASON, count=3), "never asked"], monkeypatch)

    stdio.reader.push(TASK)
    dialog = stdio.answer("best_of")
    result = stdio.out.wait_for(lambda event: event["type"] == "command_result" and event["name"] == "best-of", 10)
    stdio.finish()

    # What the person was asked is a plain choice with the multiple in it.
    assert dialog["kind"] == "choice" and "Roughly 4 times" in dialog["body"] + dialog["title"]
    assert [option["value"] for option in dialog["options"]] == ["best_of", "go"]
    types = stdio.types()
    assert types[-5:] == ["final", "turn_end", "final", "turn_end", "command_result"]
    finals = [event for event in stdio.out.events() if event["type"] == "final"]
    assert finals[0]["message"].startswith("Starting best-of: 3 attempts")
    assert finals[1]["message"].startswith("## best-of")
    assert stdio.asked == [(f"/best-of 3 {TASK}", f"3 {TASK}")]
    assert result["ok"] is True
    assert not (repo / "b.txt").exists()


def test_a_no_over_stdio_carries_on_and_starts_no_best_of(repo, monkeypatch):
    stdio = Stdio(repo, [
        call("offer_best_of", reason=REASON),
        call("write_file", path="b.txt", content="done\n"),
        "Wrote b.txt.",
    ], monkeypatch)

    stdio.reader.push(TASK)
    stdio.answer("go")
    stdio.out.wait_for(lambda event: event["type"] == "turn_end", 10)
    stdio.finish()

    assert (repo / "b.txt").read_text(encoding="utf-8") == "done\n"
    assert stdio.asked == []
    assert "command_result" not in stdio.types()
    assert stdio.types().count("turn_end") == 1


def test_a_closed_dialog_over_stdio_counts_as_a_no(repo, monkeypatch):
    stdio = Stdio(repo, [call("offer_best_of", reason=REASON), "Carried on."], monkeypatch)

    stdio.reader.push(TASK)
    stdio.answer("")
    stdio.out.wait_for(lambda event: event["type"] == "turn_end", 10)
    stdio.finish()

    assert stdio.asked == []
    assert stdio.types().count("turn_end") == 1


def test_the_next_message_after_a_best_of_follow_up_is_an_ordinary_turn(repo, monkeypatch):
    stdio = Stdio(repo, [call("offer_best_of", reason=REASON), "Second answer."], monkeypatch)

    stdio.reader.push(TASK)
    stdio.answer("best_of")
    stdio.out.wait_for(lambda event: event["type"] == "command_result", 10)
    stdio.reader.push("what is in a.txt?")
    stdio.out.wait_for(lambda event: event["type"] == "final" and event["message"] == "Second answer.", 10)
    stdio.finish()

    # One best-of turn, not a second one for the new message; the offer is not made again, so no dialog either.
    assert len(stdio.asked) == 1
    assert [event["type"] for event in stdio.out.events()].count("input_request") == 1
