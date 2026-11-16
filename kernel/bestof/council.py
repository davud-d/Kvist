# -*- coding: utf-8 -*-
"""Council mode: four read-only members look at one architectural question, each from one angle.

Round 1: every member reads what it needs and hands in a card (one decision and its main argument). Round 2, the
only one: each member gets the other members' cards, never their reasoning, and names the main risk it sees in each
other decision. Then one model call (the main agent's smart model, no tools) chooses exactly one of the decisions.
The code copies the chosen decision from its card, so the verdict never carries words the model made up.

A member is a ``LocalAgent`` of its own, in a child session of the main one (``kind: "council_member"``), with the
reader role, no helpers and nobody to ask. All members share the system prompt and tools; only the angle, the last
line of the goal, differs, so a provider that caches the prefix serves members 2-4 from the cache member 1 made.
That is why member 1 goes first and the others start once the model has answered it once.

The events a frontend sees are ``council_started``, ``council_card``, ``council_objection``, ``council_verdict``
(UI_API.md section 7); ``angle`` in all of them is the angle's id, which is also the member's ``executor_id``.
"""
from __future__ import annotations

import copy
import json
import threading
import uuid
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from kernel.agent.agent_state import RunCancelled
from kernel.agent.helpers import _HelperUI
from kernel.bestof.turns import TurnResult
from kernel.sandbox.redaction import defang_prompt_boundary_tags, redact_text

# A member reads to settle one question; the council waits for the slowest of four.
MEMBER_MAX_STEPS = 12
# Member 1 warms the provider's prompt cache; the rest do not wait longer than this for it.
FIRST_ANSWER_TIMEOUT = 90.0
MIN_CARDS = 2
_POLL_SECONDS = 0.05


@dataclass(frozen=True, slots=True)
class Angle:
    id: str
    brief: str
    label_en: str
    label_ru: str


ANGLES = (
    Angle("simplicity", "the simplest thing that can work: fewest moving parts, least new code and fewest new "
          "concepts for the next reader", "Simplicity", "Простота"),
    Angle("security", "what can go wrong for safety: untrusted input, secrets, permissions and how far a failure "
          "spreads", "Security", "Безопасность"),
    Angle("speed", "speed of development: how soon this works in front of people and what it blocks or unblocks next",
          "Development speed", "Скорость разработки"),
    Angle("maintenance", "the cost of keeping it: upgrades, debugging, who owns it a year from now and how it ages",
          "Maintenance cost", "Цена поддержки"),
)

_PROMPTS = Path(__file__).resolve().parent.parent / "prompts" / "bestof"


def _prompt(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8").strip()


_MEMBER_INSTRUCTIONS = _prompt("council_member.txt").replace("{max_steps}", str(MEMBER_MAX_STEPS))
_OBJECTION_INSTRUCTIONS = _prompt("council_objection.txt")
_CHOOSER_SYSTEM = _prompt("council_chooser.txt")

_RETRY_CARD = ('Your last answer has no usable JSON card. Answer now with only this object, no other text and no more '
               'reading: {"decision": "...", "argument": "..."}')
_RETRY_RISKS = ('Your last answer has no usable JSON object. Answer now with only this object, no other text and no '
                'more reading: {"risks": [{"target": "<angle id>", "risk": "..."}]}')

# Words of the answer the person reads, by language.
_TEXT = {
    # The mode's name, the same in both languages; the intro line under it says what it is.
    "title": ("council", "council"),
    "intro": ("Four members looked at the question, each from one angle, then named the main risks in each other's "
              "decisions, and one decision was chosen.",
              "Четыре участника посмотрели на вопрос, каждый со своей стороны, затем назвали главные риски в решениях "
              "друг друга, и выбрано одно решение."),
    "question": ("Question", "Вопрос"),
    "cards": ("Cards", "Карточки"),
    "objections": ("Objections", "Возражения"),
    "decision": ("Decision", "Решение"),
    "against": ("Still against", "Остались против"),
    "no_one": ("Nobody objects to the choice.", "К выбору возражений не осталось."),
    "dropped": ("Left the council", "Выбыли"),
    "dropped_card": ("no usable card", "нет пригодной карточки"),
    "no_objection": ("gave no usable objections", "не дали пригодных возражений"),
    "stopped": ("The council was stopped before it reached a decision.", "council остановлен до решения."),
    "to": ("on", "к"),
}


def _pick(language: str, key: str) -> str:
    english, russian = _TEXT[key]
    return russian if language == "ru" else english


class _CouncilError(Exception):
    """The council cannot reach a verdict; ``english`` and ``russian`` are what the person reads."""

    def __init__(self, english: str, russian: str) -> None:
        super().__init__(english)
        self.english = english
        self.russian = russian


@dataclass(slots=True)
class _Member:
    angle: Angle
    session: str
    agent: Any
    thread: threading.Thread | None = None
    card: dict[str, str] | None = None
    risks: list[dict[str, str]] | None = None
    dropped: str = ""


class _FirstAnswerClient:
    """The main client, telling when the model has answered a request for the first time (or failed to).

    A streamed answer is not answered when ``create`` returns: some clients return a lazy generator and send the
    request only on its first step. The signal then comes with the first chunk (or the end or failure of the
    stream), by which time the provider has read the prompt, which is what its cache needs.
    """

    def __init__(self, client: Any, answered: threading.Event) -> None:
        self._client = client
        self._answered = answered
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        try:
            response = self._client.chat.completions.create(**kwargs)
        except BaseException:
            self._answered.set()
            raise
        if not kwargs.get("stream"):
            self._answered.set()
            return response
        return self._signal_on_first_chunk(response)

    def _signal_on_first_chunk(self, stream: Any) -> Iterator[Any]:
        try:
            for chunk in stream:
                self._answered.set()
                yield chunk
        finally:
            self._answered.set()
            close = getattr(stream, "close", None)
            if close is not None:
                close()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._client, name)


