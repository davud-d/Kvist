# -*- coding: utf-8 -*-
"""``/best-of [N] <task>``: N executors work on one task, a reviewer compares them, the person decides.

One turn of the main agent. The code makes a snapshot and a worktree per executor, starts the executors
(``executors.py``), and lets the main agent itself, in the internal role ``judge``, review what they leave with the
``executor`` and ``verdict`` actions (``judge.py``). Then it asks the person what to do with the verdict:

* ``accept``  - the winner's change becomes one commit in the person's branch;
* ``diff``    - the winner's patch is shown and the same question comes again;
* ``other``   - the person picks another finished result;
* ``discard`` - everything the run made is removed;
* no answer   - the run stays ``pending`` and waits for ``/best-of accept | diff | discard`` in a later turn.

The same command word manages a run left waiting (``status``, ``diff [id]``, ``accept [id]``, ``discard``); those
are turns too, so what they do is journaled. A run's task and verdict live in ``decision.json`` inside its run
folder, because ``run.json`` belongs to ``worktrees.py`` and does not know them.

The words the person reads are in the language of their request; ``command_result.message`` carries both languages
like every command's. The reviewer is called "check" or "best-of" to the person, never "judge".
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kernel.bestof import worktrees
# The session name is what ``bestof_started`` announces before the pool has built its executors.
from kernel.bestof.executors import ExecutorPool, _session_name, executor_ids
from kernel.bestof.judge import JUDGE_ROLE, JudgeTools
from kernel.bestof.styles import DEFAULT_EXECUTORS, MAX_EXECUTORS, MIN_EXECUTORS, STYLE_LADDER, styles_for
from kernel.bestof.turns import TurnResult
from kernel.bestof.worktrees import BestOfRefused, GitError, RunInfo
from kernel.context.language_policy import response_language_name
from kernel.protocol.frontend_protocol import PURPOSE_BESTOF_DECISION
from kernel.sandbox.redaction import redact_text

_logger = logging.getLogger(__name__)

RECORD_FILE = "decision.json"
# The patch shown for a decision is what a frontend can show (UI_API.md section 7); the whole one stays in the branch.
PATCH_CHARS = 20_000
SUBJECT_CHARS = 72
# How often a waiting decision dialog looks whether the person stopped the turn.
_STOP_POLL_SECONDS = 0.05
MANAGE_WORDS = ("status", "diff", "accept", "discard")
_ONE_WORD_COMMANDS = ("status", "discard")

_TEXT: dict[str, tuple[str, str]] = {
    "usage": (
        "Usage: /best-of [{lo}-{hi}] <task>, or /best-of status | diff [id] | accept [id] | discard",
        "Использование: /best-of [{lo}-{hi}] <задача> или /best-of status | diff [id] | accept [id] | discard",
    ),
    "range": ("Best-of runs {lo} to {hi} executors, not {n}",
              "Best-of запускает от {lo} до {hi} исполнителей, а не {n}"),
    "title": ("best-of", "best-of"),
    "nothing_waiting": (
        "No best-of run is waiting. Start one with /best-of <task>.",
        "Нет прогона best-of, который ждёт решения. Запусти новый: /best-of <задача>.",
    ),
    "unknown_executor": ("There is no executor {id} in run {run}; it has: {known}",
                         "В прогоне {run} нет исполнителя {id}; есть: {known}"),
    "no_winner": (
        "This run has no chosen winner. Say which result to take: /best-of accept <id> (executors: {known}).",
        "В этом прогоне не выбран победитель. Скажи, какой результат взять: /best-of accept <id> "
        "(исполнители: {known}).",
    ),
    "no_result": ("{id} has no result to take: it did not get to finish.",
                  "У {id} нет результата, который можно взять: он не успел закончить."),
    # The run
    "run_title": ("Best-of run {run}", "Прогон best-of {run}"),
    "task": ("Task", "Задача"),
    "state": ("State", "Состояние"),
    "state_pending": ("waiting for your decision", "ждёт твоего решения"),
    "state_cancelled": ("stopped", "остановлен"),
    "state_interrupted": ("interrupted (Kvist was closed during the run)", "прерван (Kvist закрыли во время прогона)"),
    "state_running": ("running", "идёт"),
    "executors": ("Executors", "Исполнители"),
    "result_none": ("no result", "результата нет"),
    "result_lost": ("its folder is gone", "папка пропала"),
    "result_files": ("{files} files, +{added} -{removed}", "файлов: {files}, +{added} -{removed}"),
    "verdict": ("Verdict", "Вердикт"),
    "winner": ("Winner", "Победитель"),
    "winner_none": ("none: no result solves the task", "нет: ни один результат не решает задачу"),
    "winner_failing": ("Its tests fail, and so do those of every other result left in the comparison.",
                       "Его тесты падают, и у всех остальных оставшихся результатов тоже."),
    "reason": ("Reason", "Причина"),
    "warning": ("Warning", "Внимание"),
    "col_executor": ("Executor", "Исполнитель"),
    "col_style": ("Style", "Стиль"),
    "col_outcome": ("Outcome", "Итог"),
    "col_tests": ("Tests", "Тесты"),
    "col_diff": ("Diff", "Diff"),
    "col_note": ("Note", "Заметка"),
    "outcome_winner": ("winner", "победитель"),
    "outcome_finalist": ("finalist", "финалист"),
    "outcome_eliminated": ("eliminated", "выбыл"),
    "outcome_failed": ("failed", "не справился"),
    "tests_unknown": ("not run", "не запускались"),
    "tests_running": ("running", "идут"),
    "tests_passed": ("passed", "прошли"),
    "tests_failed": ("failed", "упали"),
    "next_steps": (
        "Look with /best-of status or /best-of diff [id]; take a result with /best-of accept [id]; drop everything "
        "with /best-of discard.",
        "Посмотреть: /best-of status или /best-of diff [id]; взять результат: /best-of accept [id]; отбросить всё: "
        "/best-of discard.",
    ),
    # The decision dialog
    "decision_title": ("Best-of is done: what to do with the result?", "Best-of закончен: что сделать с результатом?"),
    "option_accept": ("Accept the winner ({id})", "Принять победителя ({id})"),
    "option_diff": ("Show the winner's diff", "Показать diff победителя"),
    "option_other": ("Choose another result", "Выбрать другой результат"),
    "option_accept_failing": ("Accept {id} anyway (its tests fail)", "Всё равно принять {id} (тесты падают)"),
    "option_diff_none": ("Look at a result's diff", "Посмотреть diff результата"),
    "option_other_none": ("Choose a result by hand", "Выбрать результат вручную"),
    "no_accept": ("There is no winner to accept. Look at the diffs, choose a result by hand, or discard everything.",
                  "Победителя, которого можно принять, нет. Посмотри diff, выбери результат вручную или отбрось всё."),
    "option_discard": ("Discard everything", "Отбросить всё"),
    "other_title": ("Which result to take?", "Какой результат взять?"),
    "option_back": ("Back", "Назад"),
    "no_other": ("There is no other finished result with changes to choose from.",
                 "Других законченных результатов с изменениями, из которых можно выбрать, нет."),
    "cannot_take": ("{id} cannot be taken: it did not finish, or it changed nothing.",
                    "{id} взять нельзя: он не закончил или ничего не изменил."),
    "diff_title": ("Diff of {id}", "Diff {id}"),
    # How it ends
    "accepted": ("Taken: the result of {id} ({style}) is committed as {commit}.",
                 "Принято: результат {id} ({style}) закоммичен как {commit}."),
    "accepted_unsynced": (
        "The result of {id} ({style}) is committed as {commit}, but your files could not be brought in line with "
        "it. Nothing is lost; do this by hand:",
        "Результат {id} ({style}) закоммичен как {commit}, но твои файлы привести в соответствие не удалось. "
        "Ничего не потеряно; сделай вручную:",
    ),
    "discarded": ("Discarded: the executors' work is removed; your files were not touched.",
                  "Отброшено: работа исполнителей удалена, твои файлы не тронуты."),
    "pending": ("The decision is postponed; the results are kept.", "Решение отложено; результаты сохранены."),
    "pending_stopped": ("Stopped before the decision: the run waits and the results are kept.",
                        "Остановлено до решения: прогон ждёт, результаты сохранены."),
    "conflict": ("The result of {id} was not taken: it conflicts with your changes.",
                 "Результат {id} не принят: он конфликтует с твоими правками."),
    "conflict_files": ("Files", "Файлы"),
    "conflict_fix": (
        "Commit or stash your own changes in these places, then run /best-of accept {id}.",
        "Закоммить или спрячь свои правки в этих местах, затем выполни /best-of accept {id}.",
    ),
    "not_taken": ("The result of {id} was not taken: {why}", "Результат {id} не принят: {why}"),
    "cancelled": (
        "Best-of was stopped. Executors that were working are stopped and their work so far is kept.",
        "Best-of остановлен. Работавшие исполнители остановлены, сделанное ими сохранено.",
    ),
    "no_verdict": (
        "The check ended without a verdict, so no winner was chosen. The executors' results are kept.",
        "Проверка закончилась без вердикта, победитель не выбран. Результаты исполнителей сохранены.",
    ),
    "reviewer_said": ("What the check said", "Что сказала проверка"),
    "failed": ("Best-of stopped on an error. The executors are stopped and their work is kept.",
               "Best-of остановился из-за ошибки. Исполнители остановлены, их работа сохранена."),
    # One-line command_result messages
    "msg_accepted": ("best-of: accepted {executor_id}, commit {commit}",
                     "best-of: принят {executor_id}, коммит {commit}"),
    "msg_discarded": ("best-of: discarded", "best-of: отброшено"),
    "msg_pending": ("best-of: waiting for your decision", "best-of: ждёт твоего решения"),
    "msg_cancelled": ("best-of: stopped", "best-of: остановлено"),
    "msg_no_verdict": ("best-of: no verdict", "best-of: вердикта нет"),
    "msg_shown": ("best-of: done", "best-of: готово"),
}


def _both(key: str, **fields: Any) -> str:
    english, russian = _TEXT[key]
    return f"{english.format(**fields)} / {russian.format(**fields)}"


def _say(language: str, key: str, **fields: Any) -> str:
    english, russian = _TEXT[key]
    return (russian if language == "ru" else english).format(**fields)


def _in_language(text: str, language: str) -> str:
    """One language of an ``English / Русский`` line from ``worktrees``; the lines after the first stay as they are."""
    first, newline, rest = text.partition("\n")
    parts = first.split(" / ")
    if len(parts) < 2:
        return text
    half = len(parts) // 2
    chosen = " / ".join(parts[half:] if language == "ru" else parts[:half]).strip()
    return chosen + newline + rest


# --- the command ---------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Request:
    """What ``/best-of`` was asked to do: start a run (``run``) or one of ``MANAGE_WORDS`` on the waiting one."""

    kind: str
    count: int = DEFAULT_EXECUTORS
    task: str = ""
    executor_id: str = ""


def parse_request(text: str) -> Request | str:
    """The request in ``text``, or the refusal (``English / Русский``) when it is not one."""
    words = text.split()
    if not words:
        return _both("usage", lo=MIN_EXECUTORS, hi=MAX_EXECUTORS)
    head = words[0].casefold()
    longest = 1 if head in _ONE_WORD_COMMANDS else 2
    if head in MANAGE_WORDS and len(words) <= longest:
        return Request(head, executor_id=words[1].lower() if len(words) == 2 else "")
    count = DEFAULT_EXECUTORS
    task = text.strip()
    if words[0].isascii() and words[0].isdigit():
        count = int(words[0])
        if not MIN_EXECUTORS <= count <= MAX_EXECUTORS:
            return _both("range", lo=MIN_EXECUTORS, hi=MAX_EXECUTORS, n=count)
        task = task[len(words[0]):].strip()
    if not task:
        return _both("usage", lo=MIN_EXECUTORS, hi=MAX_EXECUTORS)
    return Request("run", count, task)


def request_refusal(text: str) -> str | None:
    """Why ``/best-of <text>`` cannot start, or ``None``; the transport asks before it opens a turn."""
    parsed = parse_request(text)
    return parsed if isinstance(parsed, str) else None


# --- what is kept between turns ----------------------------------------------------


@dataclass(slots=True)
class Record:
    """What a waiting run remembers besides ``run.json``: its task, the styles and the verdict."""

    task: str
    styles: dict[str, str]
    verdict: dict[str, Any] | None = None

    def save(self, run: RunInfo) -> None:
        path = run.run_dir / RECORD_FILE
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps({"task": self.task, "styles": self.styles, "verdict": self.verdict},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary, path)

    @classmethod
    def load(cls, run: RunInfo) -> Record:
        """The record of ``run``; a run without a readable one (older, or damaged) is still manageable by id."""
        styles = {eid: STYLE_LADDER[index] for index, eid in enumerate(run.executors) if index < len(STYLE_LADDER)}
        try:
            stored = json.loads((run.run_dir / RECORD_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            _logger.warning("best-of run %s has no readable %s: %s", run.run_id, RECORD_FILE, error)
            return cls("", styles)
        return cls(str(stored.get("task") or ""), {**styles, **(stored.get("styles") or {})},
                   stored.get("verdict") or None)


# --- words -------------------------------------------------------------------------


def _cell(text: Any) -> str:
    return redact_text(" ".join(str(text).split())).replace("|", "\\|")


def _tests_cell(tests: dict[str, Any], language: str) -> str:
    return _say(language, f"tests_{tests.get('state') or 'unknown'}")


def _diff_cell(diff: dict[str, Any], language: str) -> str:
    return _say(language, "result_files", files=diff["files"], added=diff["added"], removed=diff["removed"])


def verdict_table(verdict: dict[str, Any], language: str) -> str:
    """The verdict as a Markdown table plus the winner and the reason: what the decision dialog and ``final`` show."""
    heads = [_say(language, key) for key in ("col_executor", "col_style", "col_outcome", "col_tests", "col_diff",
                                             "col_note")]
    lines = ["| " + " | ".join(heads) + " |", "|" + "---|" * len(heads)]
    for row in verdict["rows"]:
        cells = [row["executor_id"], row["style"], _say(language, f"outcome_{row['outcome']}"),
                 _tests_cell(row["tests"], language), _diff_cell(row["diff"], language), row.get("note") or ""]
        lines.append("| " + " | ".join(_cell(cell) for cell in cells) + " |")
    winner = next((row for row in verdict["rows"] if row["executor_id"] == verdict["winner"]), None)
    if winner is None:
        lines += ["", f"**{_say(language, 'winner')}:** {_say(language, 'winner_none')}"]
    else:
        lines += ["", f"**{_say(language, 'winner')}:** {verdict['winner']} ({winner['style']})"]
        if failing_winner(verdict):
            lines += ["", f"**{_say(language, 'warning')}:** {_say(language, 'winner_failing')}"]
    lines += ["", f"**{_say(language, 'reason')}:** {verdict['reason']}"]
    return "\n".join(lines)


def failing_winner(verdict: dict[str, Any]) -> bool:
    """True when the winner's tests ran and failed (the reviewer may name such a winner only if every rival fails too)."""
    return any(row["executor_id"] == verdict["winner"] and (row.get("tests") or {}).get("state") == "failed"
               for row in verdict["rows"])


