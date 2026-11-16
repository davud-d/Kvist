# -*- coding: utf-8 -*-
"""``/best-of``: the command, a whole run with a scripted model, the person's decision, and runs left waiting.

No network: one ``ReviewClient`` answers the executors (told apart by the style in their system prompt) and the
reviewer (its role prompt), from fixed scripts, and the main interface is a ``Watching`` that records every event in
order and answers the decision dialog from a list.
"""
from __future__ import annotations

import copy
import json
import re
import subprocess
import threading
import time
from types import SimpleNamespace

import pytest

from kernel.bestof import run as bestof_run
from kernel.bestof import worktrees
from kernel.bestof.run import commit_message, parse_request, request_refusal, verdict_table
from kernel.bestof.turns import run_turn, turn_refusal
from kernel.tests.conftest import NullUI

RUN_TASK = "Make the greeting friendlier."
JUDGE_MARK = "the reviewer of a best-of run"


@pytest.fixture(autouse=True)
def _text_protocol(monkeypatch):
    # The native-tools probe would spend a scripted reply on the tool-calling check.
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


@pytest.fixture
def repo(tmp_path, tmp_path_factory, monkeypatch):
    """A git repository at tmp_path with one commit; git sees neither the user's config nor their hooks."""
    config = tmp_path_factory.mktemp("gitconfig") / "empty"
    config.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(config))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    for who in ("AUTHOR", "COMMITTER"):
        monkeypatch.setenv(f"GIT_{who}_NAME", "Test")
        monkeypatch.setenv(f"GIT_{who}_EMAIL", "test@example.invalid")
    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")
    for command in (["init", "-q", "-b", "main"], ["add", "-A"], ["commit", "-q", "-m", "first"]):
        subprocess.run(["git", *command], cwd=tmp_path, check=True, capture_output=True)
    return tmp_path


def git(repo, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout


def call(name, arguments) -> str:
    return f'<tool_call>{json.dumps({"name": name, "arguments": arguments}, ensure_ascii=False)}</tool_call>'


def write(path: str, content: str) -> str:
    return call("write_file", {"path": path, "content": content})


def verdict_call(winner="e1", rows=None, reason="e1 is the smaller change and its tests pass.") -> str:
    rows = rows or [{"executor_id": "e1", "outcome": "winner" if winner == "e1" else "finalist", "note": "smallest diff"},
                    {"executor_id": "e2", "outcome": "winner" if winner == "e2" else "finalist", "note": "also fine"}]
    return call("verdict", {"rows": rows, "winner": winner, "reason": reason})


WAIT = call("executor", {"op": "wait", "timeout": 60})

# What each executor does by default: change a.txt and report.
E1_WORK = [write("a.txt", "hello there\n"), "Changed the greeting in a.txt."]
E2_WORK = [write("a.txt", "hi!\n"), "Shortened the greeting in a.txt."]
STYLES = {"e1": "senior-dev", "e2": "minimalist", "e3": "paranoid"}


def who_asks(kwargs) -> str:
    for message in kwargs["messages"]:
        if message["role"] == "system":
            if JUDGE_MARK in message["content"]:
                return "judge"
            found = re.search(r"WORKING STYLE: ([a-z-]+)", message["content"])
            return found.group(1) if found else "?"
    return "?"


class ReviewClient:
    """One model client for the executors (by style) and the reviewer (``judge``), from fixed scripts.

    ``holds[who] = (n, event)`` makes that one's requests number ``n`` and later wait for the event, so a test can
    keep an executor busy while the reviewer acts.
    """

    def __init__(self, scripts=None, holds=None):
        defaults = {"senior-dev": list(E1_WORK), "minimalist": list(E2_WORK), "judge": [WAIT, verdict_call()]}
        defaults.update(scripts or {})
        self.scripts = {who: list(replies) for who, replies in defaults.items()}
        self.holds = holds or {}
        self.requests: list[tuple[str, list[dict]]] = []
        self._count: dict[str, int] = {}
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        who = who_asks(kwargs)
        with self._lock:
            self.requests.append((who, copy.deepcopy(kwargs["messages"])))
            number = self._count.get(who, 0)
            self._count[who] = number + 1
        hold = self.holds.get(who)
        if hold is not None and number >= hold[0]:
            hold[1].wait(10)
        with self._lock:
            replies = self.scripts.get(who)
            text = replies.pop(0) if replies else ("No verdict." if who == "judge" else "Nothing more to report.")
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None)

    def asked(self, who: str) -> list[list[dict]]:
        with self._lock:
            return [messages for asking, messages in self.requests if asking == who]


class Watching(NullUI):
    """The main interface: every event of the run in order, and the person's answers to the decision dialog."""

    def __init__(self, answers=()):
        self.events: list[tuple[str, dict]] = []
        self.answers = list(answers)
        self.before_answer = None
        self.finals: list[tuple[str, str]] = []
        self.routes: list[str] = []
        self._lock = threading.Lock()

    def _note(self, name, payload):
        with self._lock:
            self.events.append((name, payload))

    def print_bestof_started(self, payload):
        self._note("started", payload)

    def print_bestof_status(self, payload):
        self._note("status", payload)

    def print_executor_activity(self, payload):
        self._note("activity", payload)

    def print_bestof_eliminated(self, payload):
        self._note("eliminated", payload)

    def print_bestof_verdict(self, payload):
        self._note("verdict", payload)

    def print_bestof_diff(self, payload):
        self._note("diff", payload)

    def print_bestof_finished(self, payload):
        self._note("finished", payload)

    def ask_decision(self, title, options, *, body="", purpose="", run_id=""):
        self._note("decision", {"title": title, "options": options, "body": body, "purpose": purpose,
                                "run_id": run_id})
        if self.before_answer is not None:
            self.before_answer()
        return self.answers.pop(0) if self.answers else None

    def print_final(self, message, user_messages=None, outcome="done"):
        self.finals.append((message, outcome))

    def print_route(self, role, source):
        self.routes.append(role)

    def kinds(self, skip=("status", "activity")) -> list[str]:
        return [name for name, _payload in self.events if name not in skip]

    def of(self, kind: str) -> list[dict]:
        return [payload for name, payload in self.events if name == kind]


def make_main(agent_factory, client, answers=(), language="en"):
    main = agent_factory(max_steps=12, stream=False)
    main.client = client
    watching = Watching(answers)
    object.__setattr__(main._ui, "_ui", watching)
    main._state.response_language = language
    return main, watching


def best_of(main, text: str):
    return run_turn(main, "best-of", text)


def commits(repo) -> list[str]:
    return git(repo, "log", "--format=%s").splitlines()


