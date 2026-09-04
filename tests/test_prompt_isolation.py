# tests/test_prompt_isolation.py

import json
from types import SimpleNamespace

from agent_model import call_action_model, _load_role_prompt
from agent_planner import create_plan, PLANNER_PROMPT


def test_action_model_uses_current_agent_role_prompt(tmp_path):
    captured = {}

    class Agent:
        config = SimpleNamespace(fast_model="fast", smart_model="smart")
        executor = SimpleNamespace(workspace=tmp_path)
        _state = SimpleNamespace(
            current_agent_role="devops",
            selected_action_model="",
            selected_action_role="",
        )

        def _select_action_model_for_run(self):
            return "fast"

        def _prefer_smart_model(self):
            return False
        
        def _call_model(self, model, system_prompt, user_prompt, **kwargs):
            captured["model"] = model
            captured["system_prompt"] = system_prompt
            captured["user_prompt"] = user_prompt
            return "{}"

    call_action_model(Agent(), "test task")

    # build_role_system_prompt appends the selected style and this workspace's
    # instructions after the role prompt, so the role prompt is a prefix, not
    # the whole system prompt.
    assert captured["system_prompt"].startswith(_load_role_prompt("devops"))
    assert not captured["system_prompt"].startswith(_load_role_prompt("coder"))
    assert not captured["system_prompt"].startswith(_load_role_prompt("chat"))


def test_prompt_dir_resolves_to_correct_directory():
    """_PROMPT_DIR must point to prompts/system/, NOT a stale prompts/prompts/ copy."""
    from pathlib import Path
    from agent_model import _PROMPT_DIR

    resolved = _PROMPT_DIR.resolve()
    assert resolved.name == "system", f"_PROMPT_DIR resolves to {resolved}, expected .../prompts/system"
    assert resolved.parent.name == "prompts", f"_PROMPT_DIR resolves to {resolved}"
    # Must NOT end with prompts/prompts
    assert not str(resolved).endswith("prompts/prompts"), (
        f"_PROMPT_DIR points to stale duplicate: {resolved}"
    )


def test_prompt_duplicate_directory_not_loaded():
    """Verify that prompts/prompts/ (if it exists) is NOT the active prompt source."""
    from pathlib import Path
    from agent_model import _PROMPTS_ROOT as _PROMPT_DIR

    stale_dir = _PROMPT_DIR / "prompts"
    if stale_dir.exists():
        # Stale directory exists, but code must NOT use it
        resolved = _PROMPT_DIR.resolve()
        assert resolved != stale_dir.resolve(), (
            f"STALE PROMPT DETECTED: _PROMPT_DIR resolves to {resolved}, "
            f"but prompts/prompts/ also exists at {stale_dir}. "
            "Remove prompts/prompts/ — it is not the active prompt source."
        )


def test_planner_uses_planner_prompt_not_role_prompt():
    captured = {}

    class Agent:
        config = SimpleNamespace(fast_model="fast")

        def _call_model(self, model, system_prompt, user_prompt, **kwargs):
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
        def _is_complex_goal(self, goal):
            return False
    create_plan(Agent(), "test task")

    assert captured["system_prompt"] == PLANNER_PROMPT
    assert captured["system_prompt"] != _load_role_prompt("coder")
    assert captured["system_prompt"] != _load_role_prompt("devops")
    assert captured["system_prompt"] != _load_role_prompt("chat")
