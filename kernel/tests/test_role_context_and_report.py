# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from kernel.agent_runtime import _is_report_goal


# ── BUG-1: read content survives into session history ───────────────────────

def test_harvest_run_outputs_captures_read_file_content(agent_factory):
    agent = agent_factory()
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_file", "args": {"path": "hypr.conf"}}},
            "feedback": {"ok": True, "data": {"path": "hypr.conf", "content": "bind = SUPER, Q, killactive"}},
        }
    )

    outputs = agent._harvest_run_outputs()

    joined = "\n".join(outputs)
    assert "hypr.conf" in joined
    assert "killactive" in joined


def test_harvest_run_outputs_captures_search_and_multi_read(agent_factory):
    agent = agent_factory()
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_multiple_files", "args": {}}},
            "feedback": {
                "ok": True,
                "data": {"files": [{"path": "a.conf", "content": "monitor=DP-1"}]},
            },
        }
    )
    agent._state.step_logs.append(
        {
            "step": 2,
            "status": "ok",
            "command": {"action": {"type": "search_code", "args": {}}},
            "feedback": {
                "ok": True,
                "data": {"query": "monitor", "results": [{"path": "a.conf", "line": 3, "text": "monitor=DP-1"}]},
            },
        }
    )

    joined = "\n".join(agent._harvest_run_outputs())

    assert "a.conf" in joined
    assert "monitor=DP-1" in joined


def test_harvest_marks_truncated_multi_read(agent_factory):
    """More files than the per-step cap must leave an explicit marker naming the
    hidden files — a silent drop would reintroduce BUG-1 in a new form."""
    from kernel.agent import _SESSION_MULTI_READ_MAX_FILES

    agent = agent_factory()
    total = _SESSION_MULTI_READ_MAX_FILES + 2
    files = [{"path": f"f{i}.conf", "content": f"content-{i}"} for i in range(total)]
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_multiple_files", "args": {}}},
            "feedback": {"ok": True, "data": {"files": files}},
        }
    )

    joined = "\n".join(agent._harvest_run_outputs())

    assert "more file(s)" in joined
    assert f"f{total - 1}.conf" in joined


def test_chat_history_preamble_allows_admitting_absence():
    from kernel.agent import _CHAT_HISTORY_PREAMBLE

    low = _CHAT_HISTORY_PREAMBLE.lower()
    # Positive availability stays; the dangerous gag ("do not say it's missing")
    # is gone; honesty on a harvest miss is explicit.
    assert "available to you" in low
    assert "do not say" not in low
    assert "say so honestly" in low
    assert "inventing it" in low


def test_add_to_session_history_stores_read_content_for_next_role(agent_factory):
    """A coder run that reads a file must leave its content in session history,
    so a later chat turn can answer about it instead of reporting empty context."""
    agent = agent_factory()
    agent._state.step_logs.append(
        {
            "step": 1,
            "status": "ok",
            "command": {"action": {"type": "read_file", "args": {"path": "hypr.conf"}}},
            "feedback": {"ok": True, "data": {"path": "hypr.conf", "content": "gaps_in = 5"}},
        }
    )

    agent._add_to_session_history("прочитай hypr.conf", "coder", "done")

    entry = agent._session_history[-1]
    assert "gaps_in = 5" in "\n".join(entry.get("outputs", []))


# ── The previous answer reaches the next turn whole ─────────────────────────

_FINDING = "Нашёл мусор: в requirements-dev.txt после pyinstaller идёт строка «Существует KVIST.md»."
_LONG_READER_ANSWER = (
    "Разобрался — это сам Kvist.\n\n" + "- dependency line\n" * 400
    + _FINDING + "\n\n" + "- more dependency lines\n" * 400
)


def test_previous_answer_reaches_the_next_role_whole(agent_factory):
    """A reader found junk mid-answer, the user said "yes, delete it", and the
    coder denied ever seeing it: the answer was stored as its first 200
    characters and the action prompt did not render it at all."""
    from kernel.agent_context import render_session_history

    agent = agent_factory()
    agent._add_to_session_history("что за проект?", "reader", _LONG_READER_ANSWER)

    assert agent._session_history[-1]["result"] == _LONG_READER_ANSWER
    assert _FINDING in agent._build_runtime_prompt("да, удаляй", 1)
    assert _FINDING in render_session_history(agent._session_history, include_outputs=False)


def test_chat_turn_sees_the_previous_agent_answer(agent_factory, monkeypatch):
    from kernel.tests.test_agent import _chat_agent
    from kernel.tests.conftest import FakeLLMClient

    agent = _chat_agent(agent_factory, monkeypatch, stream=False)
    agent.client = FakeLLMClient(content="ok")
    agent._session_history = [
        {"goal": "что за проект?", "role": "reader", "result": _LONG_READER_ANSWER}
    ]

    agent._run_chat("а что за мусор ты нашёл?")

    assert _FINDING in agent.client.requests[0]["messages"][-1]["content"]


