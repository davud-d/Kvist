# tests/test_prompt_isolation.py

import json
from types import SimpleNamespace

from agent_model import call_action_model, _load_role_prompt
from agent_planner import create_plan, PLANNER_PROMPT


def test_action_model_uses_current_agent_role_prompt():
    captured = {}

    class Agent:
        config = SimpleNamespace(fast_model="fast", smart_model="smart")
        _state = SimpleNamespace(
            current_agent_role="devops",
            selected_action_model="",
            selected_action_role="",
        )

        def _select_action_model_for_run(self):
            return "fast"

        def _call_model(self, model, system_prompt, user_prompt):
            captured["model"] = model
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return "{}"

    call_action_model(Agent(), "test task")

    assert captured["system_prompt"] == _load_role_prompt("devops")
    assert captured["system_prompt"] != _load_role_prompt("coder")
    assert captured["system_prompt"] != _load_role_prompt("chat")


def test_planner_uses_planner_prompt_not_role_prompt():
    captured = {}

    class Agent:
        config = SimpleNamespace(fast_model="fast")

        def _call_model(self, model, system_prompt, user_prompt):
            captured["model"] = model
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return json.dumps({
                "goal": "x",
                "complexity": "simple",
                "plan": ["step"],
                "success_criteria": ["done"],
                "risks": [],
            })

        def _parse_json_dict(self, raw):
            return json.loads(raw)

    create_plan(Agent(), "test task")

    assert captured["system_prompt"] == PLANNER_PROMPT
    assert captured["system_prompt"] != _load_role_prompt("coder")
    assert captured["system_prompt"] != _load_role_prompt("devops")
    assert captured["system_prompt"] != _load_role_prompt("chat")
