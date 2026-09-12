from __future__ import annotations

import json

import pytest

from parser import CommandValidationError, MAX_BATCH_ACTIONS, load_single_json_object, parse_agent_commands, validate_action_args


def valid_payload(action_type: str = "final_answer", args: dict | None = None) -> dict:
    return {
        "thought": "I will do the next step",
        "plan_step": "done" if action_type == "final_answer" else "inspect",
        "progress": 50,
        "user_message": "Working on it",
        "action": {"type": action_type, "args": args or {"message": "Done"}},
    }


def test_load_single_json_object_accepts_fenced_json_and_trailing_commas():
    text = '```json\n{"name": "value", "items": [1, 2,],}\n```'
    assert load_single_json_object(text) == {"name": "value", "items": [1, 2]}


def test_load_single_json_object_extracts_embedded_object_and_sanitizes_quotes():
    text = 'prefix {"message": “hello”} suffix'
    assert load_single_json_object(text) == {"message": "hello"}


def test_load_single_json_object_rejects_missing_json():
    with pytest.raises(CommandValidationError, match="Invalid JSON|Could not find"):
        load_single_json_object("no json here")


def test_parse_agent_command_returns_typed_command():
    (command,) = parse_agent_commands(
        '{"thought":"Think","plan_step":"inspect","progress":10,'
        '"user_message":"Reading","action":{"type":"read_file","args":{"path":"ui.py"}}}'
    )

    assert command.action.type == "read_file"
    assert command.action.args["path"] == "ui.py"


def test_parse_agent_command_ignores_a_declared_progress():
    """The runtime derives progress; a model still emitting one must not cost a step."""
    (command,) = parse_agent_commands(json.dumps({**valid_payload(), "progress": 101}))
    assert command.action.type == "final_answer"
    assert not hasattr(command, "progress")


@pytest.mark.parametrize(
    "payload,error",
    [
        ({**valid_payload(), "extra": 1}, "Extra fields"),
        ({**valid_payload(), "thought": ""}, "thought"),
        ({**valid_payload(), "plan_step": "unknown"}, "plan_step"),
        ({**valid_payload(), "action": {"type": "missing", "args": {}}}, "Invalid action"),
    ],
)
def test_parse_agent_command_rejects_invalid_root_schema(payload, error):
    import json

    with pytest.raises(CommandValidationError, match=error):
        parse_agent_commands(json.dumps(payload))


@pytest.mark.parametrize(
    "action_type,args",
    [
        ("refuse", {"reason": "illegal request"}),
        ("read_file", {"path": "a.py"}),
        ("read_multiple_files", {"paths": ["a.py", "b.py"]}),
        ("write_file", {"path": "a.py", "content": ""}),
        ("append_file", {"path": "a.py", "content": "x"}),
        ("replace_in_file", {"path": "a.py", "old_text": "x", "new_text": "", "count": 1}),
        ("list_dir", {"path": "."}),
        ("list_tree", {"path": ".", "max_depth": 2}),
        ("find_files", {"pattern": "*.py", "path": "."}),
        ("search_code", {"query": "class", "max_results": 10}),
        ("git_status", {}),
        ("git_diff", {"path": "a.py", "ref": "HEAD", "staged": False}),
        ("run_shell", {"command": ["python", "--version"], "timeout": 1}),
        ("web_search", {"query": "pytest docs", "top_k": 5, "freshness": "week"}),
        ("fetch_url", {"url": "https://docs.python.org/3/", "max_chars": 1000, "extract_mode": "text"}),
        ("delegate_task", {"target_role": "coder", "task": "inspect", "commands": ["git status"]}),
    ],
)
def test_validate_action_args_accepts_valid_shapes(action_type, args):
    validate_action_args(action_type, args)


@pytest.mark.parametrize(
    "action_type,args,error",
    [
        ("refuse", {}, "Missing required fields"),
        ("refuse", {"reason": ""}, "reason"),
        ("read_multiple_files", {"paths": []}, "non-empty list"),
        ("replace_in_file", {"path": "a", "old_text": "x", "new_text": "y", "count": 0}, "range 1..1000"),
        ("list_tree", {"path": ".", "max_depth": 11}, "range 1..10"),
        ("search_code", {"query": "x", "max_results": 201}, "range 1..200"),
        ("run_shell", {"command": [], "timeout": 1}, "must not be empty"),
        ("run_shell", {"command": ["python"], "timeout": 31}, "range 1..30"),
        ("run_shell", {"command": "systemctl status nginx", "timeout": 15}, "must be a list of strings"),
        ("web_search", {"query": "x", "freshness": "year"}, "freshness"),
        ("fetch_url", {"url": "file:///tmp/x"}, "http or https"),
        ("delegate_task", {"target_role": "root", "task": "x"}, "target_role"),
    ],
)
def test_validate_action_args_rejects_edge_cases(action_type, args, error):
    with pytest.raises(CommandValidationError, match=error):
        validate_action_args(action_type, args)


