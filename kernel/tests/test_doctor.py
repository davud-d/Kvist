# -*- coding: utf-8 -*-
from __future__ import annotations

from types import SimpleNamespace

from kernel.agent.agent_state import AgentConfig
from kernel.config import doctor
from kernel.config.doctor import FAIL, INFO, OK, WARN, Check
from kernel.tests.conftest import FakeLLMClient


def _caller(content: str = "pong") -> tuple[SimpleNamespace, str, FakeLLMClient]:
    client = FakeLLMClient(content=content)
    config = AgentConfig(provider="deepseek", persist_logs=False, stream=False)
    caller = SimpleNamespace(client=client, config=config, _ui=SimpleNamespace(print_error=print, debug_print=print))
    return caller, "deepseek-flash", client


def test_a_pong_is_ok_and_the_question_names_provider_and_model():
    caller, model, client = _caller("Pong.")
    asked: list[tuple[str, str]] = []

    def ask(title: str, body: str) -> bool:
        asked.append((title, body))
        return True

    check = doctor.probe_model(lambda: (caller, model), ask, ru=False)

    assert check.status == OK
    assert "deepseek / deepseek-flash" in asked[0][1]
    assert len(client.requests) == 1


def test_nothing_is_sent_without_permission():
    caller, model, client = _caller()

    check = doctor.probe_model(lambda: (caller, model), lambda _t, _b: False, ru=True)

    assert check.status == INFO
    assert client.requests == []


def test_another_answer_is_a_warning_not_a_failure():
    caller, model, _client = _caller("hello there")

    check = doctor.probe_model(lambda: (caller, model), lambda _t, _b: True, ru=False)

    assert check.status == WARN
    assert "hello there" in check.detail


def test_a_provider_error_is_reported_with_the_check_failing():
    caller, model, client = _caller()

    def refuse(**_kwargs):
        raise RuntimeError("401 invalid api key")

    client.chat.completions.create = refuse

    check = doctor.probe_model(lambda: (caller, model), lambda _t, _b: True, ru=False)

    assert check.status == FAIL


def test_a_client_that_cannot_be_built_fails_before_asking():
    def broken():
        raise RuntimeError("DeepSeek API key is required.")

    def ask(_title, _body):
        raise AssertionError("asked about a request that cannot be made")

    check = doctor.probe_model(broken, ask, ru=False)

    assert check.status == FAIL
    assert "required" in check.detail


def test_an_empty_install_reports_the_missing_provider(tmp_path):
    checks = doctor.diagnose(tmp_path, ru=False)

    assert doctor.has_failures(checks)
    assert not doctor.provider_ready(checks, ru=False)
    assert all(check.status == OK for check in checks if check.group == "Modules")


def test_the_report_groups_checks_and_counts_problems():
    checks = [
        Check("One", "a", OK, "fine"),
        Check("One", "b", WARN),
        Check("Two", "c", FAIL, "broken"),
    ]

    text = doctor.render(checks, ru=False)

    assert text.splitlines()[0] == "One"
    assert "✗ c — broken" in text
    assert text.endswith("1 failed, 1 warnings, 3 checks")


def test_the_report_speaks_russian_on_request(tmp_path):
    checks = doctor.diagnose(tmp_path, ru=True)

    assert "Провайдер" in doctor.render(checks, ru=True)