def test_budget_drops_the_oldest_turns_first(agent_factory):
    agent = agent_factory(max_context_tokens=1_500)
    agent._session_history = [
        {"goal": "old task", "role": "coder",
         "result": "old answer line\n" * 3_000 + "OLD_TAIL"},
        {"goal": "new task", "role": "reader", "result": "NEWEST_FINDING"},
    ]

    trimmed = agent._trim_prompt_to_token_limit("system", agent._build_runtime_prompt("g", 1))

    assert "NEWEST_FINDING" in trimmed
    assert "OLD_TAIL" not in trimmed


def test_answer_lines_never_become_prompt_sections():
    from kernel.agent_context import render_session_history, split_prompt_into_sections

    block = render_session_history(
        [{"goal": "g", "role": "coder", "result": "User task:\nPlan:\nreal text"}],
        include_outputs=True,
    )

    headers = [header for header, _text, _priority in split_prompt_into_sections(block)]
    assert headers == ["Session history (earlier turns, newest first):"]


def test_render_report_disables_json_mode(agent_factory, monkeypatch):
    """report_to_user renders free-form markdown, so the completion must not run
    in JSON mode — otherwise a JSON-mode provider streams raw JSON to console."""
    from kernel import agent_model
    from kernel.agent_runtime import _render_report

    seen = {}

    def fake_completion(self, model, messages, stream=False, json_mode=True):
        seen["json_mode"] = json_mode
        seen["stream"] = stream
        return "## Done\nAll good."

    monkeypatch.setattr(agent_model, "_create_json_completion", fake_completion)
    monkeypatch.setattr(
        agent_model, "safe_extract_completion_content", lambda resp, *a: resp
    )

    agent = agent_factory(stream=False)
    out = _render_report(agent, "{}")

    assert seen["json_mode"] is False
    assert out == "## Done\nAll good."


# ── BUG-2: report tasks are answered, not stubbed ───────────────────────────

def test_is_report_goal_detects_substantive_requests():
    assert _is_report_goal("Найди конфиг и скажи что улучшить")
    assert _is_report_goal("explain what this config does")
    assert not _is_report_goal("проверь есть ли файл config.txt")
    assert not _is_report_goal("создай hello.py")
    # A change that also asks for an explanation still owes the explanation.
    assert _is_report_goal("исправь баг и объясни, что было не так")


def test_run_loop_report_task_lets_model_answer_instead_of_stub(agent_factory):
    """Regression for BUG-2: a verify-style task that also asks for a spoken
    answer must end with the model's substantive final_answer, never with the
    synthetic 'all checks passed' stub."""
    agent = agent_factory(max_steps=5)
    (agent.executor.workspace / "config.txt").write_text("gaps_in = 5\n", encoding="utf-8")
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.response_language = "ru"
    agent._state.task_language = "ru"

    substantive = (
        "В config.txt задано gaps_in = 5. Рекомендую уменьшить до 3 и вынести "
        "значение в переменную, чтобы переиспользовать в других правилах."
    )
    responses = [
        {
            "thought": "Читаю config",
            "plan_step": "inspect",
            "progress": 60,
            "user_message": "Читаю",
            "action": {"type": "read_file", "args": {"path": "config.txt"}},
        },
        {
            "thought": "Даю рекомендации",
            "plan_step": "done",
            "progress": 100,
            "user_message": "Готово",
            "action": {"type": "final_answer", "args": {"message": substantive}},
        },
    ]
    call_count = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        index = call_count["value"]
        if index >= len(responses):
            raise AssertionError("run_loop requested an extra action")
        call_count["value"] += 1
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model

    result = agent._run_loop("Найди config и скажи что улучшить", start_step=1)

    assert call_count["value"] == 2
    assert result == substantive
    assert "проверки пройдены" not in result.lower()


def test_runtime_preserves_a_short_model_answer_without_padding_or_retry(agent_factory):
    agent = agent_factory(max_steps=6)
    (agent.executor.workspace / "config.txt").write_text("gaps_in = 5\n", encoding="utf-8")
    agent._state.selected_action_role = "smart"
    agent._state.selected_action_model = "mock-model"
    agent._state.response_language = "ru"
    agent._state.task_language = "ru"

    stub_answer = {
        "thought": "Завершаю",
        "plan_step": "done",
        "progress": 100,
        "user_message": "Готово",
        "action": {"type": "final_answer", "args": {"message": "Готово."}},
    }
    responses = [
        {
            "thought": "Читаю config",
            "plan_step": "inspect",
            "progress": 60,
            "user_message": "Читаю",
            "action": {"type": "read_file", "args": {"path": "config.txt"}},
        },
        stub_answer,
        stub_answer,
    ]
    call_count = {"value": 0}

    def fake_call_action_model(prompt: str) -> str:
        index = call_count["value"]
        if index >= len(responses):
            raise AssertionError("run_loop requested an extra action")
        call_count["value"] += 1
        return json.dumps(responses[index], ensure_ascii=False)

    agent._call_action_model = fake_call_action_model

    result = agent._run_loop("Найди config и скажи что улучшить", start_step=1)

    assert call_count["value"] == 2
    assert result == "Готово."