def worktree_list(repo) -> list[str]:
    return [line for line in git(repo, "worktree", "list", "--porcelain").splitlines() if line.startswith("worktree ")]


def wait_until(predicate, seconds: float = 10.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.02)


def record_pools(monkeypatch) -> list:
    """The executor pools the turn builds, in order, so a test can see them while the turn runs."""
    pools: list = []
    original = bestof_run.ExecutorPool.__init__

    def init(self, *args, **kwargs):
        original(self, *args, **kwargs)
        pools.append(self)

    monkeypatch.setattr(bestof_run.ExecutorPool, "__init__", init)
    return pools


def open_when_stopped(gate: threading.Event, pool, *executor_ids: str) -> None:
    """Open ``gate`` once the pool has told these executors to stop.

    A request held at the gate then ends on the stop and not on its answer, whatever the speed of the machine.
    """
    def work():
        # The pool builds its executors in ``start``, which a test that holds the gate may reach after this runs.
        wait_until(lambda: {executor.executor_id for executor in pool.executors() if executor.forced}
                   >= set(executor_ids))
        gate.set()

    threading.Thread(target=work, daemon=True).start()


# --- the command and its arguments ---------------------------------------------------


@pytest.mark.parametrize(("text", "expected"), [
    ("fix the bug", ("run", 4, "fix the bug")),
    ("2 fix the bug", ("run", 2, "fix the bug")),
    ("6   fix\nthe bug", ("run", 6, "fix\nthe bug")),
    ("Сделай приветствие дружелюбнее", ("run", 4, "Сделай приветствие дружелюбнее")),
    ("status of the build is wrong", ("run", 4, "status of the build is wrong")),
    ("diff e1 e2 please", ("run", 4, "diff e1 e2 please")),
    ("3 status", ("run", 3, "status")),
])
def test_a_request_is_an_optional_executor_count_then_the_task(text, expected):
    request = parse_request(text)

    assert (request.kind, request.count, request.task) == expected


@pytest.mark.parametrize(("text", "expected"), [
    ("status", ("status", "")),
    ("STATUS", ("status", "")),
    ("discard", ("discard", "")),
    ("diff", ("diff", "")),
    ("diff E2", ("diff", "e2")),
    ("accept", ("accept", "")),
    ("accept e1", ("accept", "e1")),
])
def test_the_management_words_alone_manage_the_run_left_waiting(text, expected):
    request = parse_request(text)

    assert (request.kind, request.executor_id) == expected


@pytest.mark.parametrize("text", ["", "   ", "3", "5   ", "7 do it", "1 do it", "0 do it", "99"])
def test_no_task_or_a_count_out_of_range_is_refused_in_both_languages(text):
    refusal = request_refusal(text)

    assert refusal is not None and " / " in refusal
    english, russian = refusal.split(" / ")
    assert english.isascii() and not russian.isascii()
    assert ("/best-of" in english) or ("executors" in english)


def test_a_valid_request_has_no_refusal_and_the_council_keeps_its_own_usage_text():
    assert request_refusal("2 do it") is None and request_refusal("status") is None
    assert turn_refusal("best-of", "7 x") == request_refusal("7 x")
    assert turn_refusal("council", "  ") == "Usage: /council <question> / Использование: /council <вопрос>"
    assert turn_refusal("council", "why?") is None


def test_the_range_refusal_names_the_limits_and_the_number_asked_for():
    english, russian = request_refusal("7 do it").split(" / ")

    assert english == "Best-of runs 2 to 6 executors, not 7"
    assert russian == "Best-of запускает от 2 до 6 исполнителей, а не 7"


def test_the_commit_message_is_the_first_line_of_the_task_then_the_winner_and_the_reason():
    message = commit_message("  Make the greeting friendlier.\n\nKeep it short.", "e2", "minimalist", "It is smaller.\n")

    assert message == ("Make the greeting friendlier.\n\nBest-of: e2 (minimalist) was chosen.\n\nIt is smaller.\n")
    long = commit_message("x" * 200, "e1", "senior-dev", "")
    assert long.splitlines()[0] == "x" * 69 + "..." and long.endswith("was chosen.\n")


# --- a whole run -------------------------------------------------------------------


def test_two_executors_a_verdict_the_persons_accept_and_one_commit_with_the_winners_change(agent_factory, repo):
    client = ReviewClient()
    main, watching = make_main(agent_factory, client, answers=["accept"])
    head = git(repo, "rev-parse", "HEAD").strip()

    result = best_of(main, f"2 {RUN_TASK}")

    assert watching.kinds() == ["started", "verdict", "decision", "finished"]
    started, verdict, decision, finished = (watching.of(kind)[0] for kind in ("started", "verdict", "decision", "finished"))
    assert started["task"] == RUN_TASK and started["count"] == 2
    assert [item["executor_id"] for item in started["executors"]] == ["e1", "e2"]
    assert [item["style"] for item in started["executors"]] == ["senior-dev", "minimalist"]
    assert started["executors"][0]["branch"] == f"kvist/bestof/{started['run_id']}/e1"
    # ``base`` is the short hash of the snapshot commit the executors started from.
    assert re.fullmatch(r"[0-9a-f]{10}", started["base"])
    assert git(repo, "cat-file", "-t", started["base"]).strip() == "commit"
    assert watching.of("status")[0]["state"] == "waiting"
    assert {payload["state"] for payload in watching.of("status")} >= {"waiting", "running", "finished"}
    assert decision["purpose"] == "bestof_decision" and decision["run_id"] == started["run_id"]
    assert [value for value, _label in decision["options"]] == ["accept", "diff", "other", "discard"]
    assert "| e1 | senior-dev | winner |" in decision["body"] and "**Winner:** e1 (senior-dev)" in decision["body"]
    assert verdict["winner"] == "e1" and finished["outcome"] == "accepted" and finished["executor_id"] == "e1"
    # One commit on top of the user's branch, with the winner's change and nothing else.
    assert commits(repo) == [RUN_TASK, "first"]
    assert finished["commit"] == git(repo, "rev-parse", "HEAD").strip() != head
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello there\n"
    # Nothing is left staged or modified (``.kvist`` is Kvist's own folder, which a project ignores).
    assert git(repo, "status", "--porcelain", "--", ".", ":(exclude).kvist").strip() == ""
    body = git(repo, "log", "-1", "--format=%B")
    assert "Best-of: e1 (senior-dev) was chosen." in body and "smaller change" in body
    # The executors' folders and branches are gone.
    assert worktree_list(repo) == [f"worktree {repo.resolve()}"]
    assert git(repo, "branch", "--list", "kvist/bestof/*").strip() == ""
    assert result.ok and result.effects["outcome"] == "accepted" and result.effects["commit"] == finished["commit"]
    assert " / " in result.message and finished["commit"][:10] in result.message


