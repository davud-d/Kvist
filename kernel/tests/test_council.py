# -*- coding: utf-8 -*-
"""Council mode: four read-only members, one round of cards, one of objections, one chosen decision.

No network: every member and the chooser talk to ``CouncilClient``, which answers by who is asking (the angle on
the last line of the goal) and in which round, and records every request.
"""
from __future__ import annotations

import json
import re
import threading
import time
from types import SimpleNamespace

import pytest

from kernel.bestof import council
from kernel.bestof.council import (
    ANGLES,
    check_verdict,
    member_goal,
    parse_card,
    parse_risks,
    run_council,
)
from kernel.session.agent_logging import read_session_document
from kernel.tests.conftest import NullUI

QUESTION = "Which database should the app use for its job queue?"
IDS = [angle.id for angle in ANGLES]
DECISIONS = {
    "simplicity": "Keep the queue in SQLite",
    "security": "Use Postgres with row-level security",
    "speed": "Buy a hosted queue service",
    "maintenance": "Keep SQLite and add Litestream backups",
}
ARGUMENTS = {angle: f"The {angle} argument stands on files nobody else read." for angle in IDS}
CHOOSER_MARK = "you close a council"


@pytest.fixture(autouse=True)
def _text_protocol(monkeypatch):
    # The native-tools probe would spend a scripted reply on the tool-calling check.
    monkeypatch.setenv("AGENT_NATIVE_TOOLS", "0")


def card_text(angle: str) -> str:
    prose = f"SECRET-{angle}-REASONING: I looked at the code and weighed it.\n"
    return prose + json.dumps({"decision": DECISIONS[angle], "argument": ARGUMENTS[angle]})


def risks_text(angle: str) -> str:
    risks = [{"target": other, "risk": f"RISK-{angle}-ON-{other}"} for other in IDS if other != angle]
    return "My objections:\n" + json.dumps({"risks": risks})


def verdict_text(chosen: str = "security", dissent: list | None = None, reason: str = "It survives its risks.") -> str:
    rows = [{"executor_id": "speed", "why": "Hosting adds a vendor."}] if dissent is None else dissent
    return json.dumps({"chosen": chosen, "reason": reason, "dissent": rows})


class Request:
    def __init__(self, who: str, phase: str, kwargs: dict, text: str) -> None:
        self.who, self.phase, self.kwargs, self.text = who, phase, kwargs, text


_PHASES = (("risks", "Round 2 of the council"), ("card-retry", "no usable JSON card"),
           ("risks-retry", "no usable JSON object"))


def classify(system: str, text: str) -> tuple[str, str]:
    """Who is asking and in which round: the marker that comes last in the prompt is the current request.

    Without any marker it is a first-round request, whose only mark is the angle line.
    """
    if CHOOSER_MARK in system:
        return "chooser", "verdict"
    marked = max(_PHASES, key=lambda item: text.rfind(item[1]))
    phase = marked[0] if text.rfind(marked[1]) != -1 else "card"
    angles = re.findall(r"Your angle: (\w+)", text)
    return (angles[-1] if angles else "?"), phase


class CouncilClient:
    """Replies from ``script[(who, phase)]`` (a list, used in order); ``hold[who]`` blocks that member's card."""

    def __init__(self, script: dict | None = None, hold: dict | None = None) -> None:
        defaults: dict = {(angle, "card"): [card_text(angle)] for angle in IDS}
        defaults.update({(angle, "risks"): [risks_text(angle)] for angle in IDS})
        defaults[("chooser", "verdict")] = [verdict_text()]
        defaults.update(script or {})
        self.script = {key: list(value) for key, value in defaults.items()}
        self.hold = hold or {}
        self.requests: list[Request] = []
        self._lock = threading.Lock()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs):
        messages = kwargs["messages"]
        system = str(messages[0]["content"])
        text = "\n".join(str(message["content"]) for message in messages[1:])
        who, phase = classify(system, text)
        with self._lock:
            self.requests.append(Request(who, phase, kwargs, text))
            replies = self.script.get((who, phase), [])
            reply = replies.pop(0) if len(replies) > 1 else (replies[0] if replies else "")
        if phase == "card" and who in self.hold:
            self.hold[who].wait(10)
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=None)

    def of(self, phase: str) -> list[Request]:
        with self._lock:
            return [request for request in self.requests if request.phase == phase]


