# -*- coding: utf-8 -*-
"""The reviewer of a best-of run: the ``executor`` and ``verdict`` actions, their checks, and the role's limits.

``JudgeTools`` is tried straight on a pool of real executors (scripted model, real git worktrees); the rest goes
through the whole ``/best-of`` turn, where the reviewer is the main agent with the role ``judge``.
"""
from __future__ import annotations

import json
import re
import threading

import pytest

from kernel.agent.agent_model import _load_role_prompt
from kernel.agent.call_labels import call_label
from kernel.agent.native_tools import ACTION_TOOLS, tool_definition, tools_for_role
from kernel.bestof import worktrees
from kernel.bestof.executors import ExecutorPool, executor_ids
from kernel.bestof.judge import JUDGE_ACTIONS, JUDGE_ROLE, JudgeTools, run_judge_action
from kernel.bestof.styles import styles_for
from kernel.protocol.parser import CommandValidationError, build_command, validate_action_args
from kernel.protocol.protocol import Action, AgentCommand, ToolCallRequest
from kernel.tests.test_bestof_executors import CheckSeam
from kernel.tests.test_bestof_run import (  # noqa: F401 - the fixtures and helpers the scenarios share
    E1_WORK,
    RUN_TASK,
    WAIT,
    ReviewClient,
    _text_protocol,
    best_of,
    call,
    commits,
    git,
    make_main,
    open_when_stopped,
    record_pools,
    repo,
    verdict_call,
    wait_until,
    write,
)


def command(action_type: str, **arguments) -> AgentCommand:
    return AgentCommand("review", Action(action_type, arguments))


def make_pool(agent_factory, repo, client=None, count=2):
    main, watching = make_main(agent_factory, client or ReviewClient())
    main.control.begin()
    run = worktrees.create_run(repo, "run1", executor_ids(count))
    pool = ExecutorPool(main, run, RUN_TASK, styles_for(count))
    pool.start()
    return main, watching, pool, JudgeTools(pool)


def good_rows(**changes):
    rows = [{"executor_id": "e1", "outcome": "winner", "note": "best"},
            {"executor_id": "e2", "outcome": "finalist", "note": "fine"}]
    for index, outcome in changes.items():
        rows[int(index[1:]) - 1] = {**rows[int(index[1:]) - 1], "outcome": outcome}
    return rows


# --- the actions as the parser and the tool channel see them ----------------------------------------


def build(name: str, arguments: dict) -> AgentCommand:
    return build_command(ToolCallRequest("c1", name, arguments), text="", batch_id="b", plan_step="inspect",
                         language="en")


@pytest.mark.parametrize("arguments", [
    {"op": "status"},
    {"op": "wait"},
    {"op": "wait", "executor_id": "e1", "timeout": 30},
    {"op": "inspect", "executor_id": "e1", "cursor": 0, "limit": 10},
    {"op": "message", "executor_id": "e1", "text": "use the existing helper"},
    {"op": "start", "executor_id": "e1", "instruction": "also update the docs"},
    {"op": "diff", "executor_id": "e1"},
    {"op": "diff", "executor_id": "e1", "path": "a.txt"},
    {"op": "check", "executor_id": "e1"},
    {"op": "check", "executor_id": "e1", "command": ["python3", "-m", "pytest", "-q"]},
    {"op": "set_check", "command": ["python3", "-m", "pytest", "-q"]},
    {"op": "eliminate", "executor_id": "e2", "reason": "deleted the failing test"},
])
def test_every_executor_op_with_its_own_arguments_passes_the_parser(arguments):
    built = build("executor", arguments)

    assert built.action.type == "executor" and built.action.args == arguments


@pytest.mark.parametrize(("arguments", "message"), [
    ({}, "'op' must be one of"),
    ({"op": "kill", "executor_id": "e1"}, "'op' must be one of"),
    ({"op": "message", "executor_id": "e1"}, "Missing required fields: text"),
    ({"op": "eliminate", "executor_id": "e1"}, "Missing required fields: reason"),
    ({"op": "eliminate", "reason": "x"}, "Missing required fields: executor_id"),
    ({"op": "start", "executor_id": "e1"}, "Missing required fields: instruction"),
    ({"op": "set_check"}, "Missing required fields: command"),
    ({"op": "set_check", "command": []}, "at least one string"),
    ({"op": "set_check", "command": "pytest -q"}, "'command' must be a list"),
    ({"op": "check", "executor_id": "e1", "command": ["ok", 3]}, "'command' must be a list"),
    ({"op": "status", "executor_id": "e1"}, "Extra fields: executor_id"),
    ({"op": "wait", "timeout": 9000}, "'timeout' must be a number in"),
    ({"op": "wait", "timeout": "soon"}, "'timeout' must be a number in"),
    ({"op": "inspect", "executor_id": "e1", "limit": 0}, "'limit' must be an integer"),
    ({"op": "diff", "executor_id": "e1", "path": 3}, "'path' must be a string"),
])
def test_an_executor_call_that_does_not_match_its_op_is_refused_with_the_reason(arguments, message):
    with pytest.raises(CommandValidationError, match=message):
        build("executor", arguments)