def commit_message(task: str, executor_id: str, style: str, reason: str) -> str:
    """The first line of the task as the subject; the winner and the reviewer's reason below it."""
    lines = [line.strip() for line in task.splitlines() if line.strip()]
    subject = " ".join(lines[0].split()) if lines else f"Best-of result of {executor_id}"
    if len(subject) > SUBJECT_CHARS:
        subject = subject[:SUBJECT_CHARS - 3].rstrip() + "..."
    body = f"Best-of: {executor_id} ({style}) was chosen."
    return f"{subject}\n\n{body}\n\n{reason.strip()}\n" if reason.strip() else f"{subject}\n\n{body}\n"


def _fenced(text: str, language: str) -> str:
    fence = "```"
    while fence in text:
        fence += "`"
    return f"{fence}{language}\n{text.rstrip()}\n{fence}"


# --- turning the screen off for the reviewer's own words ------------------------------


class _QuietReview:
    """The main interface for the length of the review, minus the two things that are not for the person.

    The reviewer ends with a verdict, not an answer: its closing words are shown as ``final`` only by the turn
    itself, after the decision, and a second ``final`` would answer one command twice. The ``route`` event would
    tell the person the role is "judge", which is an internal name.
    """

    def __init__(self, shown: Any) -> None:
        object.__setattr__(self, "_shown", shown)

    def print_final(self, *args: Any, **kwargs: Any) -> None:
        return None

    def print_route(self, *args: Any, **kwargs: Any) -> None:
        return None

    def __getattr__(self, name: str) -> Any:
        return getattr(self._shown, name)

    def __setattr__(self, name: str, value: Any) -> None:
        setattr(self._shown, name, value)