def test_the_final_answer_carries_the_table_and_the_outcome_for_frontends_without_a_screen(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["accept"])

    result = best_of(main, f"2 {RUN_TASK}")

    assert watching.finals == [(result.final, "done")]
    assert result.final.startswith("## best-of")
    assert "Taken: the result of e1 (senior-dev) is committed as" in result.final
    assert "| Executor | Style | Outcome | Tests | Diff | Note |" in result.final
    assert "| e2 | minimalist | finalist | not run | 1 files, +1 -1 | also fine |" in result.final
    assert "**Reason:** e1 is the smaller change" in result.final


def test_in_russian_the_person_reads_russian_and_the_command_result_carries_both(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["accept"], language="ru")

    result = best_of(main, "2 Сделай приветствие дружелюбнее")

    english, russian = result.message.split(" / ")
    assert english.startswith("best-of: accepted e1") and russian.startswith("best-of: принят e1")
    assert "Принято: результат e1 (senior-dev)" in result.final and "Победитель" in result.final
    assert "| Исполнитель | Стиль | Итог |" in result.final
    assert re.search(r"[А-Яа-я]", watching.of("finished")[0]["message"]) and "accepted" not in watching.of("finished")[0]["message"]
    labels = [label for _value, label in watching.of("decision")[0]["options"]]
    assert labels[0] == "Принять победителя (e1)" and "Discard" not in " ".join(labels)


def test_the_reviewer_is_never_named_to_the_person_and_answers_nothing_of_its_own(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["accept"])

    result = best_of(main, f"2 {RUN_TASK}")

    # One final answer for one command: the reviewer's own closing words are not shown, and neither is its role.
    assert len(watching.finals) == 1
    assert "judge" not in watching.routes
    assert "judge" not in result.final.lower() and "judge" not in result.message.lower()
    texts = json.dumps(watching.events, ensure_ascii=False).lower()
    assert "judge" not in texts


def test_the_turn_leaves_no_trace_of_the_review_in_the_session_or_a_checkpoint(agent_factory, repo):
    main, _ = make_main(agent_factory, ReviewClient(), answers=["accept"])

    best_of(main, f"2 {RUN_TASK}")

    assert [entry["goal"] for entry in main._session_history] == [f"/best-of 2 {RUN_TASK}"]
    assert main._session_history[0]["role"] == "reader" and "e1" in main._session_history[0]["result"]
    # The entry carries the number of the person's message, not the one the review took for itself.
    assert main._session_history[0]["msg_id"] == 1
    assert main._peek_checkpoint() is None
    assert main.judge_tools is None


def test_the_reviewer_is_told_the_task_the_executors_their_order_of_work_and_the_language(agent_factory, repo):
    client = ReviewClient()
    main, _ = make_main(agent_factory, client, answers=["accept"], language="ru")

    best_of(main, "2 Сделай приветствие дружелюбнее")

    first = client.asked("judge")[0]
    system = next(message["content"] for message in first if message["role"] == "system")
    goal = "\n".join(message["content"] for message in first if message["role"] == "user")
    assert JUDGE_MARK in system and "ALLOWED ACTIONS" in system
    assert "Сделай приветствие дружелюбнее" in goal
    assert "- e1: style senior-dev" in goal and "- e2: style minimalist" in goal
    assert "in Russian" in goal
    assert goal.index("1. Cheating") < goal.index("2. Tests") < goal.index("3. Quality")
    assert "set_check" in goal and "only of the executors still in the comparison" in goal


# --- the decision ------------------------------------------------------------------------


def test_diff_shows_the_patch_as_an_event_and_asks_the_same_question_over_it(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["diff:e2", "accept"])

    best_of(main, f"2 {RUN_TASK}")

    assert watching.kinds() == ["started", "verdict", "decision", "diff", "decision", "finished"]
    first, second = watching.of("decision")
    patch = watching.of("diff")[0]
    assert patch["executor_id"] == "e2" and patch["files"] == ["a.txt"] and "+hi!" in patch["patch"]
    assert second["body"] == patch["patch"] and second["options"] == first["options"]
    assert second["title"] == first["title"] and second["purpose"] == "bestof_decision"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello there\n"


def test_a_plain_diff_means_the_winners_and_the_question_returns_to_the_table(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["diff", "other", "back", "discard"])

    best_of(main, f"2 {RUN_TASK}")

    assert watching.of("diff")[0]["executor_id"] == "e1"
    decisions = watching.of("decision")
    assert decisions[1]["body"] == watching.of("diff")[0]["patch"]
    # Asking for another result after a diff shows the table again, not the patch.
    assert decisions[2]["body"] == decisions[0]["body"]


def test_other_offers_the_remaining_finalists_and_taking_one_commits_that_one(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["other", "pick:e2"])

    result = best_of(main, f"2 {RUN_TASK}")

    first, second, *rest = watching.of("decision")
    assert rest == [] and second["title"] != first["title"]
    assert second["options"] == [("pick:e2", "e2 (minimalist)"), ("back", "Back")]
    assert second["purpose"] == "bestof_decision" and "| e1 |" in second["body"]
    assert watching.of("finished")[0]["executor_id"] == "e2" and result.effects["executor_id"] == "e2"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hi!\n"
    assert commits(repo) == [RUN_TASK, "first"]
    assert "Best-of: e2 (minimalist) was chosen." in git(repo, "log", "-1", "--format=%B")


def test_pick_in_the_first_dialog_takes_that_finalist_and_a_result_that_is_not_one_is_refused(agent_factory, repo):
    scripts = {"judge": [WAIT, verdict_call(rows=[
        {"executor_id": "e1", "outcome": "winner", "note": "best"},
        {"executor_id": "e2", "outcome": "eliminated", "note": "out"}])]}
    main, watching = make_main(agent_factory, ReviewClient(scripts), answers=["pick:e2", "pick:e9", "other", "pick:e1"])

    best_of(main, f"2 {RUN_TASK}")

    decisions = watching.of("decision")
    # e2 was eliminated by the reviewer, e9 does not exist: both come back to the question with a reason.
    assert decisions[1]["body"].startswith("e2 cannot be taken") and decisions[2]["body"].startswith("e9 cannot be taken")
    # Nobody else is left to choose from, and the winner itself may be picked.
    assert decisions[3]["body"].startswith("There is no other finished result")
    assert watching.of("finished")[0]["executor_id"] == "e1"


