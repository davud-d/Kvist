# -*- coding: utf-8 -*-
"""Auto mode: a second model answers the approval questions instead of the person.

This is NOT a safe mode. The classifier can be wrong, and text in a file, a web
page or a command's output can push it toward a mistake. Everything it allows
runs with the person's permissions. What it can never touch: project ``deny``
rules, the built-in hard blocks, and the per-execution approval of an intrinsic
DANGEROUS command (the classifier may refuse those, never wave them through).

``AutoApprovalGate`` wraps whatever gate the executor was built with. While no
classifier is set it is a pass-through, so the wrapper can stay installed when
the person switches auto mode off. With one set, each question goes to the
classifier first:

* ``allow``  - the action runs, nobody is asked;
* ``deny``   - it does not run, and the model gets ``refusal_text`` explaining
  that the refusal covers the effect, not the wording;
* ``unsure`` - the person is asked through the inner gate, with a deadline of
  ``USER_DECISION_SECONDS``; no answer in time counts as no.

Questions of one response are classified in one model call (``prefetch``). Two
things keep the classifier from being called for the same thing twice, both for
the session only and both written to the run log: a command it allowed once is
allowed again for the same request, and at the start of a task it may name
command patterns (``preapprove``) that are certain to be needed. Those patterns
are vetted by code (``vet_pattern``), so a pattern that would let arbitrary code
run is dropped whatever the model proposed.
"""
from __future__ import annotations

import hashlib
import json
import shlex
import threading
from collections.abc import Callable
from concurrent.futures import Future
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass
from pathlib import Path

from kernel.sandbox.approvals import ApprovalTimedOut, FetchDecision
from kernel.sandbox.command_policy import canonical_executable_name
from kernel.sandbox.permission_policy import grants, pattern_is_specific
from kernel.sandbox.redaction import defang_prompt_boundary_tags, redact_text
from kernel.sandbox.terminal_types import TerminalError

USER_DECISION_SECONDS = 120.0
MAX_PREAPPROVED = 12
# How long a command that must ask waits for an answer already being fetched, before it asks itself.
PREAPPROVAL_WAIT_SECONDS = 60.0
PREFETCH_WAIT_SECONDS = 60.0

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts" / "system"
_PROMPT_PATH = _PROMPTS / "permission_classifier_prompt.txt"
_PREAPPROVAL_PROMPT_PATH = _PROMPTS / "permission_preapproval_prompt.txt"
_VERDICTS = ("allow", "deny", "unsure")

# Heads a pre-approved pattern may never start with: they run whatever they are given, reach the
# network, delete, or install.
_NEVER_HEADS = frozenset({
    "bash", "sh", "zsh", "fish", "dash", "sudo", "su", "doas", "env", "xargs", "eval", "exec", "find",
    "curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "rm", "dd", "chmod", "chown",
    "npx", "pnpx", "bunx", "pip", "pip3", "pipx", "uv", "docker", "kubectl",
    "node", "deno", "bun", "ruby", "perl", "php",
})
# Test and lint binaries whose own name already pins what runs, so a star may follow the name.
_PINNED_TOOLS = frozenset({"pytest", "ruff", "mypy", "eslint", "tsc", "prettier", "jest", "vitest", "black", "flake8", "pyright"})
_PYTHON_MODULES = frozenset({"pytest", "unittest", "ruff", "mypy", "black", "flake8", "pyright", "compileall"})
_GIT_SUBCOMMANDS = frozenset({"add", "commit", "diff", "log", "show", "status"})
_CHANGING_WORDS = frozenset({
    "install", "i", "add", "remove", "uninstall", "publish", "push", "deploy", "login", "exec", "dlx",
    "update", "upgrade", "init", "create",
})
_SHELL_OPERATORS = frozenset({"|", "||", "&&", "&", ";", ">", ">>", "<", "2>&1"})
_SHELL_CHARS = ";|&<>`$()"
_WILDCARDS = "*?["


class ClassifierError(Exception):
    """The classifier model could not be reached or did not answer."""


@dataclass(frozen=True, slots=True)
class Question:
    kind: str  # "command" | "confirm" | "fetch"
    subject: str  # what is asked, as the person would read it; untrusted text
    # What identifies the question when a verdict was fetched ahead of it: the
    # reason a command stops for approval is only known once it runs.
    key: str = ""
    argv: tuple[str, ...] = ()  # a command's own words, to match against the task's patterns


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: str  # "allow" | "deny" | "unsure"
    reason: str