# --- one run, from the person's side ---------------------------------------------------


class _Run:
    """A best-of run (live or left waiting): what the person is told and what they can decide."""

    def __init__(self, main: Any, run: RunInfo, record: Record, language: str) -> None:
        self.main = main
        self.run = run
        self.record = record
        self.language = language

    # --- events and results -------------------------------------------------------

    def _emit(self, name: str, payload: dict[str, Any]) -> None:
        printer = getattr(self.main._ui, name, None)
        if printer is not None:
            printer(payload)

    def _say(self, key: str, **fields: Any) -> str:
        return _say(self.language, key, **fields)

    def _finish(self, outcome: str, message: str, executor_id: str = "", commit: str = "") -> None:
        payload: dict[str, Any] = {"run_id": self.run.run_id, "outcome": outcome, "message": message}
        if executor_id:
            payload["executor_id"] = executor_id
        if commit:
            payload["commit"] = commit
        self._emit("print_bestof_finished", payload)

    def _result(self, ok: bool, key: str, final_parts: list[str], *, stopped: bool = False,
                outcome: str, **extra: Any) -> TurnResult:
        title = f"## {self._say('title')}"
        effects = {"run_id": self.run.run_id, "outcome": outcome, **extra}
        if stopped:
            effects["stopped"] = True
        # The one-line message names the executor and the short hash of the commit, when there are any.
        fields = {"executor_id": extra.get("executor_id", ""), "commit": str(extra.get("commit", ""))[:10]}
        return TurnResult(ok, _both(key, **fields), "\n\n".join([title, *final_parts]), stopped=stopped,
                          effects=effects)

    def _known(self, executor_id: str) -> None:
        if executor_id not in self.run.executors:
            raise BestOfRefused(_both("unknown_executor", id=executor_id, run=self.run.run_id,
                                      known=", ".join(self.run.executors)))

    def _style(self, executor_id: str) -> str:
        return self.record.styles.get(executor_id, "")

    # --- the picture ------------------------------------------------------------------

    def executor_lines(self) -> str:
        """One line per executor: its style and what it left."""
        lines = []
        for executor_id, info in self.run.executors.items():
            if info.status == worktrees.EXECUTOR_LOST:
                left = self._say("result_lost")
            elif info.result is None:
                left = self._say("result_none")
            else:
                diff = worktrees.summary(self.run, executor_id)
                left = self._say("result_files", files=len(diff.files), added=diff.added, removed=diff.removed)
            lines.append(f"- **{executor_id}** ({self._style(executor_id)}): {left}")
        return "\n".join(lines)

    def _finalists(self) -> list[str]:
        """The results the person may pick among: those the reviewer left in the running (winner included).

        With no winner the reviewer left none in the running, and the person chooses by hand among every result
        that changed something, because that is the whole point of choosing by hand.
        """
        verdict = self.record.verdict or {}
        rows = verdict.get("rows") or []
        if not verdict.get("winner"):
            return [row["executor_id"] for row in rows if row["diff"]["files"] > 0]
        return [row["executor_id"] for row in rows if row["outcome"] in ("winner", "finalist")]

    # --- the decision -----------------------------------------------------------------

    def _ask(self, title: str, options: list[tuple[str, str]], body: str) -> str | None:
        """The person's answer, or ``None`` when the dialog was closed or they stopped the turn while it was open.

        The dialog blocks in the interface. Over the event stream a stop closes it (``EventSession.cancel``:
        ``input_closed`` with ``reason: "cancelled"``, which also frees the thread); an interface that has no such
        channel is not woken by a stop, so the dialog is asked on a thread of its own and the turn watches for the
        stop. An answer that comes after the stop is ignored.
        """
        answer: list[str | None] = []
        failure: list[BaseException] = []
        answered = threading.Event()

        def ask() -> None:
            try:
                answer.append(self._ask_interface(title, options, body))
            except Exception as error:  # noqa: BLE001 - re-raised on the turn's own thread below
                failure.append(error)
            finally:
                answered.set()

        threading.Thread(target=ask, name="kvist-bestof-decision", daemon=True).start()
        while not answered.wait(_STOP_POLL_SECONDS):
            if self.main.control.interrupted():
                return None
        if failure:
            raise failure[0]
        return answer[0]

    def _ask_interface(self, title: str, options: list[tuple[str, str]], body: str) -> str | None:
        ui = self.main._ui
        decide = getattr(ui, "ask_decision", None)
        if decide is not None:
            return decide(title, options, body=body, purpose=PURPOSE_BESTOF_DECISION, run_id=self.run.run_id)
        choose = getattr(ui, "ask_choice", None)
        if choose is None:
            return None
        return choose(f"{title}\n\n{body}" if body else title, options)

    def decide(self) -> TurnResult:
        """Show the verdict and act on the person's answer; a closed dialog leaves the run pending."""
        verdict = self.record.verdict
        winner = verdict["winner"]
        options = self._decision_options(verdict)
        title = self._say("decision_title")
        table = verdict_table(verdict, self.language)
        body = table
        while True:
            answer = self._ask(title, options, body)
            if answer is None:
                return self.postpone()
            action, _, target = answer.partition(":")
            body = table
            if action == "accept" and winner:
                return self.take(winner)
            if action == "accept":
                body = f"{self._say('no_accept')}\n\n{table}"
            elif action == "discard":
                return self.discard()
            elif action == "diff":
                body = self._diff_body(target or winner or next(iter(self._finalists()), ""), table)
            elif action == "pick" and target in self._finalists():
                return self.take(target)
            elif action == "pick":
                body = f"{self._say('cannot_take', id=target)}\n\n{table}"
            elif action == "other":
                picked = self._ask_other(winner, table)
                if picked is None:
                    return self.postpone()
                if picked:
                    return self.take(picked)
                body = f"{self._say('no_other')}\n\n{table}" if not self._others(winner) else table

    def _decision_options(self, verdict: dict[str, Any]) -> list[tuple[str, str]]:
        """The question's choices; the first is what a frontend opens on, so it is never ``accept`` unless it is safe.

        A winner whose tests pass opens on accept. A winner whose tests fail opens on looking at the diff, and
        accepting it says so. With no winner there is nothing to accept: the question opens on discarding.
        """
        winner = verdict["winner"]
        discard = ("discard", self._say("option_discard"))
        if not winner:
            return [discard, ("diff", self._say("option_diff_none")), ("other", self._say("option_other_none"))]
        diff = ("diff", self._say("option_diff"))
        other = ("other", self._say("option_other"))
        if failing_winner(verdict):
            return [diff, ("accept", self._say("option_accept_failing", id=winner)), other, discard]
        return [("accept", self._say("option_accept", id=winner)), diff, other, discard]

    def _diff_body(self, executor_id: str, table: str) -> str:
        """The patch to ask the question over; a result that has none is said so above the table."""
        if not executor_id:
            return f"{self._say('no_other')}\n\n{table}"
        try:
            return self.show_diff(executor_id)
        except BestOfRefused as refused:
            return f"{_in_language(str(refused), self.language)}\n\n{table}"

    def _others(self, winner: str) -> list[str]:
        return [executor_id for executor_id in self._finalists() if executor_id != winner]

    def _ask_other(self, winner: str, table: str) -> str | None:
        """Which other finalist to take: its id, ``""`` for none (nothing to pick, or back), ``None`` when closed."""
        candidates = self._others(winner)
        if not candidates:
            return ""
        options = [(f"pick:{executor_id}", f"{executor_id} ({self._style(executor_id)})") for executor_id in candidates]
        options.append(("back", self._say("option_back")))
        answer = self._ask(self._say("other_title"), options, table)
        if answer is None:
            return None
        action, _, target = answer.partition(":")
        return target if action == "pick" and target in candidates else ""

    def show_diff(self, executor_id: str) -> str:
        """Tell the frontend the patch of ``executor_id`` and return it as the body of the next question."""
        self._known(executor_id)
        patch = redact_text(worktrees.full_diff(self.run, executor_id, limit=PATCH_CHARS))
        files = [change.path for change in worktrees.summary(self.run, executor_id).files]
        self._emit("print_bestof_diff", {"run_id": self.run.run_id, "executor_id": executor_id, "patch": patch,
                                         "files": files})
        return patch

    # --- the outcomes ------------------------------------------------------------------

    def _verdict_part(self) -> list[str]:
        return [verdict_table(self.record.verdict, self.language)] if self.record.verdict else []

    def take(self, executor_id: str) -> TurnResult:
        """Commit ``executor_id``'s change to the person's branch: one commit, or a stop with the reason.

        A stop that came before the branch moves keeps the run waiting; once git has the commit nothing is undone.
        """
        if self.main.control.interrupted():
            return self.postpone()
        style = self._style(executor_id)
        verdict = self.record.verdict or {}
        # With no winner the reason explains why nothing was good enough, which is not why this one was taken.
        reason = verdict.get("reason", "") if verdict.get("winner") else ""
        message = commit_message(self.record.task, executor_id, style, reason)
        try:
            taken = worktrees.accept(self.run, executor_id, message)
        except BestOfRefused as refused:
            return self._not_taken(executor_id, _in_language(str(refused), self.language))
        except GitError as error:
            if self.run.state != worktrees.STATE_ACCEPTED:
                return self._not_taken(executor_id, _in_language(str(error), self.language))
            return self._unsynced(executor_id, style, error)
        if not taken.accepted:
            return self._conflict(executor_id, taken)
        commit = taken.commit[:10]
        line = self._say("accepted", id=executor_id, style=style, commit=commit)
        self._finish("accepted", line, executor_id, taken.commit)
        return self._result(True, "msg_accepted", [line, *self._verdict_part()], outcome="accepted",
                            executor_id=executor_id, commit=taken.commit)

    def _unsynced(self, executor_id: str, style: str, error: GitError) -> TurnResult:
        commit = self.run.accepted_commit or ""
        steps = _in_language(str(error), self.language).partition("\n")[2]
        line = self._say("accepted_unsynced", id=executor_id, style=style, commit=commit[:10])
        self._finish("accepted", line, executor_id, commit)
        return self._result(False, "msg_accepted", [line, _fenced(steps, "sh"), *self._verdict_part()],
                            outcome="accepted", executor_id=executor_id, commit=commit)

    def _not_taken(self, executor_id: str, why: str) -> TurnResult:
        line = self._say("not_taken", id=executor_id, why=why)
        self._keep_pending(line)
        return self._result(False, "msg_pending", [line, self._say("next_steps")], outcome="pending",
                            executor_id=executor_id)

    def _conflict(self, executor_id: str, taken: worktrees.AcceptResult) -> TurnResult:
        head, _, git_text = _in_language(taken.conflict_text, self.language).partition("\n")
        parts = [self._say("conflict", id=executor_id), head.strip()]
        if taken.conflict_paths:
            parts.append(f"{self._say('conflict_files')}: " + ", ".join(f"`{path}`" for path in taken.conflict_paths))
        if git_text.strip():
            parts.append(_fenced(redact_text(git_text), "text"))
        parts.append(self._say("conflict_fix", id=executor_id))
        short = self._say("conflict", id=executor_id) + " " + self._say("conflict_fix", id=executor_id)
        self._keep_pending(short)
        return self._result(False, "msg_pending", parts, outcome="pending", executor_id=executor_id)

    def _keep_pending(self, message: str) -> None:
        """Tell the frontends the run waits for the person. A run that was already waiting keeps its own state."""
        if self.run.state == worktrees.STATE_RUNNING:
            worktrees.mark(self.run, worktrees.STATE_PENDING)
        self._finish("pending", message)

    def postpone(self) -> TurnResult:
        """The dialog was closed, nobody can answer, or the person stopped the turn: the results stay for
        ``/best-of accept | diff | discard``. A stop is reported as one (``stopped``), the run waits all the same."""
        stopped = self.main.control.interrupted()
        line = self._say("pending_stopped" if stopped else "pending")
        self._keep_pending(line)
        return self._result(not stopped, "msg_pending", [line, *self._verdict_part(), self._say("next_steps")],
                            stopped=stopped, outcome="pending")

    def discard(self) -> TurnResult:
        worktrees.discard(self.run)
        line = self._say("discarded")
        self._finish("discarded", line)
        return self._result(True, "msg_discarded", [line, *self._verdict_part()], outcome="discarded")