def test_discard_removes_everything_and_leaves_the_users_branch_and_files_alone(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["discard"])
    head = git(repo, "rev-parse", "HEAD").strip()

    result = best_of(main, f"2 {RUN_TASK}")

    assert watching.kinds()[-1] == "finished" and watching.of("finished")[0]["outcome"] == "discarded"
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello\n"
    assert worktree_list(repo) == [f"worktree {repo.resolve()}"]
    assert not (repo / ".kvist" / "bestof" / watching.of("started")[0]["run_id"]).exists()
    assert result.ok and result.effects["outcome"] == "discarded"
    assert "Discarded" in result.final and "nothing" not in result.final.lower().split("discarded")[0]


def test_a_closed_dialog_leaves_the_run_pending_and_the_next_turn_accepts_it(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=[])
    head = git(repo, "rev-parse", "HEAD").strip()

    first = best_of(main, f"2 {RUN_TASK}")

    run_id = watching.of("started")[0]["run_id"]
    finished = watching.of("finished")[0]
    assert finished["outcome"] == "pending" and "commit" not in finished
    assert first.ok and first.effects["outcome"] == "pending"
    assert "/best-of accept" in first.final and "/best-of diff" in first.final and "/best-of discard" in first.final
    assert "| e1 | senior-dev | winner |" in first.final
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert worktrees.load_run(repo, run_id).state == "pending"

    later = best_of(main, "accept")

    assert later.ok and commits(repo) == [RUN_TASK, "first"]
    assert watching.of("finished")[1]["outcome"] == "accepted" and watching.of("finished")[1]["executor_id"] == "e1"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello there\n"
    assert worktree_list(repo) == [f"worktree {repo.resolve()}"]
    # The second turn's events are the turn's own: the outcome, not a new run.
    assert watching.kinds()[-1] == "finished" and watching.kinds().count("started") == 1


def test_a_person_without_a_dialog_gets_the_same_question_through_ask_choice(agent_factory, repo):
    class Plain(Watching):
        ask_decision = None

        def ask_choice(self, title, options):
            self._note("choice", {"title": title, "options": options})
            return "discard"

    main, _ = make_main(agent_factory, ReviewClient())
    plain = Plain()
    object.__setattr__(main._ui, "_ui", plain)

    best_of(main, f"2 {RUN_TASK}")

    asked = plain.of("choice")[0]
    assert "| e1 | senior-dev | winner |" in asked["title"] and asked["options"][0][0] == "accept"
    assert plain.of("finished")[0]["outcome"] == "discarded"


def test_a_ui_that_can_ask_nothing_leaves_the_run_pending(agent_factory, repo):
    class Silent(NullUI):
        def __init__(self):
            self.finished = []

        def print_bestof_finished(self, payload):
            self.finished.append(payload)

    main, _ = make_main(agent_factory, ReviewClient())
    silent = Silent()
    object.__setattr__(main._ui, "_ui", silent)

    result = best_of(main, f"2 {RUN_TASK}")

    assert silent.finished[0]["outcome"] == "pending" and result.effects["outcome"] == "pending"


# --- a stop while the person is deciding --------------------------------------------------------


def test_a_stop_while_the_dialog_is_open_closes_the_question_and_leaves_the_run_waiting(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["accept"])
    unanswered = threading.Event()

    def stop_and_never_answer():
        # The person presses Stop in the real way; the screen never answers the open question.
        main.request_cancel()
        unanswered.wait(30)

    watching.before_answer = stop_and_never_answer
    head = git(repo, "rev-parse", "HEAD").strip()
    try:
        result = best_of(main, f"2 {RUN_TASK}")
    finally:
        unanswered.set()

    run_id = watching.of("started")[0]["run_id"]
    assert result.stopped and not result.ok and result.effects["outcome"] == "pending"
    assert result.effects["stopped"] is True
    assert watching.of("finished")[0]["outcome"] == "pending" and len(watching.of("finished")) == 1
    assert watching.finals == [(result.final, "incomplete")]
    assert "Stopped before the decision" in result.final and "/best-of accept" in result.final
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert worktrees.load_run(repo, run_id).state == "pending"
    # What the executors made is still there to be taken.
    later = best_of(main, "accept")
    assert later.ok and commits(repo) == [RUN_TASK, "first"]


def test_an_accept_answered_after_a_stop_takes_nothing(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["accept"])
    # The stop and the answer arrive together; the stop wins, because nothing was committed yet.
    watching.before_answer = main.request_cancel
    head = git(repo, "rev-parse", "HEAD").strip()

    result = best_of(main, f"2 {RUN_TASK}")

    assert result.stopped and result.effects["outcome"] == "pending"
    assert git(repo, "rev-parse", "HEAD").strip() == head and commits(repo) == ["first"]
    assert watching.of("finished")[0]["outcome"] == "pending"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_a_stop_during_the_second_dialog_also_leaves_the_run_waiting(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["other", "pick:e2"])
    unanswered = threading.Event()
    asked = []

    def stop_at_the_second():
        asked.append(1)
        if len(asked) == 2:
            main.request_cancel()
            unanswered.wait(30)

    watching.before_answer = stop_at_the_second
    try:
        result = best_of(main, f"2 {RUN_TASK}")
    finally:
        unanswered.set()

    assert len(watching.of("decision")) == 2
    assert result.stopped and result.effects["outcome"] == "pending" and commits(repo) == ["first"]


def test_a_dialog_that_fails_is_an_error_of_the_turn_not_a_silent_postponement(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient())

    def broken(*args, **kwargs):
        raise RuntimeError("the screen is gone")

    watching.ask_decision = broken

    with pytest.raises(RuntimeError, match="the screen is gone"):
        best_of(main, f"2 {RUN_TASK}")

    # The executors' work is kept for a look, and the run says it failed.
    assert watching.of("finished")[0]["outcome"] == "failed"


# --- a conflict with the person's own work ----------------------------------------------------


def test_a_file_the_person_changed_after_the_start_stops_the_accept_and_changes_nothing(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["accept"])
    watching.before_answer = lambda: (repo / "a.txt").write_text("my own edit\n", encoding="utf-8")
    head = git(repo, "rev-parse", "HEAD").strip()

    result = best_of(main, f"2 {RUN_TASK}")

    run_id = watching.of("started")[0]["run_id"]
    assert watching.of("finished")[0]["outcome"] == "pending"
    assert "conflicts with your changes" in watching.of("finished")[0]["message"]
    assert not result.ok and result.effects["outcome"] == "pending"
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert (repo / "a.txt").read_text(encoding="utf-8") == "my own edit\n"
    assert worktrees.load_run(repo, run_id).state == "pending"
    assert "The result of e1 was not taken: it conflicts with your changes." in result.final
    assert "You changed these files after the run started" in result.final and "`a.txt`" in result.final
    assert "/best-of accept e1" in result.final
    # Putting the file back is what the final says to do; then the same command takes the result.
    git(repo, "checkout", "--", "a.txt")

    again = best_of(main, "accept")

    assert again.ok and commits(repo) == [RUN_TASK, "first"]
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hello there\n"