def test_a_verdict_is_rows_a_winner_and_a_reason_and_nothing_else():
    arguments = {"rows": good_rows(), "winner": "e1", "reason": "e1 is smaller."}

    assert build("verdict", arguments).action.args == arguments


@pytest.mark.parametrize(("arguments", "message"), [
    ({"winner": "e1", "reason": "x"}, "Missing required fields: rows"),
    ({"rows": [], "winner": "e1", "reason": "x"}, "'rows' must be a non-empty list"),
    ({"rows": "e1", "winner": "e1", "reason": "x"}, "'rows' must be a non-empty list"),
    ({"rows": ["e1"], "winner": "e1", "reason": "x"}, "every row"),
    ({"rows": [{"executor_id": "e1", "outcome": "best"}], "winner": "e1", "reason": "x"}, "'outcome' must be one of"),
    ({"rows": [{"outcome": "winner"}], "winner": "e1", "reason": "x"}, "Missing required fields: executor_id"),
    ({"rows": [{"executor_id": "e1", "outcome": "winner", "style": "paranoid"}], "winner": "e1", "reason": "x"},
     "Extra fields: style"),
    ({"rows": [{"executor_id": "e1", "outcome": "winner"}], "winner": "e1", "reason": "  "}, "'reason' must be"),
    ({"rows": [{"executor_id": "e1", "outcome": "winner"}], "reason": "x"}, "Missing required fields: winner"),
    ({"rows": [{"executor_id": "e1", "outcome": "winner"}], "winner": "e1", "reason": "x", "tests": "ok"},
     "Extra fields: tests"),
])
def test_a_malformed_verdict_is_refused_before_it_runs_and_the_reviewer_cannot_state_style_tests_or_diff(
        arguments, message):
    with pytest.raises(CommandValidationError, match=message):
        validate_action_args("verdict", arguments)


def test_the_two_actions_are_native_tools_with_every_op_and_the_rows_schema_in_them():
    executor = tool_definition("executor")["function"]["parameters"]
    verdict = tool_definition("verdict")["function"]["parameters"]

    assert executor["properties"]["op"]["enum"] == ["status", "wait", "inspect", "message", "start", "diff", "check",
                                                    "set_check", "eliminate"]
    assert executor["required"] == ["op"] and executor["additionalProperties"] is False
    assert verdict["required"] == ["rows", "winner", "reason"]
    row = verdict["properties"]["rows"]["items"]
    assert row["properties"]["outcome"]["enum"] == ["winner", "finalist", "eliminated", "failed"]
    assert set(ACTION_TOOLS) >= JUDGE_ACTIONS


def test_only_the_reviewers_role_prompt_offers_the_two_actions_to_the_model():
    names = {role: {tool["function"]["name"] for tool in tools_for_role(_load_role_prompt(role))}
             for role in ("judge", "coder", "reader", "devops", "universal")}

    assert names["judge"] == {"final_answer", "read_file", "read_multiple_files", "list_dir", "list_tree",
                              "find_files", "search_code", "git_status", "git_diff", "executor", "verdict"}
    for role in ("coder", "reader", "devops", "universal"):
        assert not names[role] & JUDGE_ACTIONS
    assert not names["judge"] & {"write_file", "append_file", "replace_in_file", "run_shell", "delegate_task",
                                 "helper", "web_search", "fetch_url"}