# --- a live run -------------------------------------------------------------------------


def judge_goal(task: str, executors: list[tuple[str, str]], language: str) -> str:
    """The reviewer's task: what was asked, who attempted it, the order of review with its reasons, how to end."""
    listing = "\n".join(f"- {executor_id}: style {style}" for executor_id, style in executors)
    name = response_language_name(language)
    return (
        "Review this best-of run and end it with your verdict.\n\n"
        f"The task every executor was given:\n{task}\n\n"
        f"The executors ({len(executors)}), each working alone in its own copy of the repository:\n{listing}\n\n"
        f"The person writes in {name}. Everything they read from you (notes, the reason, the reason of an "
        f"elimination) is in {name}.\n\n"
        "Review in this order, because each step makes the next cheaper:\n"
        "1. Cheating, from the diffs: deleted, skipped or xfail-marked tests, weakened asserts, stubs where logic "
        "belongs, commented-out code, edits that have nothing to do with the task. A green test run on such a diff "
        "means nothing, so check this before you trust any result. Use the summaries first; read the diff of a "
        "file only when it looks wrong.\n"
        "2. Tests: find out how the project runs them and set that command once with executor set_check, early; "
        "it then runs on each executor as it ends. Executors that ended before that are checked with executor "
        "check.\n"
        "3. Quality: read the full diff only of the executors still in the comparison. When two are equally "
        "correct the smaller diff wins, and a change outside the task counts against an attempt, never for it.\n"
        "Eliminate an executor as soon as you know it is out (executor eliminate, with the reason): that stops it "
        "and saves the person its cost. Use executor wait instead of polling; it makes no model requests.\n\n"
        "End with verdict: one row per executor, exactly one winner that finished and changed something, and a "
        "reason of two or three sentences. If no executor solves the task, that is a normal outcome: end with "
        "verdict whose winner is null, no winner row, and a reason that says what you found, instead of choosing "
        "the least bad one. A winner whose tests fail is allowed only when every executor still in the comparison "
        "fails them too."
    )