class Recorder(NullUI):
    """The main interface: keeps the council events in the order they were shown."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def _note(self, name: str, payload: dict) -> None:
        self.events.append((name, payload))

    def print_council_started(self, payload):
        self._note("started", payload)

    def print_council_card(self, payload):
        self._note("card", payload)

    def print_council_objection(self, payload):
        self._note("objection", payload)

    def print_council_verdict(self, payload):
        self._note("verdict", payload)

    def kinds(self) -> list[str]:
        return [name for name, _payload in self.events]

    def payloads(self, kind: str) -> list[dict]:
        return [payload for name, payload in self.events if name == kind]


def make_main(agent_factory, client, language: str = "en"):
    main = agent_factory(max_steps=8, stream=False)
    main.client = client
    recorder = Recorder()
    object.__setattr__(main._ui, "_ui", recorder)
    main._state.response_language = language
    main.control.begin()
    return main, recorder


def wait_until(predicate, seconds: float = 5.0) -> None:
    deadline = time.monotonic() + seconds
    while not predicate():
        assert time.monotonic() < deadline, "condition never became true"
        time.sleep(0.01)


def hold_all() -> dict:
    return {angle: threading.Event() for angle in IDS}


# --- the whole council -------------------------------------------------------------


def test_cards_then_objections_then_one_verdict_with_the_decision_copied_from_the_card(agent_factory):
    client = CouncilClient()
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    kinds = recorder.kinds()
    assert kinds[0] == "started" and kinds[-1] == "verdict"
    assert kinds[1:5] == ["card"] * 4 and kinds[5:9] == ["objection"] * 4 and len(kinds) == 10
    verdict = recorder.payloads("verdict")[0]
    chosen_card = next(card for card in recorder.payloads("card") if card["executor_id"] == verdict["chosen"])
    assert verdict["decision"].encode() == chosen_card["decision"].encode() == DECISIONS["security"].encode()
    assert verdict["reason"] == "It survives its risks."
    assert verdict["dissent"] == [{"executor_id": "speed", "angle": "speed", "why": "Hosting adds a vendor."}]
    assert result.ok and result.effects == {
        "council_id": verdict["council_id"], "chosen": "security", "decision": DECISIONS["security"]}
    assert DECISIONS["security"] in result.message


def test_every_event_names_the_angle_by_its_id_which_is_the_executor_id(agent_factory):
    main, recorder = make_main(agent_factory, CouncilClient())

    run_council(main, QUESTION)

    started = recorder.payloads("started")[0]
    assert started["question"] == QUESTION
    assert [member["executor_id"] for member in started["members"]] == IDS
    assert all(member["angle"] == member["executor_id"] for member in started["members"])
    for kind in ("card", "objection"):
        assert sorted(payload["executor_id"] for payload in recorder.payloads(kind)) == sorted(IDS)
        assert all(payload["angle"] == payload["executor_id"] for payload in recorder.payloads(kind))
    ids = {payload["council_id"] for _name, payload in recorder.events}
    assert ids == {started["council_id"]}


def test_the_answer_text_has_the_question_every_card_the_objections_the_choice_and_the_dissent(agent_factory):
    main, _recorder = make_main(agent_factory, CouncilClient())

    result = run_council(main, QUESTION)

    final = result.final
    assert final.startswith("## council\n\nFour members looked at the question")
    assert QUESTION in final
    for angle in IDS:
        assert DECISIONS[angle] in final and ARGUMENTS[angle] in final
    assert "RISK-security-ON-speed" in final
    assert f"**{DECISIONS['security']}**" in final and "It survives its risks." in final
    assert "Hosting adds a vendor." in final
    assert "judge" not in final.lower()


def test_the_answer_is_in_the_language_of_the_question(agent_factory):
    main, _recorder = make_main(agent_factory, CouncilClient(), language="ru")

    result = run_council(main, "Какую базу взять для очереди задач?")

    assert result.final.startswith("## council\n\nЧетыре участника посмотрели на вопрос")
    assert "Совет" not in result.final
    assert "Возражения" in result.final and "Решение" in result.final and "Остались против" in result.final
    assert "судь" not in result.final.lower()
    assert " / " in result.message and "council: выбрано решение" in result.message
    assert "Совет" not in result.message


def test_the_chosen_member_with_nobody_against_says_so(agent_factory):
    client = CouncilClient({("chooser", "verdict"): [verdict_text("simplicity", dissent=[])]})
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    assert recorder.payloads("verdict")[0]["dissent"] == []
    assert "Nobody objects to the choice." in result.final


# --- who sees what --------------------------------------------------------------------


def test_in_round_one_a_member_sees_the_question_and_its_angle_but_no_other_card(agent_factory):
    client = CouncilClient()
    main, _recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    first_round = client.of("card")
    assert len(first_round) == 4
    for request in first_round:
        assert QUESTION in request.text
        for other in IDS:
            if other != request.who:
                assert DECISIONS[other] not in request.text and ARGUMENTS[other] not in request.text
                assert f"SECRET-{other}" not in request.text


def test_in_round_two_a_member_gets_only_the_cards_of_the_others_not_their_reasoning(agent_factory):
    client = CouncilClient()
    main, _recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    second_round = client.of("risks")
    assert sorted(request.who for request in second_round) == sorted(IDS)
    for request in second_round:
        for other in IDS:
            if other == request.who:
                continue
            assert DECISIONS[other] in request.text and ARGUMENTS[other] in request.text
            assert f"SECRET-{other}" not in request.text


def test_the_chooser_sees_cards_and_objections_and_is_not_a_member(agent_factory):
    client = CouncilClient()
    main, _recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    (chooser,) = client.of("verdict")
    assert chooser.kwargs["model"] == main.config.smart_model
    assert "tools" not in chooser.kwargs
    for angle in IDS:
        assert DECISIONS[angle] in chooser.text and f"RISK-{angle}-ON-" in chooser.text
    assert "SECRET-" not in chooser.text


# --- what the members have in common ------------------------------------------------------


def test_the_goals_differ_only_in_the_angle_line_at_the_end():
    goals = [member_goal(QUESTION, angle) for angle in ANGLES]

    head = goals[0][:goals[0].rindex("Your angle:")]
    assert head.startswith(QUESTION)
    assert all(goal.startswith(head) for goal in goals)
    tails = [goal[len(head):] for goal in goals]
    assert len(set(tails)) == 4 and all("\n" not in tail for tail in tails)
    assert [tail.split()[2] for tail in tails] == IDS


def test_members_share_the_system_prompt_the_request_options_and_everything_before_the_angle(agent_factory):
    client = CouncilClient()
    main, _recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    first_requests = {request.who: request for request in reversed(client.of("card"))}
    assert sorted(first_requests) == sorted(IDS)
    reference = first_requests["simplicity"]
    cut = reference.kwargs["messages"][1]["content"].rindex("Your angle:")
    for request in first_requests.values():
        assert request.kwargs["messages"][0] == reference.kwargs["messages"][0]
        assert {k: v for k, v in request.kwargs.items() if k != "messages"} == \
               {k: v for k, v in reference.kwargs.items() if k != "messages"}
        user = request.kwargs["messages"][1]["content"]
        assert user[:cut] == reference.kwargs["messages"][1]["content"][:cut]


def test_members_are_read_only_readers_in_child_sessions_of_the_main_one(agent_factory):
    client = CouncilClient({("simplicity", "card"): [
        '<tool_call>{"name": "write_file", "arguments": {"path": "notes.txt", "content": "x"}}</tool_call>',
        card_text("simplicity")]})
    main, recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    assert not (main.executor.workspace / "notes.txt").exists()
    assert "read-only inspection" in str(client.of("card")[0].kwargs["messages"][0]["content"])
    started = recorder.payloads("started")[0]
    for member in started["members"]:
        document = read_session_document(main, member["session"])
        assert document["service"] == {
            "kind": "council_member", "parent_session": main.current_session, "council_id": started["council_id"],
            "executor_id": member["executor_id"], "angle": member["executor_id"]}
        assert document["usage_totals"]["calls"] >= 2
    assert main.current_session not in {member["session"] for member in started["members"]}


def test_the_choice_is_counted_in_the_main_session_and_the_members_in_theirs(agent_factory):
    main, _recorder = make_main(agent_factory, CouncilClient())
    assert main._usage_totals.calls == 0

    run_council(main, QUESTION)

    assert main._usage_totals.calls == 1


# --- the first member goes first --------------------------------------------------------


def test_the_other_members_ask_the_model_only_after_the_first_one_was_answered(agent_factory):
    client = CouncilClient(hold={"simplicity": threading.Event()})
    main, _recorder = make_main(agent_factory, client)
    done: list = []
    thread = threading.Thread(target=lambda: done.append(run_council(main, QUESTION)), daemon=True)
    thread.start()

    wait_until(lambda: len(client.requests) >= 1)
    time.sleep(0.4)
    assert [request.who for request in client.requests] == ["simplicity"]

    client.hold["simplicity"].set()
    thread.join(20)
    assert done and done[0].ok
    assert client.of("card")[0].who == "simplicity" and len(client.of("card")) == 4


class LazyStreamClient(CouncilClient):
    """Like the providers whose ``create(stream=True)`` is a generator: nothing is sent before the first step."""

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.created: list[str] = []

    def create(self, **kwargs):
        if not kwargs.get("stream"):
            return super().create(**kwargs)
        messages = kwargs["messages"]
        who, _phase = classify(str(messages[0]["content"]), "\n".join(str(m["content"]) for m in messages[1:]))
        with self._lock:
            self.created.append(who)

        def chunks():
            # Sent only now, as the first step of the iteration.
            reply = super(LazyStreamClient, self).create(**{**kwargs, "stream": False})
            text = reply.choices[0].message.content
            yield SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text))])

        return chunks()


def test_the_others_wait_for_the_first_chunk_of_a_lazy_stream_not_for_create_to_return(agent_factory):
    client = LazyStreamClient(hold={"simplicity": threading.Event()})
    main, _recorder = make_main(agent_factory, client)
    main.config.stream = True
    done: list = []
    thread = threading.Thread(target=lambda: done.append(run_council(main, QUESTION)), daemon=True)
    thread.start()

    wait_until(lambda: len(client.created) >= 1)
    time.sleep(0.4)
    assert client.created == ["simplicity"]

    client.hold["simplicity"].set()
    thread.join(20)
    assert done and done[0].ok
    assert sorted(set(client.created)) == sorted(IDS) and client.created[0] == "simplicity"


def test_the_others_do_not_wait_longer_than_the_timeout_for_the_first(agent_factory, monkeypatch):
    monkeypatch.setattr(council, "FIRST_ANSWER_TIMEOUT", 0.3)
    client = CouncilClient(hold={"simplicity": threading.Event()})
    main, _recorder = make_main(agent_factory, client)
    done: list = []
    thread = threading.Thread(target=lambda: done.append(run_council(main, QUESTION)), daemon=True)
    thread.start()

    wait_until(lambda: {request.who for request in client.requests} >= {"security", "speed", "maintenance"})
    assert not done
    client.hold["simplicity"].set()
    thread.join(20)
    assert done and done[0].ok


# --- members that fail ----------------------------------------------------------------------


def test_an_unusable_card_is_asked_for_once_more_and_then_the_member_leaves(agent_factory):
    client = CouncilClient({("security", "card"): ["I think Postgres."], ("security", "card-retry"): ["Just prose."],
                            ("chooser", "verdict"): [verdict_text("simplicity", [])]})
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    assert sorted(card["executor_id"] for card in recorder.payloads("card")) == sorted(set(IDS) - {"security"})
    assert [request.who for request in client.of("card-retry")] == ["security"]
    objections = recorder.payloads("objection")
    assert sorted(item["executor_id"] for item in objections) == sorted(set(IDS) - {"security"})
    assert all(risk["target"] != "security" for item in objections for risk in item["risks"])
    assert result.ok and "Left the council: Security (no usable card)." in result.final
    assert recorder.payloads("verdict")[0]["chosen"] != "security"


def test_a_retry_that_gives_a_card_keeps_the_member(agent_factory):
    client = CouncilClient({("speed", "card"): ["Not JSON."], ("speed", "card-retry"): [card_text("speed")]})
    main, recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    assert sorted(card["executor_id"] for card in recorder.payloads("card")) == sorted(IDS)


def test_the_first_json_object_that_is_a_card_counts_even_after_other_json(agent_factory):
    quoted = '{"unrelated": 1}\n' + card_text("speed")
    client = CouncilClient({("speed", "card"): [quoted]})
    main, recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    assert {card["executor_id"]: card["decision"] for card in recorder.payloads("card")}["speed"] == DECISIONS["speed"]


def test_fewer_than_two_cards_end_the_council_with_a_plain_error(agent_factory):
    script = {(angle, phase): ["no card"] for angle in IDS if angle != "simplicity" for phase in ("card", "card-retry")}
    client = CouncilClient(script)
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    assert not result.ok and not result.stopped
    assert "1 of 4 members handed in a usable card" in result.final and "needs at least 2" in result.message
    assert "council не состоялся" in result.message and " / " in result.message
    assert "objection" not in recorder.kinds() and "verdict" not in recorder.kinds()
    assert client.of("verdict") == []


def test_a_member_whose_run_crashes_leaves_without_stopping_the_others(agent_factory, monkeypatch):
    from kernel.agent.agent import LocalAgent

    real_run = LocalAgent.run

    def run(self, user_goal, routed_role=None, **kwargs):
        if (self.service_session or {}).get("executor_id") == "speed":
            raise RuntimeError("provider exploded")
        return real_run(self, user_goal, routed_role, **kwargs)

    monkeypatch.setattr(LocalAgent, "run", run)
    main, recorder = make_main(agent_factory, CouncilClient({("chooser", "verdict"): [verdict_text("security", [])]}))

    result = run_council(main, QUESTION)

    assert result.ok
    assert "speed" not in {card["executor_id"] for card in recorder.payloads("card")}
    assert "Left the council: Development speed" in result.final


def test_a_member_that_gives_no_objection_is_named_and_the_council_goes_on(agent_factory):
    client = CouncilClient({("maintenance", "risks"): ["No idea."], ("maintenance", "risks-retry"): ["Nothing."]})
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    assert "maintenance" not in {item["executor_id"] for item in recorder.payloads("objection")}
    assert len(recorder.payloads("objection")) == 3
    assert "Maintenance cost: gave no usable objections." in result.final
    assert result.ok


def test_risks_aimed_at_nobody_or_at_oneself_are_dropped_and_one_risk_per_target_stays(agent_factory):
    risks = {"risks": [{"target": "simplicity", "risk": "mine"}, {"target": "speed", "risk": "first"},
                       {"target": "speed", "risk": "second"}, {"target": "ghost", "risk": "boo"},
                       {"target": "security", "risk": "  "}]}
    client = CouncilClient({("simplicity", "risks"): [json.dumps(risks)]})
    main, recorder = make_main(agent_factory, client)

    run_council(main, QUESTION)

    mine = next(item for item in recorder.payloads("objection") if item["executor_id"] == "simplicity")
    assert mine["risks"] == [{"target": "speed", "risk": "first"}]


# --- the choice ------------------------------------------------------------------------------


@pytest.mark.parametrize("bad", [
    verdict_text("nobody"),
    verdict_text("security and speed combined"),
    verdict_text(""),
    verdict_text("security", dissent=[{"executor_id": "security", "why": "against myself"}]),
    verdict_text("security", dissent=[{"executor_id": "ghost", "why": "who"}]),
    verdict_text("security", dissent=[{"executor_id": "speed", "why": ""}]),
    verdict_text("security", dissent=[{"executor_id": "speed", "why": "a"}, {"executor_id": "speed", "why": "b"}]),
    verdict_text("security", reason=" "),
    "I choose security.",
])
def test_an_invalid_choice_is_asked_for_once_more_and_then_the_council_fails(agent_factory, bad):
    client = CouncilClient({("chooser", "verdict"): [bad, bad]})
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    assert len(client.of("verdict")) == 2
    assert "rejected" in client.of("verdict")[1].text
    assert not result.ok and "Nothing was chosen." in result.final
    assert "Ничего не выбрано." in result.message
    assert "verdict" not in recorder.kinds()
    # The record stays readable: the cards and objections the council did produce.
    assert DECISIONS["security"] in result.final and "RISK-speed-ON-security" in result.final


def test_a_valid_choice_on_the_second_try_stands(agent_factory):
    client = CouncilClient({("chooser", "verdict"): [verdict_text("speed and security"), verdict_text("speed", [])]})
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    assert result.ok and recorder.payloads("verdict")[0]["chosen"] == "speed"
    assert main._usage_totals.calls == 2


def test_a_member_without_a_card_cannot_be_chosen(agent_factory):
    bad = verdict_text("security")
    script = {("security", "card"): ["prose"], ("security", "card-retry"): ["prose"],
              ("chooser", "verdict"): [bad, bad]}
    main, recorder = make_main(agent_factory, CouncilClient(script))

    result = run_council(main, QUESTION)

    assert not result.ok and "verdict" not in recorder.kinds()


def test_a_failing_chooser_call_ends_the_council_with_its_error(agent_factory):
    client = CouncilClient()

    def create(**kwargs):
        if CHOOSER_MARK in str(kwargs["messages"][0]["content"]):
            raise ConnectionError("connection reset")
        return CouncilClient.create(client, **kwargs)

    client.chat = SimpleNamespace(completions=SimpleNamespace(create=create))
    main, recorder = make_main(agent_factory, client)

    result = run_council(main, QUESTION)

    assert not result.ok and "connection reset" in result.final and "Nothing was chosen." in result.final
    assert "verdict" not in recorder.kinds()


# --- stopping ------------------------------------------------------------------------------------


def test_cancel_during_round_one_stops_every_member_and_ends_without_a_verdict(agent_factory):
    hold = hold_all()
    client = CouncilClient(hold=hold)
    main, recorder = make_main(agent_factory, client)
    done: list = []
    thread = threading.Thread(target=lambda: done.append(run_council(main, QUESTION)), daemon=True)
    thread.start()
    try:
        wait_until(lambda: len(client.requests) >= 1)
        main.request_cancel()
        thread.join(10)
        assert not thread.is_alive()
    finally:
        for event in hold.values():
            event.set()

    (result,) = done
    assert result.stopped and not result.ok
    assert "stopped" in result.final
    assert "verdict" not in recorder.kinds() and "objection" not in recorder.kinds()
    assert client.of("verdict") == []


def test_a_pause_stops_the_council_too_because_it_has_nothing_to_wait_with(agent_factory):
    hold = hold_all()
    client = CouncilClient(hold=hold)
    main, recorder = make_main(agent_factory, client)
    done: list = []
    thread = threading.Thread(target=lambda: done.append(run_council(main, QUESTION)), daemon=True)
    thread.start()
    try:
        wait_until(lambda: len(client.requests) >= 1)
        assert main.request_pause()
        thread.join(10)
        assert not thread.is_alive()
    finally:
        for event in hold.values():
            event.set()

    assert done[0].stopped and "verdict" not in recorder.kinds()


def test_cancel_while_all_four_members_wait_stops_them_all(agent_factory, monkeypatch):
    monkeypatch.setattr(council, "FIRST_ANSWER_TIMEOUT", 0.2)
    hold = hold_all()
    client = CouncilClient(hold=hold)
    main, recorder = make_main(agent_factory, client)
    done: list = []
    thread = threading.Thread(target=lambda: done.append(run_council(main, QUESTION)), daemon=True)
    thread.start()
    try:
        wait_until(lambda: len(client.requests) >= 4)
        main.request_cancel()
        thread.join(10)
        assert not thread.is_alive()
    finally:
        for event in hold.values():
            event.set()

    assert done[0].stopped
    assert "verdict" not in recorder.kinds()


# --- reading the answers ----------------------------------------------------------------------------


def test_a_card_is_found_in_prose_or_a_code_fence():
    assert parse_card('Here:\n```json\n{"decision": " A ", "argument": "B"}\n```\nDone.') == \
           {"decision": "A", "argument": "B"}
    assert parse_card('{"decision": "A"}') is None
    assert parse_card('{"decision": "", "argument": "B"}') is None
    assert parse_card('{"decision": 3, "argument": "B"}') is None
    assert parse_card("no json { at all") is None


def test_risks_need_a_known_target_and_a_text():
    text = json.dumps({"risks": [{"target": "a", "risk": "x"}, {"target": "z", "risk": "y"}, "junk"]})
    assert parse_risks(text, ["a", "b"]) == [{"target": "a", "risk": "x"}]
    assert parse_risks(json.dumps({"risks": []}), ["a"]) is None
    assert parse_risks(json.dumps({"risks": "x"}), ["a"]) is None


def test_a_verdict_names_a_member_with_a_card_and_other_members_as_dissent():
    verdict, problem = check_verdict(verdict_text("security"), IDS)
    assert verdict == {"chosen": "security", "reason": "It survives its risks.",
                       "dissent": [{"executor_id": "speed", "why": "Hosting adds a vendor."}]} and problem == ""
    assert check_verdict(verdict_text("security"), ["simplicity", "speed"])[0] is None
    assert check_verdict("{}", IDS)[0] is None