@pytest.mark.parametrize(("arguments", "english", "russian"), [
    ({"op": "status"}, "checks how the variants are doing", "смотрит итоги вариантов"),
    ({"op": "wait"}, "waits for the variants", "ждёт варианты"),
    ({"op": "wait", "executor_id": "e2"}, "waits for variant e2", "ждёт вариант e2"),
    ({"op": "inspect", "executor_id": "e2"}, "looks at what variant e2 did", "смотрит, что делал вариант e2"),
    ({"op": "message", "executor_id": "e2", "text": "x"}, "sends a note to variant e2", "пишет варианту e2"),
    ({"op": "start", "executor_id": "e2", "instruction": "x"}, "gives variant e2 more work",
     "даёт варианту e2 новую работу"),
    ({"op": "diff", "executor_id": "e2"}, "reads the diff of variant e2", "читает diff варианта e2"),
    ({"op": "check", "executor_id": "e2"}, "runs the check on variant e2", "запускает проверку варианта e2"),
    ({"op": "set_check", "command": ["pytest"]}, "picks the check command", "выбирает команду проверки"),
    ({"op": "eliminate", "executor_id": "e3", "reason": "x"}, "drops variant e3 from the comparison",
     "убирает вариант e3 из сравнения"),
])
def test_a_step_of_the_review_is_labelled_for_the_person_in_terms_of_variants(arguments, english, russian):
    assert build("executor", arguments).action.description == english
    built_ru = build_command(ToolCallRequest("c1", "executor", dict(arguments)), text="", batch_id="b",
                             plan_step="inspect", language="ru")
    assert built_ru.action.description == russian


def test_the_verdict_has_a_label_and_no_label_names_the_reviewer_or_the_executors():
    verdict = {"rows": good_rows(), "winner": "e1", "reason": "x"}
    labels = [build("verdict", verdict).action.description]
    for language in ("en", "ru"):
        for op in ("status", "wait", "inspect", "message", "start", "diff", "check", "set_check", "eliminate"):
            arguments = {"op": op, "executor_id": "e1", "text": "x", "instruction": "x", "reason": "x",
                         "command": ["x"]}
            labels.append(call_label("executor", arguments, language))
        labels.append(call_label("verdict", verdict, language))

    assert labels[0] == "gives the verdict" and call_label("verdict", verdict, "ru") == "выносит вердикт"
    for label in labels:
        assert label and not re.search(r"judge|executor|судь|исполнител", label, re.IGNORECASE), label


# --- who may call them ------------------------------------------------------------------------------


def test_the_actions_exist_for_the_reviewer_only_and_only_while_a_review_is_under_way(agent_factory, repo):
    main, _, pool, tools = make_pool(agent_factory, repo)
    pool.wait(30)
    status = command("executor", op="status")

    # No review under way: the agent has no tools for it.
    refused = run_judge_action(main, status)
    assert refused["ok"] is False and "belongs to the review of a best-of run" in refused["error"]
    # A review under way, but the agent is not in the reviewer's role.
    main.judge_tools = tools
    main._state.current_agent_role = "coder"
    assert run_judge_action(main, status)["ok"] is False
    assert run_judge_action(main, command("verdict", rows=good_rows(), winner="e1", reason="x"))["ok"] is False
    main._state.current_agent_role = JUDGE_ROLE
    assert run_judge_action(main, status)["ok"] is True
    # The runtime's own entry point hands them over the same way.
    assert main._execute_command(status)["ok"] is True
    main._state.current_agent_role = "reader"
    assert main._execute_command(status)["ok"] is False


def test_the_reviewer_cannot_write_a_file_and_is_told_so_in_the_result_of_the_call(agent_factory, repo):
    client = ReviewClient({"judge": [write("evil.txt", "changed by the reviewer\n"), WAIT, verdict_call()]})
    main, watching = make_main(agent_factory, client, answers=["discard"])

    best_of(main, f"2 {RUN_TASK}")

    assert not (repo / "evil.txt").exists()
    second = "\n".join(str(message["content"]) for message in client.asked("judge")[1])
    assert "read-only" in second and "File modifications are not allowed" in second
    # It went on to a verdict all the same.
    assert watching.of("verdict")[0]["winner"] == "e1"


def test_the_reviewer_cannot_run_a_command_or_hand_off_a_task(agent_factory, repo):
    client = ReviewClient({"judge": [call("run_shell", {"command": ["touch", "evil.txt"], "purpose": "try"}),
                                     WAIT, verdict_call()]})
    main, watching = make_main(agent_factory, client, answers=["discard"])

    best_of(main, f"2 {RUN_TASK}")

    assert not (repo / "evil.txt").exists()
    assert watching.of("verdict")