@dataclass(frozen=True, slots=True)
class Background:
    """What the classifier knows about the work besides the question itself."""

    instructions: str = ""  # the repository's KVIST.md, already quoted as untrusted workspace content
    steps: tuple[str, ...] = ()  # the agent's own one-line summaries of its recent steps, oldest first
    # Earlier turns of this session, newest first: what the person asked and what the agent answered,
    # never tool input or output. Without it "continue" is a request with no content.
    earlier: str = ""


class Classifier:
    """Asks the model whether the person would approve each question."""

    def __init__(
        self,
        ask: Callable[[list[dict[str, str]]], str],
        request: Callable[[], str],
        background: Callable[[], Background] = Background,
    ) -> None:
        self._ask = ask
        self.request = request
        self._background = background

    def classify(self, questions: list[Question]) -> list[Verdict]:
        try:
            reply = self._ask(self._messages(questions))
        except ClassifierError as error:
            return [Verdict("unsure", f"the classifier did not answer: {error}")] * len(questions)
        return _parse_verdicts(reply, len(questions))

    def propose(self, goal: str, facts: str) -> list[tuple[str, str]]:
        """``(pattern, reason)`` pairs the model thinks the task will need; ``[]`` when it cannot say."""
        background = self._background()
        user = f"The person's request (JSON string):\n{json.dumps(goal, ensure_ascii=False)}\n\n"
        if background.earlier:
            user += _untrusted("earlier_turns", background.earlier) + "\n\n"
        if background.instructions:
            user += background.instructions + "\n\n"
        user += "Facts about the project:\n" + _untrusted("project_facts", facts)
        messages = [
            {"role": "system", "content": _PREAPPROVAL_PROMPT_PATH.read_text(encoding="utf-8")},
            {"role": "user", "content": user},
        ]
        try:
            reply = self._ask(messages)
        except ClassifierError:
            return []
        return _parse_patterns(reply)

    def _messages(self, questions: list[Question]) -> list[dict[str, str]]:
        actions = json.dumps(
            [
                {"id": index, "kind": item.kind, "subject": redact_text(item.subject)}
                for index, item in enumerate(questions)
            ],
            ensure_ascii=False,
        )
        background = self._background()
        user = f"The person's request and follow-ups (JSON string):\n{json.dumps(self.request(), ensure_ascii=False)}\n\n"
        if background.earlier:
            user += _untrusted("earlier_turns", background.earlier) + "\n\n"
        if background.instructions:
            # Already quoted by system_context with its own boundary tags and defanged there.
            user += background.instructions + "\n\n"
        if background.steps:
            steps = json.dumps(list(background.steps), ensure_ascii=False)
            user += _untrusted("agent_step_summaries", steps) + "\n\n"
        user += "Actions waiting for approval:\n" + _untrusted("approval_request", actions)
        return [
            {"role": "system", "content": _PROMPT_PATH.read_text(encoding="utf-8")},
            {"role": "user", "content": user},
        ]


def _untrusted(source: str, text: str) -> str:
    return (
        f'<UNTRUSTED_TOOL_OUTPUT source="{source}">\n{defang_prompt_boundary_tags(redact_text(text))}\n'
        "</UNTRUSTED_TOOL_OUTPUT>"
    )


def _parse_patterns(reply: str) -> list[tuple[str, str]]:
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        return []
    try:
        items = json.loads(reply[start:end + 1]).get("patterns")
    except (json.JSONDecodeError, AttributeError):
        return []
    if not isinstance(items, list):
        return []
    found = []
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("pattern"), str):
            reason = item.get("reason")
            found.append((" ".join(item["pattern"].split()), reason.strip() if isinstance(reason, str) else ""))
    return found


def vet_pattern(pattern: str) -> str:
    """Why a proposed pattern may not be used, or "" when it may.

    Code decides this, not the model: a pattern lasts for the whole task, so it
    must not let a command through that the classifier would have judged by its
    arguments (an interpreter, a shell, an install, a push, a wildcard in the middle).
    """
    tokens = pattern.split()
    if not tokens or not pattern_is_specific(pattern):
        return "it does not start with a command name"
    if any(char in token for token in tokens for char in _SHELL_CHARS):
        return "it contains a shell operator"
    if any(char in token for token in tokens[:-1] for char in _WILDCARDS):
        return "a wildcard may only end the pattern"
    head = canonical_executable_name(tokens[0])
    if head in _NEVER_HEADS:
        return f"'{head}' runs whatever it is given"
    if head in ("python", "python3") and not (
        len(tokens) >= 3 and tokens[1] == "-m" and tokens[2] in _PYTHON_MODULES
    ):
        return "an interpreter is allowed only as '-m <test or lint tool>'"
    if head == "git" and not (len(tokens) >= 2 and tokens[1] in _GIT_SUBCOMMANDS):
        return "this git command is not one of add, commit, diff, log, show, status"
    if head != "git" and any(token in _CHANGING_WORDS for token in tokens[1:]):
        return "it installs, publishes or pushes"
    literal = [token for token in tokens if not any(char in token for char in _WILDCARDS)]
    if len(literal) < len(tokens) and len(literal) < 2 and head not in _PINNED_TOOLS:
        return "a pattern with a wildcard must name the command and what it does"
    return ""