def test_the_conflict_text_of_git_reaches_the_final_when_the_patch_does_not_apply(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=["accept"])

    def commit_over_it():
        (repo / "a.txt").write_text("hello\nsecond line\n", encoding="utf-8")
        git(repo, "commit", "-aq", "-m", "meanwhile")

    watching.before_answer = commit_over_it

    result = best_of(main, f"2 {RUN_TASK}")

    assert not result.ok and result.effects["outcome"] == "pending"
    assert "conflicts with your changes" in result.final
    assert commits(repo) == ["meanwhile", "first"]


# --- stopping ----------------------------------------------------------------------------------------


def test_a_stop_during_the_run_stops_every_executor_marks_the_run_cancelled_and_keeps_the_work(
        agent_factory, repo, monkeypatch):
    gate = threading.Event()
    # Both executors answer once (their change is written), then wait for the model while the reviewer waits for them.
    client = ReviewClient(holds={"senior-dev": (1, gate), "minimalist": (1, gate)})
    main, watching = make_main(agent_factory, client)
    head = git(repo, "rev-parse", "HEAD").strip()
    pools = record_pools(monkeypatch)
    reviewer_waits = threading.Event()
    wait = bestof_run.ExecutorPool.wait

    def noted_wait(pool, *args, **kwargs):
        reviewer_waits.set()
        return wait(pool, *args, **kwargs)

    monkeypatch.setattr(bestof_run.ExecutorPool, "wait", noted_wait)

    def stop_when_the_reviewer_waits():
        assert reviewer_waits.wait(10)
        wait_until(lambda: len(client.asked("senior-dev")) >= 2 and len(client.asked("minimalist")) >= 2)
        main.request_cancel()
        open_when_stopped(gate, pools[0], "e1", "e2")

    stopper = threading.Thread(target=stop_when_the_reviewer_waits, daemon=True)
    stopper.start()
    result = best_of(main, f"2 {RUN_TASK}")
    stopper.join(10)

    run_id = watching.of("started")[0]["run_id"]
    assert watching.kinds() == ["started", "finished"]
    assert watching.of("finished")[0]["outcome"] == "cancelled"
    assert result.stopped and not result.ok and result.effects["stopped"] is True and result.effects["outcome"] == "cancelled"
    states = {}
    for payload in watching.of("status"):
        states[payload["executor_id"]] = payload["state"]
    assert states == {"e1": "stopped", "e2": "stopped"}
    assert worktrees.load_run(repo, run_id).state == "cancelled"
    assert git(repo, "rev-parse", "HEAD").strip() == head
    assert "stopped" in result.final and "/best-of diff" in result.final
    assert watching.finals == [(result.final, "incomplete")]

    # What was done stays to be looked at.
    shown = best_of(main, "diff e1")

    assert shown.ok and "hello there" in shown.final and "```diff" in shown.final
    assert watching.of("diff")[-1]["executor_id"] == "e1"
    assert watching.of("diff")[-1]["files"] == ["a.txt"]
    assert main._peek_checkpoint() is None


def test_a_stop_before_the_review_begins_still_ends_cancelled(agent_factory, repo, monkeypatch):
    gate = threading.Event()
    client = ReviewClient(holds={"senior-dev": (0, gate)})
    main, watching = make_main(agent_factory, client)
    start = bestof_run.ExecutorPool.start

    def started_then_stopped(pool):
        start(pool)
        main.request_cancel()
        open_when_stopped(gate, pool, "e1")

    monkeypatch.setattr(bestof_run.ExecutorPool, "start", started_then_stopped)

    result = best_of(main, f"2 {RUN_TASK}")

    assert result.stopped and watching.of("finished")[0]["outcome"] == "cancelled"
    assert client.asked("judge") == []


def test_a_review_that_ends_without_a_verdict_keeps_the_results_for_the_person_to_decide(agent_factory, repo):
    scripts = {"judge": [WAIT, "None of them did what the task asked, so I will not pick one."]}
    main, watching = make_main(agent_factory, ReviewClient(scripts))

    result = best_of(main, f"2 {RUN_TASK}")

    run_id = watching.of("started")[0]["run_id"]
    assert watching.kinds() == ["started", "finished"] and watching.of("finished")[0]["outcome"] == "pending"
    assert not result.ok and result.effects["outcome"] == "pending"
    assert worktrees.load_run(repo, run_id).state == "pending"
    assert "ended without a verdict" in result.final
    assert "None of them did what the task asked" in result.final
    assert "- **e1** (senior-dev): 1 files, +1 -1" in result.final
    assert "/best-of accept [id]" in result.final


# --- a run that cannot start --------------------------------------------------------------------------


def test_outside_a_git_repository_the_turn_ends_with_the_reason_and_nothing_is_made(agent_factory, tmp_path):
    client = ReviewClient()
    main, watching = make_main(agent_factory, client)

    result = best_of(main, f"2 {RUN_TASK}")

    assert not result.ok
    assert "git repository" in result.final and "git-репозитор" in result.message
    assert watching.events == [] and client.requests == []
    assert not (tmp_path / ".kvist" / "bestof").exists()


def test_a_repository_without_a_commit_and_a_detached_head_are_refused_with_their_reason(agent_factory, tmp_path,
                                                                                         monkeypatch, repo):
    git(repo, "checkout", "-q", "--detach")
    main, watching = make_main(agent_factory, ReviewClient())

    result = best_of(main, f"2 {RUN_TASK}")

    assert not result.ok and "HEAD is detached" in result.final
    assert watching.events == []
    assert worktree_list(repo) == [f"worktree {repo.resolve()}"]


def test_a_refused_command_names_both_languages_in_the_command_result_and_one_in_the_text(agent_factory, tmp_path):
    main, _ = make_main(agent_factory, ReviewClient())

    result = best_of(main, "2 Сделай приветствие дружелюбнее")

    assert "only inside a git repository" in result.message and "только в git-репозитории" in result.message
    assert "только в git-репозитории" in result.final and "only inside" not in result.final


# --- runs left waiting ---------------------------------------------------------------------------------------


def test_with_nothing_waiting_the_management_commands_say_so(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient())

    for word in ("status", "diff", "accept", "discard", "diff e1"):
        result = best_of(main, word)

        assert not result.ok and "No best-of run is waiting" in result.final
        assert "Нет прогона best-of" in result.message
    assert watching.events == []