class _Flow(_Run):
    """The live part of a run: start the executors, review them, and hand the verdict to the person."""

    def __init__(self, main: Any, run: RunInfo, record: Record, pool: ExecutorPool, language: str,
                 count: int) -> None:
        super().__init__(main, run, record, language)
        self.pool = pool
        self.count = count
        self.tools = JudgeTools(pool)
        self._said = ""

    def execute(self) -> TurnResult:
        try:
            return self._execute()
        except Exception:
            self._abort()
            raise

    def _execute(self) -> TurnResult:
        self._emit("print_bestof_started", {
            "run_id": self.run.run_id, "task": self.record.task, "count": self.count, "base": self.run.snapshot[:10],
            "executors": [{"executor_id": eid, "style": self._style(eid), "branch": self.run.executor_branch(eid),
                           "session": _session_name(self.run.run_id, eid)} for eid in self.run.executors],
        })
        self.pool.start()
        if not self.main.control.interrupted():
            self._review()
        if self.main.control.interrupted():
            return self._stopped()
        if self.tools.verdict is None:
            return self._without_verdict()
        verdict = self.tools.verdict
        self.record.verdict = {"rows": verdict.rows, "winner": verdict.winner, "reason": verdict.reason}
        self.record.save(self.run)
        self._emit("print_bestof_verdict", {"run_id": self.run.run_id, **self.record.verdict})
        # The review's loop ended the task's control; the turn goes on (the dialog, the accept) and a stop must
        # still reach it.
        self.main.control.begin()
        return self.decide()

    def _review(self) -> None:
        """Run the main agent as the reviewer: the turn's own state is replaced by the review's and put back."""
        main = self.main
        goal = judge_goal(self.record.task, [(eid, self._style(eid)) for eid in self.run.executors], self.language)
        person_message = main._state.message_id
        # Named after what the person typed, not after the reviewer's instructions.
        main._request_session_title(f"/best-of {self.record.task}")
        shown = main._ui
        main._ui = _QuietReview(shown)
        main.judge_tools = self.tools
        try:
            # ``woken``: the reviewer's turn is not a message of the person's, so it takes no number among theirs
            # and keeps the language this turn already resolved.
            self._said = main._run_turn(goal, JUDGE_ROLE, language_text=self.record.task, woken=True)
        finally:
            main._ui = shown
            main.judge_tools = None
            main._state.message_id = person_message
            # The review is not a task to resume, and not a turn of the conversation: the best-of turn is.
            main._clear_checkpoint()
            if main._session_history and main._session_history[-1].get("role") == JUDGE_ROLE:
                main._session_history.pop()

    def _stopped(self) -> TurnResult:
        self.pool.cancel_all("user_cancelled")
        line = self._say("cancelled")
        self._finish("cancelled", line)
        return self._result(False, "msg_cancelled", [line, self.executor_lines(), self._say("next_steps")],
                            stopped=True, outcome="cancelled")

    def _without_verdict(self) -> TurnResult:
        self.pool.cancel_all("no_verdict")
        # cancel_all marks the run cancelled; the results are intact and the person decides next, so it waits.
        worktrees.mark(self.run, worktrees.STATE_PENDING)
        line = self._say("no_verdict")
        self._keep_pending(line)
        parts = [line, self.executor_lines()]
        if self._said.strip():
            parts.append(f"**{self._say('reviewer_said')}:** {self._said.strip()}")
        parts.append(self._say("next_steps"))
        return self._result(False, "msg_no_verdict", parts, outcome="pending")

    def _abort(self) -> None:
        """Something broke: stop the executors, keep what they made, say the run failed."""
        if self.run.state == worktrees.STATE_ACCEPTED:
            return
        self.pool.cancel_all("failed")
        self._finish("failed", self._say("failed"))