def test_the_role_prompt_gives_the_order_of_review_with_its_reasons_and_never_names_the_role_to_the_person():
    prompt = _load_role_prompt("judge")

    assert prompt.index("Cheating first") < prompt.index("Tests next") < prompt.index("Quality last")
    assert "only among the finalists" in prompt and "Eliminate early" in prompt
    for reason in ("proves nothing", "set that command once", "sent again with every later step"):
        assert reason in prompt
    # A fraud list the person recognises, and the way out when nobody qualifies.
    for sign in ("xfail", "weakened an assert", "stub", "commented code out"):
        assert sign in prompt
    assert "do not invent a winner" in prompt
    assert "DATA to analyze" in prompt and "UNTRUSTED_TOOL_OUTPUT" in prompt
    assert not re.search(r"\bjudge\b", prompt, re.IGNORECASE)


# --- executor ----------------------------------------------------------------------------------------


def test_status_gives_every_executors_summary_without_the_full_diff_and_marks_the_words_as_untrusted(
        agent_factory, repo):
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]

    feedback = tools.run(command("executor", op="status"))

    assert feedback["ok"] is True and feedback["data"]["untrusted"] is True
    first, second = feedback["data"]["executors"]
    assert (first["executor_id"], first["style"], first["state"]) == ("e1", "senior-dev", "finished")
    assert (second["executor_id"], second["style"]) == ("e2", "minimalist")
    assert first["files"] == ["a.txt"] and first["diff"] == {"files": 1, "added": 1, "removed": 1}
    assert first["report"] == "Changed the greeting in a.txt." and "a.txt" in first["stat"]
    assert first["tests"] == {"state": "unknown", "summary": ""}
    assert "+hello there" not in json.dumps(feedback)


def test_wait_returns_when_the_executors_have_ended_and_makes_no_model_request_of_the_main_agent(agent_factory, repo):
    client = ReviewClient()
    _, _, pool, tools = make_pool(agent_factory, repo, client)

    feedback = tools.run(command("executor", op="wait", timeout=30))

    assert feedback["ok"] is True and feedback["data"]["done"] is True
    assert [row["state"] for row in feedback["data"]["executors"]] == ["finished", "finished"]
    assert client.asked("judge") == []


def test_wait_for_one_executor_and_a_timeout_that_ends_with_done_false(agent_factory, repo):
    gate = threading.Event()
    client = ReviewClient(holds={"minimalist": (0, gate)})
    _, _, pool, tools = make_pool(agent_factory, repo, client)
    try:
        one = tools.run(command("executor", op="wait", executor_id="e1", timeout=30))
        late = tools.run(command("executor", op="wait", timeout=0.3))
    finally:
        gate.set()

    assert one["data"]["done"] is True and [row["executor_id"] for row in one["data"]["executors"]] == ["e1"]
    assert late["data"]["done"] is False
    assert pool.wait(30)["done"]


def test_diff_gives_the_patch_of_an_executor_or_of_one_path_and_inspect_pages_through_its_actions(agent_factory, repo):
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]

    whole = tools.run(command("executor", op="diff", executor_id="e1"))
    one_path = tools.run(command("executor", op="diff", executor_id="e1", path="a.txt"))
    nothing = tools.run(command("executor", op="diff", executor_id="e1", path="zzz.txt"))
    pages = tools.run(command("executor", op="inspect", executor_id="e1", cursor=0, limit=1))

    assert "+hello there" in whole["data"]["patch"] and whole["data"]["untrusted"] is True
    assert one_path["data"]["patch"] == whole["data"]["patch"] and nothing["data"]["patch"] == ""
    assert pages["ok"] is True and pages["data"]["untrusted"] is True
    assert [item["action"] for item in pages["data"]["items"]] == ["write_file"] and pages["data"]["has_more"] is True


def test_set_check_runs_on_the_executors_and_check_runs_it_now_with_the_result_in_the_feedback(
        agent_factory, repo, monkeypatch):
    seam = CheckSeam(monkeypatch)
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]

    unset = tools.run(command("executor", op="check", executor_id="e1"))
    assert unset["ok"] is False and "no check command" in unset["error"]
    set_it = tools.run(command("executor", op="set_check", command=[seam.MARKER]))
    ran = tools.run(command("executor", op="check", executor_id="e1"))
    other = tools.run(command("executor", op="check", executor_id="e2", command=[seam.MARKER, "-x"]))

    assert set_it["ok"] is True and set_it["data"]["check"] == [seam.MARKER]
    assert ran["data"]["state"] == "passed" and "3 passed" in ran["data"]["summary"] and ran["data"]["untrusted"] is True
    assert other["data"]["state"] == "passed" and seam.calls[-1][1]["command"] == [seam.MARKER, "-x"]
    assert pool.summary("e1")["tests"]["state"] == "passed"


