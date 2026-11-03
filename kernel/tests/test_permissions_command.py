from __future__ import annotations

import json
from types import SimpleNamespace

from kernel.agent.agent_runtime import _prefetch_approvals
from kernel.config.user_config import get_auto_approval
from kernel.sandbox import permission_command
from kernel.sandbox.auto_approval import Classifier
from kernel.sandbox.permission_policy import load_policy


def verdicts(*items):
    return json.dumps({"decisions": [
        {"id": index, "verdict": verdict, "reason": reason} for index, (verdict, reason) in enumerate(items)
    ]})


def run(agent, line, confirm=lambda title, body: True, ru=False):
    return permission_command.run(agent, line.split(), confirm, ru)


# ── rules ────────────────────────────────────────────────────────────

def test_status_lists_every_rule_list_and_the_auto_state(agent_factory):
    agent = agent_factory()
    run(agent, "allow npm run *")
    run(agent, "deny curl *")

    ok, text = run(agent, "")

    assert ok
    assert "Allow: npm run *" in text and "Deny: curl *" in text
    assert "Outside the sandbox (host): (none)" in text
    assert "Auto mode: off" in text
    assert "/permissions auto" in text


def test_allow_ask_and_deny_are_saved_in_their_lists(agent_factory):
    agent = agent_factory()

    for kind in ("allow", "ask", "deny"):
        ok, text = run(agent, f"{kind} git status")
        assert ok and text == f"Added to {kind}: git status"

    policy, _ = load_policy(agent.executor.workspace)
    assert (policy.allow, policy.ask, policy.deny) == (("git status",), ("git status",), ("git status",))


def test_a_lone_star_is_refused_for_allow_and_host_but_not_for_deny(agent_factory):
    agent = agent_factory()

    for kind in ("allow", "host"):
        ok, text = run(agent, f"{kind} *")
        assert not ok and "must start with the command's name" in text
    assert run(agent, "deny *")[0]

    policy, _ = load_policy(agent.executor.workspace)
    assert (policy.allow, policy.host, policy.deny) == ((), (), ("*",))


def test_host_is_added_only_after_the_person_confirms_and_reads_the_warning(agent_factory):
    agent = agent_factory()
    shown = []

    ok, _ = run(agent, "host git push *", lambda title, body: shown.append(body) or False)
    assert not ok and load_policy(agent.executor.workspace)[0].host == ()

    ok, _ = run(agent, "host git push *", lambda title, body: shown.append(body) or True)
    assert ok and load_policy(agent.executor.workspace)[0].host == ("git push *",)
    assert all("OUTSIDE the sandbox" in body and "git push *" in body for body in shown)


def test_remove_deletes_the_rule_and_says_where_it_was(agent_factory):
    agent = agent_factory()
    run(agent, "allow git status")
    run(agent, "ask git status")

    ok, text = run(agent, "remove git status")

    assert ok and text == "Removed from allow, ask: git status"
    assert run(agent, "remove git status") == (False, "No such rule: git status")


def test_check_explains_a_command_and_so_does_the_old_form(agent_factory):
    agent = agent_factory()

    for line in ("check python script.py", "python script.py"):
        ok, text = run(agent, line)
        assert ok and text.startswith("python script.py → ")


def test_the_answers_come_in_russian_when_asked(agent_factory):
    agent = agent_factory()

    assert run(agent, "allow git status", ru=True)[1] == "Добавлено в allow: git status"
    assert "Автоматический режим: выключен" in run(agent, "", ru=True)[1]


# ── the menu ─────────────────────────────────────────────────────────

class ScriptedUI:
    """Answers choose_option / prompt_text from a script and records what was shown."""

    def __init__(self, choices, texts=()):
        self.choices, self.texts = list(choices), list(texts)
        self.shown: list[dict] = []

    def choose_option(self, title, options, current_value="", help_text=""):
        self.shown.append({"title": title, "options": options, "help": help_text})
        return self.choices.pop(0)

    def prompt_text(self, title, current_value="", help_text=""):
        return self.texts.pop(0)


def test_the_bare_command_opens_the_menu_and_a_choice_adds_the_rule(agent_factory):
    agent = agent_factory()
    ui = ScriptedUI(["allow", "deny", None], ["npm run *", "curl *"])

    ok, text = permission_command.run(agent, [], lambda t, b: True, False, ui=ui)

    policy, _ = load_policy(agent.executor.workspace)
    assert ok and (policy.allow, policy.deny) == (("npm run *",), ("curl *",))
    assert [row["value"] for row in ui.shown[0]["options"]] == ["allow", "host", "ask", "deny", "remove", "check", "auto"]
    assert "Added to allow: npm run *" in ui.shown[1]["help"] and "Allow: npm run *" in ui.shown[1]["help"]
    assert text == "Added to deny: curl *"


def test_cancelling_a_prompt_changes_nothing_and_returns_to_the_menu(agent_factory):
    agent = agent_factory()
    ui = ScriptedUI(["allow", None], [None])

    permission_command.run(agent, [], lambda t, b: True, False, ui=ui)

    assert len(ui.shown) == 2 and load_policy(agent.executor.workspace)[0].allow == ()


def test_removing_a_rule_is_picked_from_the_list_and_only_from_its_own_list(agent_factory):
    agent = agent_factory()
    run(agent, "allow git status")
    run(agent, "ask git status")
    ui = ScriptedUI(["remove", "ask\tgit status", None])

    permission_command.run(agent, [], lambda t, b: True, False, ui=ui)

    policy, _ = load_policy(agent.executor.workspace)
    assert (policy.allow, policy.ask) == (("git status",), ())
    assert {row["description"] for row in ui.shown[1]["options"]} == {"allow", "ask"}