def member_goal(question: str, angle: Angle) -> str:
    """The question, what every member is told, and last the angle: the only part that differs."""
    return f"{question}\n\n{_MEMBER_INSTRUCTIONS}\n\nYour angle: {angle.id} — {angle.brief}"


def _json_objects(text: str) -> Iterator[dict]:
    decoder = json.JSONDecoder()
    start = text.find("{")
    while start != -1:
        try:
            value, end = decoder.raw_decode(text, start)
        except ValueError:
            start = text.find("{", start + 1)
            continue
        if isinstance(value, dict):
            yield value
        start = text.find("{", end)


def _text_of(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def parse_card(text: str) -> dict[str, str] | None:
    """The first JSON object in the answer that carries a decision and an argument."""
    for found in _json_objects(text):
        decision, argument = _text_of(found.get("decision")), _text_of(found.get("argument"))
        if decision and argument:
            return {"decision": decision, "argument": argument}
    return None


def parse_risks(text: str, targets: list[str]) -> list[dict[str, str]] | None:
    """The first JSON object with at least one risk aimed at a member in ``targets``; one risk per target."""
    for found in _json_objects(text):
        entries = found.get("risks")
        if not isinstance(entries, list):
            continue
        risks: dict[str, str] = {}
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            target, risk = _text_of(entry.get("target")), _text_of(entry.get("risk"))
            if target in targets and risk and target not in risks:
                risks[target] = risk
        if risks:
            return [{"target": target, "risk": risk} for target, risk in risks.items()]
    return None


def _dissent_row(entry: Any) -> dict[str, str]:
    source = entry if isinstance(entry, dict) else {}
    return {"executor_id": _text_of(source.get("executor_id")), "why": _text_of(source.get("why"))}


def check_verdict(text: str, carded: list[str]) -> tuple[dict | None, str]:
    """The model's choice if it is valid, else ``None`` and what was wrong (said to the model on the retry)."""
    for found in _json_objects(text):
        if "chosen" not in found:
            continue
        chosen = _text_of(found.get("chosen"))
        if chosen not in carded:
            return None, (f'"chosen" must be exactly one of: {", ".join(carded)}. A mix of decisions or a '
                          "compromise is not a choice.")
        reason = _text_of(found.get("reason"))
        if not reason:
            return None, '"reason" is missing.'
        dissent = found.get("dissent", [])
        if not isinstance(dissent, list):
            return None, '"dissent" must be a list.'
        rows = [_dissent_row(entry) for entry in dissent]
        named = [row["executor_id"] for row in rows]
        if any(not row["why"] or row["executor_id"] not in carded or row["executor_id"] == chosen for row in rows) \
                or len(set(named)) != len(named):
            return None, ('every "dissent" entry needs a "why" and the "executor_id" of a different member, '
                          "each member at most once.")
        return {"chosen": chosen, "reason": reason, "dissent": rows}, ""
    return None, "there is no JSON object with a \"chosen\" member in it."


class _Council:
    def __init__(self, main: Any, question: str) -> None:
        self.main = main
        self.question = question
        self.language = main._state.response_language
        self.council_id = f"c{uuid.uuid4().hex[:8]}"
        self.members: list[_Member] = []
        self.first_answered = threading.Event()
        self._notes: list[str] = []

    # --- the whole council ------------------------------------------------

    def run(self) -> TurnResult:
        self._build_members()
        self.main._ui.print_council_started({
            "council_id": self.council_id, "question": self.question,
            "members": [{"executor_id": m.angle.id, "angle": m.angle.id, "session": m.session} for m in self.members],
        })
        try:
            return self._deliberate()
        except RunCancelled:
            return self._stopped()
        except _CouncilError as error:
            return self._failed(error)
        finally:
            for member in self.members:
                self.main.control.release(member.agent.control)

    def _deliberate(self) -> TurnResult:
        self._collect_cards()
        if self._interrupted():
            return self._stopped()
        carded = self._carded()
        if len(carded) < MIN_CARDS:
            raise self._too_few_cards(len(carded))
        self._collect_objections(carded)
        if self._interrupted():
            return self._stopped()
        return self._decided(self._choose(carded), carded)

    def _carded(self) -> list[_Member]:
        return [m for m in self.members if m.card is not None]

    def _interrupted(self) -> bool:
        return self.main.control.interrupted()

    # --- members ----------------------------------------------------------

    def _build_members(self) -> None:
        first_client = _FirstAnswerClient(self.main.client, self.first_answered)
        for index, angle in enumerate(ANGLES):
            self.members.append(self._build_member(angle, first_client if index == 0 else self.main.client))

    def _build_member(self, angle: Angle, client: Any) -> _Member:
        from kernel.agent.agent import LocalAgent
        from kernel.sandbox.approvals import DenyApprovalGate
        from kernel.sandbox.executor import SafeExecutor

        main = self.main
        config = copy.copy(main.config)
        config.interactive = False
        config.end_session_enabled = False
        config.memory_auto_processing = False
        config.max_steps = min(config.max_steps, MEMBER_MAX_STEPS)
        executor = SafeExecutor(main.executor.workspace, approvals=DenyApprovalGate(),
                                read_roots=main.executor.read_roots, scratchpad=main.executor.scratchpad)
        # The part that makes the name unique comes first: a long parent name is cut at the end.
        session = f"council-{self.council_id}-{angle.id}-{main.current_session}"[:120]
        agent = LocalAgent(client, executor, config, ui=_HelperUI(lambda payload: None), console=main.console,
                           service_session={"kind": "council_member", "parent_session": main.current_session,
                                            "council_id": self.council_id, "executor_id": angle.id,
                                            "angle": angle.id},
                           session=session)
        agent.force_read_only = True
        main.control.adopt(agent.control)
        return _Member(angle, session, agent)

    def _start(self, member: _Member, work: Callable[[_Member], None]) -> None:
        member.thread = threading.Thread(target=work, args=(member,), name=f"kvist-council-{member.angle.id}",
                                         daemon=True)
        member.thread.start()

    def _join(self, members: list[_Member]) -> None:
        """Wait for the members' threads; a stop reaches them again and again, so none can miss it."""
        threads = [m for m in members if m.thread is not None]
        while any(m.thread.is_alive() for m in threads):
            if self._interrupted():
                for member in threads:
                    if member.thread.is_alive():
                        member.agent.request_cancel()
            for member in threads:
                member.thread.join(_POLL_SECONDS)

    def _ask(self, member: _Member, prompt: str, retry: str, parse: Callable[[str], Any]) -> Any:
        """Run the member on ``prompt``; ask once more if the answer is unusable. ``None`` drops the answer."""
        for message in (prompt, retry):
            try:
                answer = member.agent.run(message, routed_role="reader")
            except Exception as error:  # noqa: BLE001 - one member's crash costs that member, not the council
                member.dropped = redact_text(f"{type(error).__name__}: {error}")[:300]
                return None
            if member.agent.control.cancel_requested():
                return None
            parsed = parse(answer or "")
            if parsed is not None:
                return parsed
        return None

    # --- round 1: cards -----------------------------------------------------

    def _collect_cards(self) -> None:
        first, rest = self.members[0], self.members[1:]
        self._start(first, self._hand_in_card)
        self._wait_for_first_answer(first)
        if self._interrupted():
            self._join([first])
            return
        for member in rest:
            self._start(member, self._hand_in_card)
        self._join(self.members)

    def _wait_for_first_answer(self, first: _Member) -> None:
        waited = 0.0
        while not self.first_answered.is_set() and first.thread.is_alive() and not self._interrupted():
            if waited >= FIRST_ANSWER_TIMEOUT:
                return
            self.first_answered.wait(_POLL_SECONDS)
            waited += _POLL_SECONDS

    def _hand_in_card(self, member: _Member) -> None:
        member.card = self._ask(member, member_goal(self.question, member.angle), _RETRY_CARD, parse_card)
        if member.card is None:
            return
        self.main._ui.print_council_card({
            "council_id": self.council_id, "executor_id": member.angle.id, "angle": member.angle.id,
            "decision": member.card["decision"], "argument": member.card["argument"],
        })

    # --- round 2: objections --------------------------------------------------

    def _collect_objections(self, carded: list[_Member]) -> None:
        for member in carded:
            self._start(member, lambda m, cards=carded: self._object(m, cards))
        self._join(carded)

    def _object(self, member: _Member, carded: list[_Member]) -> None:
        targets = [other.angle.id for other in carded if other is not member]
        member.risks = self._ask(member, self._objection_prompt(member, carded), _RETRY_RISKS,
                                 lambda text: parse_risks(text, targets))
        if member.risks is None:
            return
        self.main._ui.print_council_objection({
            "council_id": self.council_id, "executor_id": member.angle.id, "angle": member.angle.id,
            "risks": member.risks,
        })

    def _objection_prompt(self, member: _Member, carded: list[_Member]) -> str:
        others = "\n".join(f"- {other.angle.id}: {other.card['decision']}\n  Argument: {other.card['argument']}"
                           for other in carded if other is not member)
        return (f"Round 2 of the council on this question: {self.question}\n\n"
                f"Your card: {member.card['decision']}\n  Argument: {member.card['argument']}\n\n"
                f"The other members' cards:\n{others}\n\n{_OBJECTION_INSTRUCTIONS}\n\n"
                f"Your angle: {member.angle.id} — {member.angle.brief}")

    # --- the choice -------------------------------------------------------------

    def _choose(self, carded: list[_Member]) -> dict:
        """One call to the smart model, no tools; one more try if the answer is not a valid choice."""
        ids = [m.angle.id for m in carded]
        prompt = self._chooser_prompt(carded)
        problem = ""
        for _attempt in range(2):
            asked = prompt if not problem else (
                f"{prompt}\n\nYour previous answer was rejected: {problem} Answer again with the JSON object.")
            verdict, problem = check_verdict(self._ask_chooser(asked), ids)
            if verdict is not None:
                return verdict
        raise _CouncilError(
            "The cards and objections are in, but no choice came out: the answer was unusable twice in a row "
            f"({problem}) Nothing was chosen.",
            "Карточки и возражения собраны, но выбор не удалось получить: ответ дважды подряд оказался непригоден "
            f"({problem}) Ничего не выбрано.")

    def _ask_chooser(self, user_prompt: str) -> str:
        from kernel.agent.agent_model import _create_json_completion, _emit_usage, safe_extract_completion_content
        from kernel.agent.cancellable_call import call_cancellable

        main = self.main
        model = main.config.smart_model
        messages = [{"role": "system", "content": _CHOOSER_SYSTEM}, {"role": "user", "content": user_prompt}]
        try:
            response = call_cancellable(
                main._cancel_event,
                lambda: _create_json_completion(main, model, messages, stream=False, json_mode=True),
            )
            content = safe_extract_completion_content(response, main.config.provider, model)
        except RunCancelled:
            raise
        except Exception as error:  # noqa: BLE001 - any provider failure ends the council with its text
            detail = redact_text(str(error).strip() or type(error).__name__)
            raise _CouncilError(f"No choice came out: the model did not answer ({detail}). Nothing was chosen.",
                                f"Выбор не получен: модель не ответила ({detail}). Ничего не выбрано.") from error
        # Counted in the main session: this call is the main agent's own spend.
        _emit_usage(main, model=model,
                    input_tokens=main._count_tokens(_CHOOSER_SYSTEM) + main._count_tokens(user_prompt),
                    output_tokens=main._count_tokens(content or ""), kind="council", usage_source="estimated")
        return content or ""

    def _chooser_prompt(self, carded: list[_Member]) -> str:
        cards = "\n".join(f"[{m.angle.id}] decision: {m.card['decision']}\n  argument: {m.card['argument']}"
                          for m in carded)
        objections = "\n".join(f"[{m.angle.id} on {risk['target']}] {risk['risk']}"
                               for m in carded for risk in m.risks or [])
        text = (f"Question:\n{self.question}\n\nCards:\n{cards}\n\n"
                f"Objections:\n{objections or '(none were given)'}")
        # Members read the workspace, so their words are not trusted to carry prompt tags.
        return defang_prompt_boundary_tags(text)

    # --- how it ends --------------------------------------------------------------

    def _decided(self, verdict: dict, carded: list[_Member]) -> TurnResult:
        by_id = {m.angle.id: m for m in carded}
        winner = by_id[verdict["chosen"]]
        decision = winner.card["decision"]
        dissent = [{"executor_id": row["executor_id"], "angle": row["executor_id"], "why": row["why"]}
                   for row in verdict["dissent"]]
        self.main._ui.print_council_verdict({
            "council_id": self.council_id, "chosen": winner.angle.id, "decision": decision,
            "reason": verdict["reason"], "dissent": dissent,
        })
        final = self._render(carded, verdict, winner)
        message = f"council: decision chosen: {decision} / council: выбрано решение: {decision}"
        return TurnResult(True, message, final,
                          effects={"council_id": self.council_id, "chosen": winner.angle.id, "decision": decision})

    def _stopped(self) -> TurnResult:
        english, russian = _TEXT["stopped"]
        return TurnResult(False, f"{english} / {russian}",
                          f"## {_pick(self.language, 'title')}\n\n{_pick(self.language, 'stopped')}",
                          stopped=True, effects={"council_id": self.council_id, "stopped": True})

    def _failed(self, error: _CouncilError) -> TurnResult:
        text = error.russian if self.language == "ru" else error.english
        body = f"## {_pick(self.language, 'title')}\n\n{text}"
        carded = self._carded()
        if carded:
            body += "\n\n" + self._render_record(carded)
        return TurnResult(False, f"{error.english} / {error.russian}", body, effects={"council_id": self.council_id})

    # --- the words ------------------------------------------------------------------

    def _label(self, angle_id: str) -> str:
        return self._label_in(angle_id, self.language)

    @staticmethod
    def _label_in(angle_id: str, language: str) -> str:
        angle = next(a for a in ANGLES if a.id == angle_id)
        return angle.label_ru if language == "ru" else angle.label_en

    def _too_few_cards(self, count: int) -> _CouncilError:
        def reasons(language: str) -> str:
            return "; ".join(f"{self._label_in(m.angle.id, language)}: {m.dropped or _pick(language, 'dropped_card')}"
                             for m in self.members if m.card is None)

        total = len(self.members)
        return _CouncilError(
            f"The council could not meet: {count} of {total} members handed in a usable card, and it needs at "
            f"least {MIN_CARDS}. {reasons('en')}",
            f"council не состоялся: пригодную карточку сдали {count} из {total} участников, нужно не меньше "
            f"{MIN_CARDS}. {reasons('ru')}")

    def _render_record(self, carded: list[_Member]) -> str:
        language = self.language
        lines = [f"### {_pick(language, 'cards')}"]
        for member in carded:
            lines.append(f"- **{self._label(member.angle.id)}**: {member.card['decision']} {member.card['argument']}")
        objections = [(m, risk) for m in carded for risk in m.risks or []]
        if objections:
            lines += ["", f"### {_pick(language, 'objections')}"]
            for member, risk in objections:
                lines.append(f"- **{self._label(member.angle.id)}** {_pick(language, 'to')} "
                             f"**{self._label(risk['target'])}**: {risk['risk']}")
        return "\n".join(lines)

    def _render(self, carded: list[_Member], verdict: dict, winner: _Member) -> str:
        language = self.language
        parts = [f"## {_pick(language, 'title')}", _pick(language, "intro"),
                 f"**{_pick(language, 'question')}.** {self.question}", self._render_record(carded)]
        silent = [self._label(m.angle.id) for m in carded if m.risks is None]
        absent = [self._label(m.angle.id) for m in self.members if m.card is None]
        notes = []
        if absent:
            notes.append(f"{_pick(language, 'dropped')}: {', '.join(absent)} ({_pick(language, 'dropped_card')}).")
        if silent:
            notes.append(f"{', '.join(silent)}: {_pick(language, 'no_objection')}.")
        if notes:
            parts.append("\n".join(notes))
        parts.append(f"### {_pick(language, 'decision')}\n**{winner.card['decision']}** "
                     f"({self._label(winner.angle.id)})\n\n{verdict['reason']}")
        if verdict["dissent"]:
            rows = "\n".join(f"- **{self._label(row['executor_id'])}**: {row['why']}" for row in verdict["dissent"])
            parts.append(f"### {_pick(language, 'against')}\n{rows}")
        else:
            parts.append(_pick(language, "no_one"))
        return "\n\n".join(parts)


def run_council(main: Any, question: str) -> TurnResult:
    """Hold a council on ``question`` as the main agent's turn; returns the turn's result."""
    return _Council(main, question).run()