def test_a_refusal_of_the_pool_comes_back_as_an_error_for_the_reviewer_not_as_a_crash(agent_factory, repo):
    gate = threading.Event()
    client = ReviewClient(holds={"senior-dev": (0, gate)})
    _, _, pool, tools = make_pool(agent_factory, repo, client)
    try:
        unknown = tools.run(command("executor", op="diff", executor_id="e9"))
        message = tools.run(command("executor", op="message", executor_id="e2", text="hello"))
        working_diff = tools.run(command("executor", op="diff", executor_id="e1"))
        restart = tools.run(command("executor", op="start", executor_id="e1", instruction="again"))
    finally:
        gate.set()

    assert unknown["ok"] is False and "no executor 'e9'" in unknown["error"]
    assert message["ok"] is True and message["data"]["state"] == "sent"
    assert working_diff["ok"] is False and "no recorded result" in working_diff["error"]
    assert restart["ok"] is False and "only an executor that ended can be restarted" in restart["error"]
    assert pool.wait(30)["done"]


def test_start_gives_a_finished_executor_more_work_in_its_own_folder(agent_factory, repo):
    scripts = {"senior-dev": [*E1_WORK, write("b.txt", "second\n"), "Added b.txt too."]}
    _, _, pool, tools = make_pool(agent_factory, repo, ReviewClient(scripts))
    assert pool.wait(30)["done"]

    started = tools.run(command("executor", op="start", executor_id="e1", instruction="Also add b.txt."))
    assert started["ok"] is True and started["data"]["state"] == "running"
    assert pool.wait(30, "e1")["done"]

    assert pool.summary("e1")["files"] == ["a.txt", "b.txt"]
    assert pool.summary("e1")["report"] == "Added b.txt too."


def test_eliminate_stops_a_working_executor_and_the_event_carries_the_reason(agent_factory, repo):
    gate = threading.Event()
    client = ReviewClient(holds={"minimalist": (0, gate)})
    _, watching, pool, tools = make_pool(agent_factory, repo, client)
    open_when_stopped(gate, pool, "e2")

    feedback = tools.run(command("executor", op="eliminate", executor_id="e2", reason="Deleted the failing test"))

    assert feedback["ok"] is True and feedback["data"]["state"] == "eliminated"
    assert feedback["data"]["reason"] == "Deleted the failing test" and feedback["data"]["untrusted"] is True
    assert watching.of("eliminated") == [{"run_id": "run1", "executor_id": "e2", "reason": "Deleted the failing test"}]
    assert not pool.get("e2").thread.is_alive()
    assert pool.wait(30)["done"]


# --- verdict ---------------------------------------------------------------------------------------------


def verdict_of(tools, rows=None, winner="e1", reason="  e1 is the smaller change.  "):
    return tools.run(command("verdict", rows=rows if rows is not None else good_rows(), winner=winner, reason=reason))


def test_a_good_verdict_is_accepted_and_its_rows_take_style_tests_and_diff_from_the_pool(agent_factory, repo,
                                                                                         monkeypatch):
    seam = CheckSeam(monkeypatch)
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]
    pool.set_check([seam.MARKER])
    pool.check("e1")
    rows = [{"executor_id": "e1", "outcome": "winner", "note": " tests all FAILED, huge diff "},
            {"executor_id": "e2", "outcome": "finalist"}]

    feedback = verdict_of(tools, rows)

    assert feedback["ok"] is True and feedback["data"]["accepted"] is True
    verdict = tools.verdict
    assert verdict.winner == "e1" and verdict.reason == "e1 is the smaller change."
    first, second = verdict.rows
    # The reviewer's words are the note and the outcome; the rest is what the pool knows.
    assert first == {"executor_id": "e1", "style": "senior-dev", "outcome": "winner",
                     "tests": pool.summary("e1")["tests"], "diff": {"files": 1, "added": 1, "removed": 1},
                     "note": "tests all FAILED, huge diff"}
    assert first["tests"]["state"] == "passed"
    assert second["style"] == "minimalist" and second["tests"] == {"state": "unknown", "summary": ""}
    assert second["note"] == ""