def workspace_facts(workspace: Path) -> str:
    """What the model needs to write commands that run here, from what the folder holds."""
    lines = []
    for relative in (".venv/bin/python", "venv/bin/python"):
        if (workspace / relative).is_file():
            lines.append(
                f"Python tools run through the project's own interpreter: {relative} -m <tool> "
                f"(for example '{relative} -m pytest'). A bare 'pytest' or 'python' does not reach the "
                "project's packages, so patterns must use this path."
            )
            break
    try:
        scripts = json.loads((workspace / "package.json").read_text(encoding="utf-8")).get("scripts")
    except (OSError, json.JSONDecodeError, AttributeError):
        scripts = None
    if isinstance(scripts, dict) and scripts:
        lines.append("package.json scripts: " + ", ".join(sorted(scripts)) + " (run as 'npm run <script>').")
    return "\n".join(lines) or "(nothing notable)"


def _parse_verdicts(reply: str, count: int) -> list[Verdict]:
    """One verdict per question; anything the model left unclear is ``unsure``."""
    unclear = Verdict("unsure", "the classifier's answer could not be read")
    start, end = reply.find("{"), reply.rfind("}")
    if start < 0 or end < start:
        return [unclear] * count
    try:
        decisions = json.loads(reply[start:end + 1]).get("decisions")
    except (json.JSONDecodeError, AttributeError):
        return [unclear] * count
    if not isinstance(decisions, list):
        return [unclear] * count
    verdicts = [unclear] * count
    for item in decisions:
        if not isinstance(item, dict):
            continue
        index, decision = item.get("id"), item.get("verdict")
        if not isinstance(index, int) or not 0 <= index < count or decision not in _VERDICTS:
            continue
        reason = item.get("reason")
        verdicts[index] = Verdict(decision, reason.strip() if isinstance(reason, str) else "")
    return verdicts


