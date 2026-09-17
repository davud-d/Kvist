# -*- coding: utf-8 -*-
from __future__ import annotations

import json

from agent_runtime import _is_report_goal, _is_substantive_answer
from agent_state import PlanInfo
from tests.conftest import make_plan


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
    from agent import _SESSION_MULTI_READ_MAX_FILES

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
    from agent import _CHAT_HISTORY_PREAMBLE

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


def test_render_report_disables_json_mode(agent_factory, monkeypatch):
    """report_to_user renders free-form markdown, so the completion must not run
    in JSON mode — otherwise a JSON-mode provider streams raw JSON to console."""
    import agent_model
    from agent_runtime import _render_report

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


def test_resolve_report_goal_prefers_planner_flag(agent_factory):
    from agent_runtime import _resolve_report_goal

    agent = agent_factory()
    agent._state.plan_info = make_plan()

    # Planner says action even though the goal reads report-ish -> planner wins.
    agent._state.plan_info.task_kind = "action"
    assert _resolve_report_goal(agent, "объясни, что не так") is False

    # Planner says report even for a create-looking goal -> planner wins.
    agent._state.plan_info.task_kind = "report"
    assert _resolve_report_goal(agent, "создай hello.py") is True


def test_resolve_report_goal_falls_back_to_heuristic_without_plan(agent_factory):
    from agent_runtime import _resolve_report_goal

    agent = agent_factory()
    agent._state.plan_info = None
    assert _resolve_report_goal(agent, "что не так с этим конфигом") is True
    assert _resolve_report_goal(agent, "создай hello.py") is False


def test_is_substantive_answer_rejects_stub():
    assert not _is_substantive_answer("Готово, все проверки пройдены.")
    assert not _is_substantive_answer("Done. All checks passed.")
    assert _is_substantive_answer(
        "В hypr.conf стоит gaps_in=5; рекомендую вынести в переменную и "
        "уменьшить до 3 для более плотной раскладки, а также включить "
        "vrr для DP-1."
    )


def test_run_loop_report_task_lets_model_answer_instead_of_stub(agent_factory):
    """Regression for BUG-2: a verify-style task that also asks for a spoken
    answer must end with the model's substantive final_answer, never with the
    synthetic 'all checks passed' stub."""
    agent = agent_factory(max_steps=5)
    (agent.executor.workspace / "config.txt").write_text("gaps_in = 5\n", encoding="utf-8")
    agent._state.plan_info = PlanInfo(
        goal="Прочитать config и дать рекомендации",
        complexity="simple",
        plan=["inspect config", "report findings"],
        success_criteria=["config is read", "recommendations are given"],
        risks=[],
        task_kind="report",
    )
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


def test_final_answer_style_only_injected_when_finalization_imminent(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.response_language = "ru"

    agent._state.completion_ready = False
    agent._state.required_next_actions = []
    mid_step = agent._build_runtime_prompt("goal", 1)
    assert "Final answer style:" not in mid_step

    agent._state.completion_ready = True
    final_step = agent._build_runtime_prompt("goal", 2)
    assert "Final answer style:" in final_step


def test_final_answer_style_injected_when_terminal_action_required(agent_factory):
    agent = agent_factory()
    agent._state.plan_info = make_plan()
    agent._state.response_language = "ru"
    agent._state.completion_ready = False
    agent._state.required_next_actions = ["final_answer", "report_to_user"]

    prompt = agent._build_runtime_prompt("goal", 1)
    assert "Final answer style:" in prompt


def test_run_loop_report_task_stub_answer_is_retried_then_failed_honestly(agent_factory):
    agent = agent_factory(max_steps=6)
    (agent.executor.workspace / "config.txt").write_text("gaps_in = 5\n", encoding="utf-8")
    agent._state.plan_info = PlanInfo(
        goal="Прочитать config и дать рекомендации",
        complexity="simple",
        plan=["inspect config", "report findings"],
        success_criteria=["config is read", "recommendations are given"],
        risks=[],
        task_kind="report",
    )
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

    assert call_count["value"] == 3
    assert "не удалось" in result.lower()