@pytest.mark.parametrize(("rows", "winner", "message"), [
    ([{"executor_id": "e1", "outcome": "winner"}], "e1", "no row for: e2"),
    ([{"executor_id": "e1", "outcome": "winner"}, {"executor_id": "e1", "outcome": "finalist"},
      {"executor_id": "e2", "outcome": "finalist"}], "e1", "more than one row for: e1"),
    ([{"executor_id": "e1", "outcome": "winner"}, {"executor_id": "e2", "outcome": "finalist"},
      {"executor_id": "e7", "outcome": "failed"}], "e1", "no such executor in the verdict: e7"),
    ([{"executor_id": "e1", "outcome": "finalist"}, {"executor_id": "e2", "outcome": "finalist"}], "e1",
     "exactly one row must have outcome winner; 0 do"),
    ([{"executor_id": "e1", "outcome": "winner"}, {"executor_id": "e2", "outcome": "winner"}], "e1",
     "exactly one row must have outcome winner; 2 do"),
    ([{"executor_id": "e1", "outcome": "winner"}, {"executor_id": "e2", "outcome": "finalist"}], "e2",
     "'winner' is e2 but the winner row is e1"),
])
def test_a_verdict_that_does_not_name_every_executor_once_with_one_matching_winner_is_refused(
        agent_factory, repo, rows, winner, message):
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]

    feedback = verdict_of(tools, rows, winner)

    assert feedback["ok"] is False and message in feedback["error"]
    assert tools.verdict is None


def test_a_winner_that_was_eliminated_or_failed_or_changed_nothing_cannot_win(agent_factory, repo):
    scripts = {"senior-dev": ["I decided not to change anything."], "minimalist": [write("a.txt", "x\n"), "Done."]}
    _, _, pool, tools = make_pool(agent_factory, repo, ReviewClient(scripts))
    assert pool.wait(30)["done"]

    empty = verdict_of(tools, good_rows(), "e1")
    pool.eliminate("e2", "dropped")
    dropped = verdict_of(tools, [{"executor_id": "e1", "outcome": "finalist"},
                                 {"executor_id": "e2", "outcome": "winner"}], "e2")

    assert empty["ok"] is False and "e1 made no changes" in empty["error"]
    assert dropped["ok"] is False and "e2 is eliminated, not finished" in dropped["error"]
    assert tools.verdict is None


def test_an_empty_finalist_is_refused_too_but_an_eliminated_row_may_be_empty(agent_factory, repo):
    scripts = {"minimalist": ["I decided not to change anything."]}
    _, _, pool, tools = make_pool(agent_factory, repo, ReviewClient(scripts))
    assert pool.wait(30)["done"]

    as_finalist = verdict_of(tools, good_rows())
    as_eliminated = verdict_of(tools, [{"executor_id": "e1", "outcome": "winner"},
                                       {"executor_id": "e2", "outcome": "eliminated", "note": "nothing changed"}])

    assert as_finalist["ok"] is False and "e2 made no changes" in as_finalist["error"]
    assert as_eliminated["ok"] is True and tools.verdict.rows[1]["diff"] == {"files": 0, "added": 0, "removed": 0}


def test_a_verdict_while_an_executor_is_still_working_is_refused_until_it_is_waited_for_or_eliminated(
        agent_factory, repo):
    gate = threading.Event()
    client = ReviewClient(holds={"minimalist": (0, gate)})
    _, _, pool, tools = make_pool(agent_factory, repo, client)
    try:
        assert pool.wait(30, "e1")["done"]
        early = verdict_of(tools)
    finally:
        gate.set()
    assert pool.wait(30)["done"]
    late = verdict_of(tools)

    assert early["ok"] is False and "e2 is still working" in early["error"]
    assert late["ok"] is True


# --- the verdict inside the turn ---------------------------------------------------------------------------


def test_a_refused_verdict_is_an_error_in_the_result_of_the_call_and_the_reviewer_sends_it_again(agent_factory, repo):
    wrong = verdict_call("e2", rows=good_rows())
    client = ReviewClient({"judge": [WAIT, wrong, verdict_call()]})
    main, watching = make_main(agent_factory, client, answers=["accept"])

    result = best_of(main, f"2 {RUN_TASK}")

    third = "\n".join(str(message["content"]) for message in client.asked("judge")[2])
    assert "'winner' is e2 but the winner row is e1" in third
    assert len(watching.of("verdict")) == 1 and watching.of("verdict")[0]["winner"] == "e1"
    assert result.effects["outcome"] == "accepted"