class AutoApprovalGate:
    """Outer approval gate: the classifier answers, the person only when it cannot."""

    def __init__(self, inner) -> None:
        self._inner = inner
        self.classifier: Classifier | None = None
        # Receives every decision for the run log; set by the agent. The screen never shows them.
        self.notify: Callable[[dict[str, str]], None] | None = None
        self._lock = threading.Lock()
        self._futures: dict[str, Future[Verdict | None]] = {}  # verdicts being fetched ahead, by key
        self._allowed: set[str] = set()  # keys of commands and fetches the classifier allowed
        self._patterns: tuple[str, ...] = ()  # vetted patterns of the current task
        self._preapproval: threading.Event | None = None  # set when the patterns are chosen
        self._generation = 0  # a task's patterns land only if no newer task started meanwhile
        self._local = threading.local()

    def __getattr__(self, name: str):
        # answers_from_any_thread, waiting_confirm_command and the like belong to the inner gate.
        return getattr(self._inner, name)

    @property
    def auto_active(self) -> bool:
        return self.classifier is not None

    def set_classifier(self, classifier: Classifier | None) -> None:
        with self._lock:
            self.classifier = classifier
            self._futures = {}
            self._allowed = set()
            self._patterns = ()
            self._generation += 1

    def preapprove(self, goal: str, facts: str) -> None:
        """At the start of a task: let the classifier name the commands it will certainly need.

        Runs beside the agent's own first model call, which usually takes longer. A command
        that asks before it is done waits for it (``wait_for_preapproval``), then is matched
        against the patterns, or classified on its own when none fits.
        """
        classifier = self.classifier
        done = threading.Event()
        with self._lock:
            self._generation += 1
            generation = self._generation
            self._patterns = ()
            self._futures = {}
            # "continue" reads the same in two tasks: what was allowed for a request ends with its run.
            self._allowed = set()
            self._preapproval = done
        if classifier is None:
            done.set()
            return
        threading.Thread(
            target=self._choose_patterns, args=(classifier, generation, goal, facts, done), daemon=True,
            name="kvist-preapproval",
        ).start()

    def wait_for_preapproval(self) -> None:
        done = self._preapproval
        if done is not None:
            done.wait(PREAPPROVAL_WAIT_SECONDS)

    def _choose_patterns(self, classifier: Classifier, generation: int, goal: str, facts: str,
                         done: threading.Event) -> None:
        try:
            kept = []
            for pattern, reason in classifier.propose(goal, facts)[:MAX_PREAPPROVED]:
                problem = vet_pattern(pattern)
                self._note("preapproval", "rejected" if problem else "allow", pattern, problem or reason)
                if not problem:
                    kept.append(pattern)
            with self._lock:
                if generation == self._generation:
                    self._patterns = tuple(kept)
        finally:
            done.set()

    def take_refusal(self) -> str:
        """The model-facing text of this thread's last refusal, once."""
        text = getattr(self._local, "refusal", "")
        self._local.refusal = ""
        return text

    def commit_remembered_rule(self) -> None:
        self._inner.commit_remembered_rule()

    # -- the gate protocol ----------------------------------------------------

    def confirm_command(self, command: list[str], reason: str, timeout: float | None = None) -> bool:
        if self.classifier is None:
            return self._inner.confirm_command(command, reason)
        question = command_question(command, reason)
        outcome = self._screen(question, may_allow=not _is_dangerous(command))
        if outcome != "ask":
            return outcome == "allow"
        return self._ask_person(
            lambda: self._inner.confirm_command(command, reason, timeout=USER_DECISION_SECONDS), False
        )

    def confirm(self, title: str, body: str, question: str, timeout: float | None = None) -> bool:
        if self.classifier is None:
            return self._inner.confirm(title, body, question)
        outcome = self._screen(Question("confirm", f"{title}\n{body}\n{question}"), may_allow=True)
        if outcome != "ask":
            return outcome == "allow"
        return self._ask_person(
            lambda: self._inner.confirm(title, body, question, timeout=USER_DECISION_SECONDS), False
        )

    def confirm_fetch_domain(self, host: str, url: str, timeout: float | None = None) -> FetchDecision:
        if self.classifier is None:
            return self._inner.confirm_fetch_domain(host, url)
        # "once" only: a classifier never makes a host trusted for good.
        outcome = self._screen(Question("fetch", f"Fetch a web page from {host}: {url}"), may_allow=True)
        if outcome != "ask":
            return "once" if outcome == "allow" else "deny"
        return self._ask_person(
            lambda: self._inner.confirm_fetch_domain(host, url, timeout=USER_DECISION_SECONDS), "deny"
        )

    # -- classification -------------------------------------------------------

    def prefetch(self, questions: list[Question]) -> None:
        """Classify every command of one response that will ask, together, in one call.

        The classifier sees the whole batch (at most what one response may hold), so it can judge
        the commands as parts of one plan and costs one request, not one per command. Each command
        takes its answer from here when its turn comes, waiting only if it is still coming.
        """
        classifier = self.classifier
        if classifier is None:
            return
        request = classifier.request()
        batch: dict[str, Question] = {}
        for question in questions:
            batch.setdefault(_key(request, question), question)
        if len(batch) < 2:
            return
        futures: dict[str, Future[Verdict | None]] = {key: Future() for key in batch}
        with self._lock:
            self._futures.update(futures)
        threading.Thread(
            target=self._classify_ahead, args=(classifier, list(batch.values()), list(futures.values())),
            daemon=True, name="kvist-classify-ahead",
        ).start()

    @staticmethod
    def _classify_ahead(classifier: Classifier, questions: list[Question], futures: list[Future]) -> None:
        verdicts: list[Verdict | None] = []
        try:
            verdicts = list(classifier.classify(questions))
        finally:
            # None sends a waiting command back to classifying itself.
            for index, future in enumerate(futures):
                future.set_result(verdicts[index] if index < len(verdicts) else None)

    def _screen(self, question: Question, may_allow: bool) -> str:
        """"allow" | "deny" | "ask": what the gate does with the question."""
        classifier = self.classifier
        assert classifier is not None
        self._local.refusal = ""  # a text nobody collected must not answer a later question
        self.wait_for_preapproval()
        request = classifier.request()
        key = _key(request, question)
        verdict = self._already_known(question, key)
        if verdict is None:
            verdict = self._classified_ahead(key)
        if verdict is None:
            verdict = classifier.classify([question])[0]
        self._report(question, verdict)
        if verdict.decision == "deny":
            self._local.refusal = refusal_text(verdict.reason)
            return "deny"
        if verdict.decision == "allow" and may_allow:
            if question.kind in ("command", "fetch"):
                with self._lock:
                    self._allowed.add(key)
            return "allow"
        return "ask"

    def _classified_ahead(self, key: str) -> Verdict | None:
        with self._lock:
            future = self._futures.pop(key, None)
        if future is None:
            return None
        try:
            return future.result(PREFETCH_WAIT_SECONDS)
        except FutureTimeout:
            return None

    def is_known(self, question: Question) -> bool:
        """Whether the question needs no classifier call (so a batch need not include it)."""
        classifier = self.classifier
        return classifier is not None and self._already_known(question, _key(classifier.request(), question)) is not None

    def _already_known(self, question: Question, key: str) -> Verdict | None:
        """An allow the classifier owes no new thought: a task pattern, or the same thing allowed before."""
        with self._lock:
            if key in self._allowed:
                return Verdict("allow", "allowed earlier for the same request")
            patterns = self._patterns
        if question.argv and not any(token in _SHELL_OPERATORS for token in question.argv):
            for pattern in patterns:
                if grants(pattern, list(question.argv)):
                    return Verdict("allow", f"matches the pattern '{pattern}' chosen at the start of the task")
        return None

    def _ask_person(self, ask, on_timeout):
        try:
            return ask()
        except ApprovalTimedOut:
            self._local.refusal = unanswered_text(int(USER_DECISION_SECONDS))
        except TerminalError:
            # A run nobody watches (no terminal, no frontend): waiting would be pointless.
            self._local.refusal = unanswered_text(None)
        return on_timeout

    def _report(self, question: Question, verdict: Verdict) -> None:
        self._note(question.kind, verdict.decision, question.subject.splitlines()[0], verdict.reason)

    def _note(self, kind: str, decision: str, subject: str, reason: str) -> None:
        if self.notify is not None:
            self.notify({"kind": kind, "decision": decision, "subject": subject, "reason": reason})