def test_parse_agent_command_accepts_optional_step_summary():
    (command,) = parse_agent_commands(
        '{"thought":"Think","plan_step":"inspect","progress":10,'
        '"user_message":"Reading","action":{"type":"read_file","args":{"path":"ui.py"}},'
        '"step_summary":"Read ui.py, found auth bug on line 42"}'
    )
    assert command.step_summary == "Read ui.py, found auth bug on line 42"


def test_parse_agent_command_accepts_missing_step_summary():
    (command,) = parse_agent_commands(
        '{"thought":"Think","plan_step":"inspect","progress":10,'
        '"user_message":"Reading","action":{"type":"read_file","args":{"path":"ui.py"}}}'
    )
    assert command.step_summary == ""


def test_parse_agent_command_rejects_non_string_step_summary():
    import json

    payload = {**valid_payload("read_file", {"path": "ui.py"}), "step_summary": 123}
    with pytest.raises(CommandValidationError, match="step_summary"):
        parse_agent_commands(json.dumps(payload))


def test_devops_run_shell_accepts_list_command():
    """DevOps run_shell: parser must accept command as list[str]."""
    payload = {
        "thought": "Проверяю статус systemd сервиса",
        "plan_step": "edit",
        "progress": 60,
        "user_message": "Проверяю статус nginx",
        "action": {
            "type": "run_shell",
            "args": {
                "command": ["systemctl", "status", "nginx", "--no-pager"],
                "timeout": 15,
            },
        },
    }
    import json
    (command,) = parse_agent_commands(json.dumps(payload))
    assert command.action.type == "run_shell"
    assert command.action.args["command"] == ["systemctl", "status", "nginx", "--no-pager"]
    assert isinstance(command.action.args["command"], list)
    assert all(isinstance(x, str) for x in command.action.args["command"])


def test_devops_run_shell_rejects_string_command_with_clear_error():
    """DevOps run_shell: if command is a string, the error message must mention 'list of strings'."""
    payload = {
        "thought": "Проверяю статус systemd сервиса",
        "plan_step": "edit",
        "progress": 60,
        "user_message": "Проверяю статус nginx",
        "action": {
            "type": "run_shell",
            "args": {
                "command": "systemctl status nginx --no-pager",
                "timeout": 15,
            },
        },
    }
    import json
    with pytest.raises(CommandValidationError, match="must be a list of strings"):
        parse_agent_commands(json.dumps(payload))


def batch_payload(actions: list[dict]) -> dict:
    return {
        "thought": "Both inputs are known",
        "plan_step": "edit",
        "user_message": "Creating the files",
        "actions": actions,
    }


def test_parse_agent_commands_expands_a_batch_in_order_with_shared_fields():
    commands = parse_agent_commands(
        json.dumps(
            batch_payload(
                [
                    {"type": "write_file", "args": {"path": "a.txt", "content": "a"}, "description": "Create a.txt"},
                    {"type": "write_file", "args": {"path": "b.txt", "content": "b"}},
                ]
            )
        )
    )

    assert [command.action.args["path"] for command in commands] == ["a.txt", "b.txt"]
    assert [command.action.description for command in commands] == ["Create a.txt", ""]
    assert {command.user_message for command in commands} == {"Creating the files"}


@pytest.mark.parametrize(
    "payload,error",
    [
        (batch_payload([]), "non-empty list"),
        (batch_payload([{"type": "list_dir", "args": {"path": "."}}] * (MAX_BATCH_ACTIONS + 1)), "at most"),
        ({**batch_payload([{"type": "list_dir", "args": {"path": "."}}]), "action": {"type": "list_dir", "args": {"path": "."}}}, "not both"),
        (batch_payload([{"type": "list_dir", "args": {"path": "."}, "description": 3}]), "description"),
        (batch_payload(["list_dir"]), "must be an object"),
        (
            batch_payload([{"type": "final_answer", "args": {"message": "done"}}, {"type": "list_dir", "args": {"path": "."}}]),
            "only be the last",
        ),
        (
            batch_payload([{"type": "list_dir", "args": {"path": "."}}, {"type": "refuse", "args": {"reason": "no"}}]),
            "only action",
        ),
    ],
)
def test_parse_agent_commands_rejects_malformed_batches(payload, error):
    with pytest.raises(CommandValidationError, match=error):
        parse_agent_commands(json.dumps(payload))


def test_parse_agent_commands_accepts_a_terminal_action_closing_the_batch():
    commands = parse_agent_commands(
        json.dumps(
            batch_payload(
                [
                    {"type": "list_dir", "args": {"path": "."}},
                    {"type": "final_answer", "args": {"message": "done"}},
                ]
            )
        )
    )
    assert [command.action.type for command in commands] == ["list_dir", "final_answer"]