def test_status_shows_the_state_the_task_the_executors_and_the_verdict_of_the_waiting_run(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=[])
    best_of(main, f"2 {RUN_TASK}")
    before = len(watching.events)

    result = best_of(main, "status")

    assert result.ok and len(watching.events) == before
    assert "waiting for your decision" in result.final and RUN_TASK in result.final
    assert "- **e1** (senior-dev): 1 files, +1 -1" in result.final and "- **e2** (minimalist)" in result.final
    assert "| e1 | senior-dev | winner |" in result.final and "/best-of accept [id]" in result.final
    assert result.effects["state"] == "pending"


def test_diff_without_an_id_is_the_winners_and_with_an_id_that_one(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=[])
    best_of(main, f"2 {RUN_TASK}")

    winner = best_of(main, "diff")
    other = best_of(main, "diff e2")

    assert [payload["executor_id"] for payload in watching.of("diff")] == ["e1", "e2"]
    assert "+hello there" in winner.final and "+hi!" in other.final
    assert winner.final.startswith("## best-of: Diff of e1") and other.effects["executor_id"] == "e2"


def test_accept_with_an_id_takes_that_one_and_an_unknown_id_is_an_answer_not_a_change(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=[])
    best_of(main, f"2 {RUN_TASK}")

    unknown = best_of(main, "accept e9")
    assert not unknown.ok and "no executor e9" in unknown.final.lower().replace("there is ", "")
    assert commits(repo) == ["first"]
    taken = best_of(main, "accept e2")

    assert taken.ok and (repo / "a.txt").read_text(encoding="utf-8") == "hi!\n"
    assert watching.of("finished")[-1]["executor_id"] == "e2"


def test_discard_of_a_waiting_run_removes_it_and_a_second_discard_finds_nothing(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=[])
    best_of(main, f"2 {RUN_TASK}")

    done = best_of(main, "discard")
    again = best_of(main, "discard")

    assert done.ok and watching.of("finished")[-1]["outcome"] == "discarded"
    assert not again.ok and worktree_list(repo) == [f"worktree {repo.resolve()}"]
    assert git(repo, "branch", "--list", "kvist/bestof/*").strip() == ""


def test_accept_without_a_chosen_winner_asks_which_one(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient({"judge": [WAIT, "Nobody qualifies."]}))
    best_of(main, f"2 {RUN_TASK}")

    result = best_of(main, "accept")

    assert not result.ok and "no chosen winner" in result.final and "e1, e2" in result.final
    assert commits(repo) == ["first"]


def test_the_latest_waiting_run_is_the_one_the_commands_mean(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=[])
    best_of(main, f"2 {RUN_TASK}")
    main.client = ReviewClient({"senior-dev": [write("b.txt", "second run\n"), "Added b.txt."]})
    best_of(main, "2 Add b.txt")
    runs = [payload["run_id"] for payload in watching.of("started")]

    status = best_of(main, "status")

    assert runs[1] in status.final and runs[0] not in status.final and "Add b.txt" in status.final


def test_a_run_whose_owner_died_is_interrupted_and_can_still_be_looked_at_and_taken(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient(), answers=[])
    best_of(main, f"2 {RUN_TASK}")
    run_id = watching.of("started")[0]["run_id"]
    run = worktrees.load_run(repo, run_id)
    run_file = run.run_dir / "run.json"
    stored = json.loads(run_file.read_text(encoding="utf-8"))
    stored["state"] = "running"
    # A process id above the kernel's own limit: no such process, so the run's owner is dead.
    stored["owner"] = {"pid": 2 ** 22 + 12345, "start": 1}
    run_file.write_text(json.dumps(stored), encoding="utf-8")

    status = best_of(main, "status")
    taken = best_of(main, "accept e1")

    assert status.ok and "interrupted" in status.final
    assert taken.ok and commits(repo) == [RUN_TASK, "first"]


# --- the table ----------------------------------------------------------------------------------------------------


def test_the_table_escapes_what_would_break_a_cell_and_hides_secrets():
    verdict = {"rows": [{"executor_id": "e1", "style": "senior-dev", "outcome": "winner",
                         "tests": {"state": "passed", "summary": ""}, "diff": {"files": 2, "added": 5, "removed": 1},
                         "note": "a | b\nsecond line sk-ant-api03-" + "x" * 40}],
               "winner": "e1", "reason": "Because."}

    table = verdict_table(verdict, "en")

    assert "| e1 | senior-dev | winner | passed | 2 files, +5 -1 | a \\| b second line" in table
    assert "sk-ant-api03-" + "x" * 40 not in table
    assert table.endswith("**Reason:** Because.")


# --- over the stdio transport ------------------------------------------------------------------------------------------


def _served(tmp_path, client):
    from kernel.tests.test_turn_commands import Served

    return Served(tmp_path, client)


def _answer_dialogs(stdio, answers):
    """Answer each decision dialog of the run with the next of ``answers``."""
    asked = []
    for answer in answers:
        event = stdio.out.wait_for(
            lambda item: item["type"] == "input_request" and item.get("purpose") == "bestof_decision"
            and item["request_id"] not in asked, 60)
        asked.append(event["request_id"])
        stdio.reader.push(json.dumps({"request_id": event["request_id"], "value": answer}))
    return asked


def test_the_command_over_stdio_streams_the_run_asks_the_dialog_and_ends_with_turn_end_then_command_result(repo):
    stdio = _served(repo, ReviewClient())

    stdio.command("best-of", "2", *RUN_TASK.split())
    _answer_dialogs(stdio, ["accept"])
    result = stdio.result("best-of")
    stdio.finish()

    types = stdio.types()
    # The reviewer's own steps stream between the start and the verdict; what the person follows is this skeleton.
    skeleton = [kind for kind in types if kind in (
        "bestof_started", "bestof_verdict", "input_request", "bestof_finished", "final", "turn_end",
        "command_result")]
    assert skeleton == ["bestof_started", "bestof_verdict", "input_request", "bestof_finished", "final", "turn_end",
                        "command_result"]
    assert types[-3:] == ["final", "turn_end", "command_result"]
    assert "route" not in types and types.count("final") == 1
    assert types.index("bestof_status") < types.index("bestof_verdict")
    request = next(event for event in stdio.out.events() if event["type"] == "input_request")
    assert request["kind"] == "choice" and request["purpose"] == "bestof_decision" and request["run_id"]
    assert "| e1 | senior-dev | winner |" in request["body"]
    assert [option["value"] for option in request["options"]] == ["accept", "diff", "other", "discard"]
    assert result["ok"] is True and result["effects"]["outcome"] == "accepted"
    assert commits(repo) == [RUN_TASK, "first"]