def command_question(command: list[str], reason: str) -> Question:
    line = shlex.join(command)
    return Question("command", f"{line}\n(stops for approval because: {reason})", key=line, argv=tuple(command))


def _key(request: str, question: Question) -> str:
    identity = question.key or question.subject
    return hashlib.sha1(f"{request}\0{question.kind}\0{identity}".encode("utf-8")).hexdigest()


def _is_dangerous(command: list[str]) -> bool:
    from kernel.sandbox.command_policy import RiskLevel, assess_command

    return assess_command(command, role="coder").risk is RiskLevel.DANGEROUS


def refusal_note(gate) -> str:
    """The auto-mode refusal text the gate left for this thread, or "" when it left none."""
    take = getattr(gate, "take_refusal", None)
    return take() if take is not None else ""


def refusal_text(reason: str) -> str:
    """What the model is told when the classifier said no. One principle: never go around a refusal."""
    return (
        "AUTO MODE: the automatic permission classifier declined this action.\n\n"
        f"Its reason: {reason or '(none given)'}\n\n"
        "The action did not run, and nothing it would have changed was changed.\n\n"
        "A check that can be walked around is not a check. The person turned on auto mode and placed a "
        "second judgement between what you intend and what happens on their machine, and the whole worth of "
        "that judgement is that its \"no\" holds. What was declined is an effect, not a sentence: the same "
        "effect reached by another command, a script, another tool, a skill, a helper, or in smaller steps "
        "is the same action, and finding such a way would be answering for the person a question they "
        "chose to keep.\n\n"
        "So never try to get around this refusal, however reasonable the detour looks. Take it as "
        "information: what the person wants reaches further, or less far, than your reading of the request. "
        "Carry on with what is plainly within the request. If the task cannot be finished without this "
        "action, say that in your answer, plainly: what you wanted to do, why the task needs it, what you "
        "did instead and what stays undone, and say that the classifier declined it. The person decides "
        "from there. A task left honestly unfinished is worth more than one finished by evading them."
    )


def unanswered_text(seconds: int | None) -> str:
    """What the model is told when the classifier could not decide and the person did not answer.

    Not a verdict on the action, so it carries no warning against asking again or finding another way.
    """
    asked = (
        f"No answer came within {seconds // 60} minutes."
        if seconds is not None
        else "Nobody could be asked (no terminal or frontend is attached)."
    )
    return (
        "AUTO MODE: this action was not approved, and nothing was decided about it.\n\n"
        "The automatic classifier could not tell whether the person would want this action, so the "
        f"question went to the person. {asked} An unanswered question counts as no. The action did not "
        "run, and nothing it would have changed was changed.\n\n"
        "This is not a judgement on the action. The person has not said it is wrong; nobody said it was "
        "right. Carry on with the parts of the task that do not need it, and in your answer say what is "
        "waiting for a decision and why, so the person can give it when they are back."
    )
