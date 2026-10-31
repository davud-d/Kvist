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

Questions of one response are classified in one model call (``prefetch``).
"""
from __future__ import annotations

import hashlib
import json
import shlex
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kernel.sandbox.approvals import ApprovalTimedOut, FetchDecision
from kernel.sandbox.redaction import defang_prompt_boundary_tags, redact_text
from kernel.sandbox.terminal_types import TerminalError

USER_DECISION_SECONDS = 120.0

_PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "system" / "permission_classifier_prompt.txt"
_VERDICTS = ("allow", "deny", "unsure")


class ClassifierError(Exception):
    """The classifier model could not be reached or did not answer."""


@dataclass(frozen=True, slots=True)
class Question:
    kind: str  # "command" | "confirm" | "fetch"
    subject: str  # what is asked, as the person would read it; untrusted text
    # What identifies the question when a verdict was fetched ahead of it: the
    # reason a command stops for approval is only known once it runs.
    key: str = ""


@dataclass(frozen=True, slots=True)
class Verdict:
    decision: str  # "allow" | "deny" | "unsure"
    reason: str


@dataclass(frozen=True, slots=True)
class Background:
    """What the classifier knows about the work besides the question itself."""

    instructions: str = ""  # the repository's KVIST.md, already quoted as untrusted workspace content
    steps: tuple[str, ...] = ()  # the agent's own one-line summaries of its recent steps, oldest first


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
        self._prefetched: dict[str, Verdict] = {}
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
            self._prefetched = {}

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
        """Classify the questions of one response in a single model call."""
        classifier = self.classifier
        if classifier is None or len(questions) < 2:
            return
        verdicts = classifier.classify(questions)
        request = classifier.request()
        with self._lock:
            self._prefetched = {_key(request, q): v for q, v in zip(questions, verdicts)}

    def _screen(self, question: Question, may_allow: bool) -> str:
        """"allow" | "deny" | "ask": what the gate does with the question."""
        classifier = self.classifier
        assert classifier is not None
        self._local.refusal = ""  # a text nobody collected must not answer a later question
        request = classifier.request()
        with self._lock:
            verdict = self._prefetched.pop(_key(request, question), None)
        if verdict is None:
            verdict = classifier.classify([question])[0]
        self._report(question, verdict)
        if verdict.decision == "deny":
            self._local.refusal = refusal_text(verdict.reason)
            return "deny"
        if verdict.decision == "allow" and may_allow:
            return "allow"
        return "ask"

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
        if self.notify is None:
            return
        subject = question.subject.splitlines()[0]
        self.notify({"kind": question.kind, "decision": verdict.decision, "subject": subject, "reason": verdict.reason})


def command_question(command: list[str], reason: str) -> Question:
    line = shlex.join(command)
    return Question("command", f"{line}\n(stops for approval because: {reason})", key=line)


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


_AFTER_A_REFUSAL = """\
What this is not: a glitch, a permission you can obtain by trying again, or a mistake in the wording of your \
command. The refusal is about what the action does, so the same effect in other clothes meets the same answer.

What to do:
- Do not look for another route to the same effect: another command, a script that does it, a different tool, \
a skill, a helper, the same change in smaller steps, renamed files so the check does not recognise it. A \
workaround is exactly what this check exists to stop, and going around it goes against what the person chose.
- Carry on with the parts of the task that do not need this action, as long as they are plainly within the \
request.
- If the action is essential to the task, stop and say so in your answer: what you were trying to do, why the \
task needs it, what you did instead and what stays undone. The person decides how to go on. They can allow it \
themselves (a permission rule, or asking you to try again once they agree) or point you to another way.
- Report this as it was: the action was declined by the automatic classifier. Never describe it as an error \
of something else."""


def refusal_text(reason: str) -> str:
    return (
        "AUTO MODE: the automatic permission classifier declined this action.\n\n"
        f"The classifier's reason: {reason or '(none given)'}\n\n"
        "What happened: the person turned on auto mode, so a second model reviews the actions that would "
        "otherwise wait for their approval. It judged that this one is not something the person asked for or "
        "would approve. The action did not run, and nothing it would have changed was changed.\n\n"
        + _AFTER_A_REFUSAL
    )


def unanswered_text(seconds: int | None) -> str:
    """``seconds`` is how long the person was given; None when nobody could be asked at all."""
    asked = (
        f"so the person was asked. No answer came within {seconds // 60} minutes, and no answer counts as no."
        if seconds is not None
        else "and nobody could be asked (no terminal or frontend is attached), which counts as no."
    )
    return (
        "AUTO MODE: this action was not approved.\n\n"
        "What happened: the person turned on auto mode, and the automatic classifier could not decide this "
        f"action on its own, {asked} The action did not run, and nothing it would have changed was changed.\n\n"
        + _AFTER_A_REFUSAL
    )