# --- the turn ---------------------------------------------------------------------------------


def run_bestof(main: Any, question: str) -> TurnResult:
    """``/best-of ...`` as the main agent's turn; returns the turn's result."""
    language = main._state.response_language or "en"
    request = parse_request(question)
    if isinstance(request, str):
        return TurnResult(False, request, _in_language(request, language))
    if request.kind == "run":
        return _start(main, request, language)
    return _manage(main, request, language)


def _start(main: Any, request: Request, language: str) -> TurnResult:
    run_id = uuid.uuid4().hex[:8]
    ids = executor_ids(request.count)
    styles = styles_for(request.count)
    try:
        run = worktrees.create_run(Path(main.executor.workspace), run_id, ids)
    except (BestOfRefused, GitError) as refused:
        text = _in_language(str(refused), language)
        return TurnResult(False, str(refused), f"## {_say(language, 'title')}\n\n{text}")
    record = Record(request.task, dict(zip(ids, styles)))
    record.save(run)
    pool = ExecutorPool(main, run, request.task, styles)
    return _Flow(main, run, record, pool, language, request.count).execute()


# --- a run left waiting -------------------------------------------------------------------------


def _manage(main: Any, request: Request, language: str) -> TurnResult:
    waiting = worktrees.recover(Path(main.executor.workspace))
    if not waiting:
        text = _say(language, "nothing_waiting")
        return TurnResult(False, _both("nothing_waiting"), f"## {_say(language, 'title')}\n\n{text}")
    run = max(waiting, key=lambda candidate: candidate.created)
    review = _Run(main, run, Record.load(run), language)
    if request.kind == "discard":
        return review.discard()
    if request.kind == "status":
        return _status(review)
    executor_id = request.executor_id or (review.record.verdict or {}).get("winner", "")
    known = ", ".join(run.executors)
    if not executor_id:
        return _unanswered(review, _both("no_winner", known=known), _say(language, "no_winner", known=known))
    if executor_id not in run.executors:
        fields = {"id": executor_id, "run": run.run_id, "known": known}
        return _unanswered(review, _both("unknown_executor", **fields), _say(language, "unknown_executor", **fields))
    if request.kind == "accept":
        return review.take(executor_id)
    return _show_diff(review, executor_id)