def test_the_menu_turns_auto_mode_on_only_after_the_warning_is_confirmed(agent_factory):
    agent = agent_factory()
    shown = []

    permission_command.run(agent, [], lambda t, b: shown.append(b) or False, False, ui=ScriptedUI(["auto", "on", None]))
    assert agent.auto_approval is False and "NOT safe" in shown[0]

    permission_command.run(agent, [], lambda t, b: True, False, ui=ScriptedUI(["auto", "on", None]))
    assert agent.auto_approval is True


def test_the_menu_speaks_russian_when_asked(agent_factory):
    ui = ScriptedUI([None])

    permission_command.run(agent_factory(), [], lambda t, b: True, True, ui=ui)

    assert ui.shown[0]["title"] == "Разрешения команд"


def test_no_text_of_the_command_carries_markdown(agent_factory):
    agent = agent_factory()
    texts = [permission_command.usage(False), permission_command.usage(True),
             permission_command.state_text(agent, False), run(agent, "allow *")[1],
             run(agent, "allow *", ru=True)[1]]

    assert all("`" not in text and "**" not in text for text in texts)


# ── auto mode ────────────────────────────────────────────────────────

def test_auto_mode_is_not_turned_on_unless_the_person_confirms(agent_factory):
    agent = agent_factory()
    shown = []

    ok, _ = run(agent, "auto on", lambda title, body: shown.append(body) or False)

    assert not ok and agent.auto_approval is False and get_auto_approval() is False
    assert "NOT safe" in shown[0] and "2 minutes" in shown[0]


def test_auto_mode_on_and_off_switch_the_agent_and_the_saved_setting(agent_factory):
    agent = agent_factory()

    ok, text = run(agent, "auto on")
    assert ok and "not safe" in text
    assert agent.auto_approval is True and get_auto_approval() is True

    ok, _ = run(agent, "auto off")
    assert ok and agent.auto_approval is False and get_auto_approval() is False
    assert run(agent, "auto")[1] == "Auto mode: off"


def test_the_warning_is_shown_in_russian_too(agent_factory):
    agent = agent_factory()
    shown = []

    run(agent, "auto on", lambda title, body: shown.append((title, body)) or False, ru=True)

    assert shown[0][0] == "Автоматический режим небезопасен" and "НЕБЕЗОПАСЕН" in shown[0][1]


def test_the_real_classifier_reads_the_fast_model_and_answers_for_the_person(agent_factory):
    agent = agent_factory()
    agent.client.content = verdicts(("allow", "a plain test run"))
    agent.set_auto_approval(True)

    assert agent.executor.approvals.confirm_command(["python", "t.py"], "moderate") is True

    request = agent.client.requests[-1]
    assert request["model"] == agent.config.fast_model
    assert "permission classifier" in request["messages"][0]["content"]


def test_decisions_go_to_the_run_state_for_the_log_and_never_to_the_screen(agent_factory):
    from kernel.agent.agent_state import RunState

    agent = agent_factory()
    shown = []
    agent._ui.print_result = shown.append
    agent.client.content = verdicts(("allow", "a plain test run"))
    agent.set_auto_approval(True)
    agent._state = RunState()  # a new run replaces the state the gate was set up with

    agent.executor.approvals.confirm_command(["python", "t.py"], "moderate")

    assert agent._state.auto_decisions == [
        {"kind": "command", "decision": "allow", "subject": "python t.py", "reason": "a plain test run"}
    ]
    assert shown == []


def test_capability_questions_go_to_the_classifier_in_auto_mode_and_to_the_person_otherwise(agent_factory):
    agent = agent_factory()
    asked = []
    agent._ui.confirm = lambda title, body: asked.append(title) or False
    assert agent.confirm_capability("Skill x", "wants the network") is False
    assert asked == ["Skill x"]

    agent.executor.set_auto_approval(Classifier(lambda messages: verdicts(("allow", "ok")), lambda: "do x"))

    assert agent.confirm_capability("Skill x", "wants the network") is True
    assert asked == ["Skill x"]


# ── the batch ────────────────────────────────────────────────────────

def planned(*commands):
    return [
        SimpleNamespace(command=SimpleNamespace(action=SimpleNamespace(type="run_shell", args={"command": argv})))
        for argv in commands
    ]


def test_commands_that_ask_are_judged_together_and_the_rest_are_left_out(agent_factory):
    agent = agent_factory()
    calls = []

    def ask(messages):
        calls.append(messages)
        block = messages[1]["content"].split("source=\"approval_request\">\n")[1].split("\n</UNTRUSTED")[0]
        subjects = [item["subject"] for item in json.loads(block)]
        return verdicts(*[("deny", "no") if "b.py" in subject else ("allow", "ok") for subject in subjects])

    gate = agent.executor.set_auto_approval(Classifier(ask, lambda: "do x"))

    _prefetch_approvals(agent, planned(["python", "a.py"], ["ls"], ["python", "b.py"]))

    assert gate.confirm_command(["python", "a.py"], "x") is True
    assert gate.confirm_command(["python", "b.py"], "x") is False
    assert len(calls) == 1  # a.py and b.py together; "ls" asks nobody
    block = calls[0][1]["content"].split('source="approval_request">\n')[1].split("\n</UNTRUSTED")[0]
    assert {item["subject"].splitlines()[0] for item in json.loads(block)} == {"python a.py", "python b.py"}


def test_nothing_is_prefetched_while_auto_mode_is_off(agent_factory):
    agent = agent_factory()

    _prefetch_approvals(agent, planned(["python", "a.py"], ["python", "b.py"]))

    assert agent.client.requests == []