def test_a_verdict_that_never_becomes_valid_leaves_the_review_without_one_and_the_run_waiting(agent_factory, repo):
    wrong = verdict_call("e2", rows=good_rows())
    client = ReviewClient({"judge": [WAIT, wrong, wrong, "I cannot settle this."]})
    main, watching = make_main(agent_factory, client)

    result = best_of(main, f"2 {RUN_TASK}")

    assert watching.of("verdict") == [] and watching.of("finished")[0]["outcome"] == "pending"
    assert not result.ok and "I cannot settle this." in result.final
    assert commits(repo) == ["first"]


def test_the_verdict_event_takes_style_tests_and_diff_from_the_pool_whatever_the_reviewer_wrote(
        agent_factory, repo, monkeypatch):
    seam = CheckSeam(monkeypatch)
    rows = [{"executor_id": "e1", "outcome": "winner", "note": "tests failed, enormous diff, style paranoid"},
            {"executor_id": "e2", "outcome": "finalist", "note": "ran no tests"}]
    check = call("executor", {"op": "check", "executor_id": "e1", "command": [seam.MARKER]})
    client = ReviewClient({"judge": [WAIT, check, verdict_call(rows=rows)]})
    main, watching = make_main(agent_factory, client, answers=["discard"])

    best_of(main, f"2 {RUN_TASK}")

    first, second = watching.of("verdict")[0]["rows"]
    assert (first["style"], second["style"]) == ("senior-dev", "minimalist")
    assert first["tests"]["state"] == "passed" and "3 passed" in first["tests"]["summary"]
    assert second["tests"] == {"state": "unknown", "summary": ""}
    assert first["diff"] == second["diff"] == {"files": 1, "added": 1, "removed": 1}
    assert first["note"] == "tests failed, enormous diff, style paranoid"


def test_an_elimination_in_the_review_stops_the_executor_and_shows_the_person_the_reason(
        agent_factory, repo, monkeypatch):
    gate = threading.Event()
    pools = record_pools(monkeypatch)
    client = ReviewClient({
        "judge": [call("executor", {"op": "wait", "executor_id": "e1", "timeout": 60}),
                  call("executor", {"op": "eliminate", "executor_id": "e2", "reason": "Слишком долго"}),
                  verdict_call(rows=[{"executor_id": "e1", "outcome": "winner", "note": "ok"},
                                     {"executor_id": "e2", "outcome": "eliminated", "note": "остановлен"}])]},
        holds={"minimalist": (0, gate)})
    main, watching = make_main(agent_factory, client, answers=["accept"])

    def open_once_the_pool_exists():
        wait_until(lambda: bool(pools))
        open_when_stopped(gate, pools[0], "e2")

    threading.Thread(target=open_once_the_pool_exists, daemon=True).start()

    best_of(main, f"2 {RUN_TASK}")

    assert watching.of("eliminated") == [{"run_id": watching.of("started")[0]["run_id"], "executor_id": "e2",
                                          "reason": "Слишком долго"}]
    assert watching.kinds() == ["started", "eliminated", "verdict", "decision", "finished"]
    final_states = {}
    for payload in watching.of("status"):
        final_states[payload["executor_id"]] = payload["state"]
    assert final_states["e2"] == "eliminated"
    assert watching.of("verdict")[0]["rows"][1]["outcome"] == "eliminated"
    assert commits(repo) == [RUN_TASK, "first"]


# --- a verdict with no winner, and a winner whose tests fail ----------------------------------------


def _checked(agent_factory, repo, monkeypatch, states):
    """A two-executor pool whose executors were checked with the given ``passed``/``failed`` results."""
    from kernel.sandbox.executor import ExecutionResult

    seam = CheckSeam(monkeypatch)
    main, watching, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]
    for executor_id, state in states.items():
        passed = state == "passed"
        seam.result = ExecutionResult(ok=passed, action_type="run_shell", data={
            "command": [seam.MARKER], "returncode": 0 if passed else 1,
            "stdout": "1 passed\n" if passed else "1 failed\n", "stderr": "", "timed_out": False})
        pool.check(executor_id, [seam.MARKER])
    return pool, tools


NO_WINNER_ROWS = [{"executor_id": "e1", "outcome": "failed", "note": "does not solve it"},
                  {"executor_id": "e2", "outcome": "eliminated", "note": "does not solve it"}]


def test_a_verdict_may_name_no_winner_when_it_says_why(agent_factory, repo):
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]

    feedback = verdict_of(tools, NO_WINNER_ROWS, winner=None, reason="  The task cannot be done.  ")

    assert feedback["ok"] is True and feedback["data"]["winner"] is None
    assert tools.verdict.winner == "" and tools.verdict.reason == "The task cannot be done."
    assert [row["outcome"] for row in tools.verdict.rows] == ["failed", "eliminated"]