def _unanswered(review: _Run, message: str, text: str) -> TurnResult:
    return TurnResult(False, message, f"## {review._say('title')}\n\n{text}",
                      effects={"run_id": review.run.run_id})


def _status(review: _Run) -> TurnResult:
    run = review.run
    parts = [f"## {review._say('title')}: {review._say('run_title', run=run.run_id)}",
             f"**{review._say('state')}:** {review._say('state_' + run.state)}"]
    if review.record.task:
        parts.append(f"**{review._say('task')}:** {review.record.task}")
    parts += [f"**{review._say('executors')}:**\n{review.executor_lines()}", *review._verdict_part(),
              review._say("next_steps")]
    return TurnResult(True, _both("msg_shown"), "\n\n".join(parts), effects={"run_id": run.run_id, "state": run.state})


def _show_diff(review: _Run, executor_id: str) -> TurnResult:
    try:
        patch = review.show_diff(executor_id)
    except BestOfRefused as refused:
        return _unanswered(review, str(refused), _in_language(str(refused), review.language))
    title = f"## {review._say('title')}: {review._say('diff_title', id=executor_id)}"
    return TurnResult(True, _both("msg_shown"), f"{title}\n\n{_fenced(patch, 'diff')}",
                      effects={"run_id": review.run.run_id, "executor_id": executor_id})