def _decision_threads() -> list:
    return [thread for thread in threading.enumerate() if thread.name == "kvist-bestof-decision" and thread.is_alive()]


def _open_dialog(stdio, answers_before=()):
    """Answer the first dialogs with ``answers_before``, then return the next request, left open."""
    asked = _answer_dialogs(stdio, answers_before)
    return stdio.out.wait_for(
        lambda item: item["type"] == "input_request" and item.get("purpose") == "bestof_decision"
        and item["request_id"] not in asked, 60)


def test_a_stop_over_stdio_closes_the_open_decision_dialog_and_frees_its_thread(repo):
    stdio = _served(repo, ReviewClient())
    stdio.command("best-of", "2", *RUN_TASK.split())
    request = _open_dialog(stdio)

    stdio.reader.push(json.dumps({"type": "cancel"}))
    result = stdio.result("best-of")
    # A late answer to the closed question changes nothing and breaks nothing.
    stdio.reader.push(json.dumps({"request_id": request["request_id"], "value": "accept"}))
    stdio.command("best-of", "status")
    status = stdio.out.wait_for(lambda item: item["type"] == "command_result" and item["effects"].get("state"), 20)
    stdio.finish()

    events = stdio.out.events()
    closed = [event for event in events if event["type"] == "input_closed"]
    assert closed == [{"type": "input_closed", "request_id": request["request_id"], "reason": "cancelled"}]
    types = [event["type"] for event in events]
    assert types.index("input_request") < types.index("input_closed") < types.index("final") \
        < types.index("turn_end") < types.index("command_result")
    assert result["ok"] is False and result["effects"]["stopped"] is True and result["effects"]["outcome"] == "pending"
    assert _decision_threads() == []
    assert "error" not in types
    assert status["ok"] is True and status["effects"]["state"] == "pending"
    assert commits(repo) == ["first"] and (repo / "a.txt").read_text(encoding="utf-8") == "hello\n"


def test_a_stop_during_the_second_dialog_closes_that_one_and_not_the_first(repo):
    stdio = _served(repo, ReviewClient())
    stdio.command("best-of", "2", *RUN_TASK.split())
    first = stdio.out.wait_for(lambda item: item["type"] == "input_request" and item.get("purpose"), 60)
    stdio.reader.push(json.dumps({"request_id": first["request_id"], "value": "other"}))
    second = stdio.out.wait_for(
        lambda item: item["type"] == "input_request" and item["request_id"] != first["request_id"], 60)

    stdio.reader.push(json.dumps({"type": "cancel"}))
    result = stdio.result("best-of")
    stdio.finish()

    closed = [event for event in stdio.out.events() if event["type"] == "input_closed"]
    assert [(event["request_id"], event["reason"]) for event in closed] == [(second["request_id"], "cancelled")]
    assert result["ok"] is False and result["effects"]["outcome"] == "pending"
    assert _decision_threads() == [] and commits(repo) == ["first"]


def test_a_stop_while_the_executors_work_closes_no_dialog(repo):
    gate = threading.Event()
    client = ReviewClient(holds={"senior-dev": (0, gate)})
    stdio = _served(repo, client)
    try:
        stdio.command("best-of", "2", *RUN_TASK.split())
        wait_until(lambda: client.asked("senior-dev"))
        stdio.reader.push(json.dumps({"type": "cancel"}))
        wait_until(lambda: any(event["type"] == "task_state" and event["state"] == "cancelling"
                               for event in stdio.out.events()))
    finally:
        gate.set()
    result = stdio.result("best-of")
    stdio.finish()

    assert result["effects"]["outcome"] == "cancelled"
    assert not [event for event in stdio.out.events() if event["type"] == "input_closed"]


def test_the_journal_keeps_what_the_person_decided_on_and_not_the_progress_of_the_executors(repo):
    stdio = _served(repo, ReviewClient())

    stdio.command("best-of", "2", *RUN_TASK.split())
    _answer_dialogs(stdio, ["accept"])
    stdio.result("best-of")
    stdio.finish()

    events = stdio.journal()
    kinds = [event["type"] for event in events]
    assert events[0] == {"type": "user", "text": f"/best-of 2 {RUN_TASK}"}
    assert kinds.index("bestof_started") < kinds.index("bestof_verdict") < kinds.index("bestof_finished") \
        < kinds.index("final") < kinds.index("turn_end")
    assert "bestof_status" not in kinds and "executor_activity" not in kinds and "bestof_diff" not in kinds
    assert kinds.count("turn_end") == 1 and kinds.count("final") == 1


@pytest.mark.parametrize("send", [
    lambda stdio: stdio.command("best-of"),
    lambda stdio: stdio.command("best-of", "  "),
    lambda stdio: stdio.command("best-of", "7", "do", "it"),
    lambda stdio: stdio.command("best-of", "3"),
    lambda stdio: stdio.reader.push("/best-of"),
    lambda stdio: stdio.reader.push("/best-of 7 do it"),
    lambda stdio: stdio.reader.push("/best-of 1"),
])
def test_a_request_that_cannot_start_is_a_command_result_with_no_turn(repo, send):
    client = ReviewClient()
    stdio = _served(repo, client)

    send(stdio)
    result = stdio.result("best-of")
    stdio.finish()

    assert result["ok"] is False and " / " in result["message"]
    assert stdio.types() == ["hello", "manifest", "command_result"]
    assert client.requests == [] and stdio.journal() == []


def test_a_goal_line_with_the_command_is_the_same_turn_as_the_command_message(repo):
    stdio = _served(repo, ReviewClient())

    stdio.reader.push(f"/best-of 2 {RUN_TASK}")
    _answer_dialogs(stdio, ["discard"])
    result = stdio.result("best-of")
    stdio.finish()

    assert result["ok"] is True and result["effects"]["outcome"] == "discarded"
    started = next(event for event in stdio.out.events() if event["type"] == "bestof_started")
    assert started["count"] == 2 and started["task"] == RUN_TASK
    assert stdio.types()[-2:] == ["turn_end", "command_result"]


def test_the_core_announces_the_command_in_hello_and_the_menu(repo):
    stdio = _served(repo, ReviewClient())
    stdio.finish()

    hello = stdio.out.events()[0]
    assert "best-of" in hello["commands"]
    manifest = next(event for event in stdio.out.events() if event["type"] == "manifest")
    entry = next(item for item in manifest["slash_commands"] if item["name"] == "best-of")
    assert entry["via"] == "core" and "/best-of [N] <task>" in entry["description"]