def test_an_empty_string_winner_means_no_winner_too(agent_factory, repo):
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]

    assert verdict_of(tools, NO_WINNER_ROWS, winner="", reason="Nothing works.")["ok"] is True
    assert tools.verdict.winner == ""


def test_no_winner_with_a_winner_row_or_without_a_reason_is_refused(agent_factory, repo):
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]

    with_row = verdict_of(tools, good_rows(), winner=None, reason="Nothing works.")
    no_reason = verdict_of(tools, NO_WINNER_ROWS, winner=None, reason="   ")

    assert with_row["ok"] is False and "e1 has outcome winner" in with_row["error"]
    assert no_reason["ok"] is False and "needs a reason" in no_reason["error"]
    assert tools.verdict is None


def test_a_finalist_of_a_verdict_without_a_winner_must_still_have_finished_with_changes(agent_factory, repo):
    _, _, pool, tools = make_pool(agent_factory, repo)
    assert pool.wait(30)["done"]
    pool.eliminate("e2", "out")

    feedback = verdict_of(tools, [{"executor_id": "e1", "outcome": "eliminated"},
                                  {"executor_id": "e2", "outcome": "finalist"}], winner=None, reason="Nothing works.")

    assert feedback["ok"] is False and "e2 is eliminated, not finished" in feedback["error"]


def test_a_winner_with_failing_tests_is_refused_while_a_finalist_passes(agent_factory, repo, monkeypatch):
    _, tools = _checked(agent_factory, repo, monkeypatch, {"e1": "failed", "e2": "passed"})

    feedback = verdict_of(tools, good_rows())

    assert feedback["ok"] is False
    assert "tests of e1 fail while those of e2 pass" in feedback["error"]
    assert tools.verdict is None


def test_a_winner_with_failing_tests_is_allowed_when_every_finalist_fails_too(agent_factory, repo, monkeypatch):
    _, tools = _checked(agent_factory, repo, monkeypatch, {"e1": "failed", "e2": "failed"})

    assert verdict_of(tools, good_rows())["ok"] is True
    assert tools.verdict.winner == "e1" and tools.verdict.rows[0]["tests"]["state"] == "failed"


def test_a_passing_rival_that_the_reviewer_eliminated_does_not_block_a_failing_winner(agent_factory, repo, monkeypatch):
    # A green run on a diff that cheats proves nothing, so the cheater is not "left in the comparison".
    _, tools = _checked(agent_factory, repo, monkeypatch, {"e1": "failed", "e2": "passed"})

    assert verdict_of(tools, good_rows(e2="eliminated"))["ok"] is True


def test_a_passing_winner_beats_a_failing_finalist_without_remark(agent_factory, repo, monkeypatch):
    _, tools = _checked(agent_factory, repo, monkeypatch, {"e1": "passed", "e2": "failed"})

    assert verdict_of(tools, good_rows())["ok"] is True


def test_the_parser_and_the_native_schema_take_a_null_winner_and_nothing_else_that_is_not_text():
    arguments = {"rows": NO_WINNER_ROWS, "winner": None, "reason": "Nothing works."}
    validate_action_args("verdict", arguments)
    with pytest.raises(CommandValidationError):
        validate_action_args("verdict", {**arguments, "winner": 3})
    schema = tool_definition("verdict")["function"]["parameters"]
    assert "null" in schema["properties"]["winner"]["type"]


def test_a_null_winner_survives_the_tool_call_where_every_other_null_means_absent():
    arguments = {"rows": NO_WINNER_ROWS, "winner": None, "reason": "Nothing works."}

    assert build("verdict", arguments).action.args == arguments
    # Leaving the key out is still an error: the reviewer has to say "none" in so many words.
    with pytest.raises(CommandValidationError, match="Missing required fields: winner"):
        build("verdict", {"rows": NO_WINNER_ROWS, "reason": "Nothing works."})


def test_the_prompt_and_the_task_tell_the_reviewer_that_no_winner_is_a_normal_outcome():
    from kernel.bestof.run import judge_goal

    prompt = _load_role_prompt("judge")
    goal = judge_goal(RUN_TASK, [("e1", "senior-dev"), ("e2", "minimalist")], "en")

    assert "do not invent a winner" in prompt and "winner is null" in prompt
    assert "a normal outcome" in prompt and "a normal outcome" in goal and "winner is null" in goal
    assert "fails them too" in goal and "fails them too" in prompt