def test_a_second_run_while_one_works_is_refused_at_once(repo):
    gate = threading.Event()
    client = ReviewClient(holds={"senior-dev": (0, gate)})
    stdio = _served(repo, client)
    try:
        stdio.command("best-of", "2", *RUN_TASK.split())
        wait_until(lambda: client.asked("senior-dev"))
        stdio.command("best-of", "2", "another")
        refusal = stdio.result("best-of")
        assert refusal["ok"] is False and "running" in refusal["message"]
    finally:
        gate.set()
    _answer_dialogs(stdio, ["discard"])
    stdio.result("best-of")
    stdio.finish()


# --- no winner, and a winner whose tests fail ----------------------------------------------------------

NO_WINNER_ROWS = [{"executor_id": "e1", "outcome": "failed", "note": "does not solve it"},
                  {"executor_id": "e2", "outcome": "eliminated", "note": "does not solve it"}]
NO_WINNER = verdict_call(winner=None, rows=NO_WINNER_ROWS, reason="The task cannot be done.")


def _checked_run(monkeypatch, passed: bool) -> str:
    """Make every executor's check come out as ``passed`` or failed; returns the judge's set_check call."""
    from kernel.sandbox.executor import ExecutionResult
    from kernel.tests.test_bestof_executors import CheckSeam

    seam = CheckSeam(monkeypatch)
    seam.result = ExecutionResult(ok=passed, action_type="run_shell", data={
        "command": [seam.MARKER], "returncode": 0 if passed else 1, "stdout": "2 passed\n" if passed else "2 failed\n",
        "stderr": "", "timed_out": False})
    return call("executor", {"op": "set_check", "command": [seam.MARKER]})


def _check_all() -> list[str]:
    """After the wait: check each executor now, so one that ended before set_check was called is checked too."""
    return [call("executor", {"op": "check", "executor_id": executor_id}) for executor_id in ("e1", "e2")]


def test_with_no_winner_the_question_opens_on_discarding_and_accepting_is_not_offered(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient({"judge": [WAIT, NO_WINNER]}), answers=["discard"])

    result = best_of(main, f"2 {RUN_TASK}")

    verdict, decision = watching.of("verdict")[0], watching.of("decision")[0]
    assert verdict["winner"] == "" and verdict["reason"] == "The task cannot be done."
    assert [row["outcome"] for row in verdict["rows"]] == ["failed", "eliminated"]
    assert [value for value, _label in decision["options"]] == ["discard", "diff", "other"]
    assert "**Winner:** none: no result solves the task" in decision["body"]
    assert "The task cannot be done." in decision["body"]
    assert result.effects["outcome"] == "discarded" and commits(repo) == ["first"]
    assert worktree_list(repo) == [f"worktree {repo.resolve()}"]


def test_an_accept_answered_to_a_run_with_no_winner_takes_nothing_and_asks_again(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient({"judge": [WAIT, NO_WINNER]}),
                               answers=["accept", "discard"])

    best_of(main, f"2 {RUN_TASK}")

    first, second = watching.of("decision")
    assert "no winner to accept" in second["body"] and second["body"].endswith(first["body"])
    assert commits(repo) == ["first"]


def test_with_no_winner_the_person_may_look_at_a_diff_and_choose_a_result_by_hand(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient({"judge": [WAIT, NO_WINNER]}),
                               answers=["diff", "diff:e2", "other", "pick:e2"])

    result = best_of(main, f"2 {RUN_TASK}")

    assert [(item["executor_id"]) for item in watching.of("diff")] == ["e1", "e2"]
    other = watching.of("decision")[3]
    assert [value for value, _label in other["options"]] == ["pick:e1", "pick:e2", "back"]
    assert result.effects["outcome"] == "accepted" and result.effects["executor_id"] == "e2"
    assert (repo / "a.txt").read_text(encoding="utf-8") == "hi!\n"
    # The reviewer's reason says why nothing was good enough, which is not why this one was taken.
    assert "cannot be done" not in git(repo, "log", "-1", "--format=%B")


def test_a_run_with_no_winner_waits_when_the_dialog_is_closed_and_the_person_may_still_pick_by_id(agent_factory, repo):
    main, watching = make_main(agent_factory, ReviewClient({"judge": [WAIT, NO_WINNER]}), answers=[None])

    best_of(main, f"2 {RUN_TASK}")
    plain = best_of(main, "accept")
    by_id = best_of(main, "accept e1")

    assert not plain.ok and "no chosen winner" in plain.final
    assert by_id.ok and by_id.effects["executor_id"] == "e1"


def test_a_winner_whose_tests_fail_is_marked_and_the_question_opens_on_the_diff_not_on_accept(
        agent_factory, repo, monkeypatch):
    check = _checked_run(monkeypatch, passed=False)
    main, watching = make_main(agent_factory, ReviewClient({"judge": [check, WAIT, *_check_all(), verdict_call()]}),
                               answers=["accept"])

    result = best_of(main, f"2 {RUN_TASK}")

    verdict, decision = watching.of("verdict")[0], watching.of("decision")[0]
    assert verdict["winner"] == "e1" and verdict["rows"][0]["tests"]["state"] == "failed"
    assert [value for value, _label in decision["options"]] == ["diff", "accept", "other", "discard"]
    assert dict(decision["options"])["accept"] == "Accept e1 anyway (its tests fail)"
    assert "**Warning:** Its tests fail, and so do those of every other result" in decision["body"]
    # Accepting is still the person's right.
    assert result.effects["outcome"] == "accepted"


def test_a_winner_whose_tests_pass_opens_on_accept_with_no_warning(agent_factory, repo, monkeypatch):
    check = _checked_run(monkeypatch, passed=True)
    main, watching = make_main(agent_factory, ReviewClient({"judge": [check, WAIT, *_check_all(), verdict_call()]}),
                               answers=["discard"])

    best_of(main, f"2 {RUN_TASK}")

    decision = watching.of("decision")[0]
    assert [value for value, _label in decision["options"]] == ["accept", "diff", "other", "discard"]
    assert "Warning" not in decision["body"]


def test_the_table_for_no_winner_and_for_a_failing_winner_is_in_the_persons_language():
    rows = [{"executor_id": "e1", "style": "senior-dev", "outcome": "failed", "tests": {"state": "failed"},
             "diff": {"files": 1, "added": 1, "removed": 0}, "note": ""}]

    none = verdict_table({"rows": rows, "winner": "", "reason": "Невыполнимо."}, "ru")
    failing = verdict_table({"rows": [{**rows[0], "outcome": "winner"}], "winner": "e1", "reason": "Лучший."}, "ru")

    assert "**Победитель:** нет: ни один результат не решает задачу" in none
    assert "**Внимание:** Его тесты падают" in failing
