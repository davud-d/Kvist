from __future__ import annotations

import json

import pytest

from kernel.parser import (
    CommandValidationError,
    build_command,
    load_single_json_object,
    parse_model_response,
    plan_step_for_calls,
    validate_action_args,
)
from kernel.protocol import ToolCallRequest


def command_for(name: str, arguments: dict | None, *, text: str = "", language: str = "en"):
    call = ToolCallRequest(call_id="call_1", name=name, arguments=arguments)
    return build_command(call, text=text, batch_id="batch_1", plan_step="inspect", language=language)


def test_load_single_json_object_accepts_fenced_json_and_trailing_commas():
    text = '```json\n{"name": "value", "items": [1, 2,],}\n```'
    assert load_single_json_object(text) == {"name": "value", "items": [1, 2]}


def test_load_single_json_object_extracts_embedded_object_and_sanitizes_quotes():
    text = 'prefix {"message": “hello”} suffix'
    assert load_single_json_object(text) == {"message": "hello"}


def test_load_single_json_object_rejects_missing_json():
    with pytest.raises(CommandValidationError, match="Invalid JSON|Could not find"):
        load_single_json_object("no json here")


def test_build_command_returns_a_typed_command_carrying_the_batch():
    command = command_for("read_file", {"path": "ui.py"}, text="Читаю ui.py")

    assert command.action.type == "read_file"
    assert command.action.args == {"path": "ui.py"}
    assert (command.text, command.call_id, command.batch_id) == ("Читаю ui.py", "call_1", "batch_1")
    assert command.action.description == "reads ui.py"
    assert not hasattr(command, "thought") and not hasattr(command, "user_message")


@pytest.mark.parametrize(
    "name,arguments,error",
    [
        ("missing", {}, "Unknown tool"),
        ("read_file", {}, "Missing required fields"),
        ("read_file", {"path": "a", "extra": 1}, "Extra fields"),
    ],
)
def test_build_command_rejects_an_invalid_call(name, arguments, error):
    with pytest.raises(CommandValidationError, match=error):
        command_for(name, arguments)


def test_build_command_passes_on_a_call_that_could_not_be_read():
    call = ToolCallRequest(call_id="c", name="read_file", arguments=None, error="Tool call 1 was not run: invalid JSON.")
    with pytest.raises(CommandValidationError, match="invalid JSON"):
        build_command(call, text="", batch_id="b", plan_step="inspect", language="en")


def test_shell_purpose_becomes_the_label_and_never_reaches_the_executor():
    command = command_for(
        "run_shell",
        {"command": ["pytest", "-q"], "purpose": "гоняю тесты парсера перед правкой, чтобы видеть исходное состояние прямо сейчас"},
        language="ru",
    )

    assert "purpose" not in command.action.args
    assert command.action.description == "гоняю тесты парсера перед правкой, чтобы видеть исходное состояние прямо"


def test_shell_without_purpose_is_labelled_by_its_command():
    assert command_for("run_shell", {"command": ["ls"]}, language="ru").action.description == "запускает ls"


@pytest.mark.parametrize(
    "names,files_changed,expected",
    [
        (["read_file", "search_code"], False, "inspect"),
        (["read_file", "replace_in_file"], False, "edit"),
        (["run_shell"], True, "verify"),
        (["run_shell"], False, "inspect"),
        (["refuse"], False, "done"),
    ],
)
def test_plan_step_is_derived_from_the_calls(names, files_changed, expected):
    assert plan_step_for_calls(names, files_changed) == expected


@pytest.mark.parametrize(
    "action_type,args",
    [
        ("refuse", {"reason": "illegal request"}),
        ("read_file", {"path": "a.py"}),
        ("read_file", {"path": "a.py", "start_line": 10, "end_line": 10}),
        ("read_file", {"path": "a.py", "start_line": 10}),
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
        ("read_file", {"path": "a.py", "start_line": 0}, "'start_line' must be an integer >= 1"),
        ("read_file", {"path": "a.py", "end_line": "9"}, "'end_line' must be an integer >= 1"),
        ("read_file", {"path": "a.py", "start_line": True}, "'start_line' must be an integer >= 1"),
        ("read_file", {"path": "a.py", "start_line": 9, "end_line": 8}, "must not be less"),
        ("read_multiple_files", {"paths": []}, "non-empty list"),
        ("replace_in_file", {"path": "a", "old_text": "x", "new_text": "y", "count": 0}, "range 1..1000"),
        ("list_tree", {"path": ".", "max_depth": 11}, "range 1..10"),
        ("search_code", {"query": "x", "max_results": 201}, "range 1..200"),
        ("run_shell", {"command": [], "timeout": 1}, "must not be empty"),
        ("run_shell", {"command": ["python"], "timeout": "30s"}, "must be a number"),
        ("run_shell", {"command": "systemctl status nginx", "timeout": 15}, "must be a list of strings"),
        ("web_search", {"query": "x", "freshness": "year"}, "freshness"),
        ("fetch_url", {"url": "file:///tmp/x"}, "http or https"),
        ("delegate_task", {"target_role": "root", "task": "x"}, "target_role"),
    ],
)
def test_validate_action_args_rejects_edge_cases(action_type, args, error):
    with pytest.raises(CommandValidationError, match=error):
        validate_action_args(action_type, args)


@pytest.mark.parametrize(
    "given,expected",
    [(600, 300.0), (0, 1.0), (60, 60)],
)
def test_run_shell_timeout_is_clamped_not_rejected(given, expected):
    # The model cannot know the active profile's ceiling; a 60s timeout must
    # not cost the whole response a format error.
    args = {"command": ["pytest"], "timeout": given}
    validate_action_args("run_shell", args)
    assert args["timeout"] == expected


def test_build_command_clamps_out_of_range_timeout():
    assert command_for("run_shell", {"command": ["pytest"], "timeout": 600}).action.args["timeout"] == 300.0


def test_devops_run_shell_accepts_list_command():
    command = command_for("run_shell", {"command": ["systemctl", "status", "nginx", "--no-pager"], "timeout": 15})
    assert command.action.args["command"] == ["systemctl", "status", "nginx", "--no-pager"]


def test_devops_run_shell_rejects_string_command_with_clear_error():
    with pytest.raises(CommandValidationError, match="must be a list of strings"):
        command_for("run_shell", {"command": "systemctl status nginx --no-pager", "timeout": 15})


# --- v2 response: text plus <tool_call> blocks --------------------------------

def _call(name: str, **arguments) -> str:
    return "<tool_call>" + json.dumps({"name": name, "arguments": arguments}) + "</tool_call>"


def test_model_response_splits_text_and_calls_in_order():
    response = parse_model_response(
        "Нашёл два места. Читаю оба.\n"
        + _call("read_file", path="a.py")
        + "\n"
        + _call("search_code", query="role", path="src/")
    )

    assert response.text == "Нашёл два места. Читаю оба."
    assert [(call.name, call.arguments) for call in response.calls] == [
        ("read_file", {"path": "a.py"}),
        ("search_code", {"query": "role", "path": "src/"}),
    ]
    assert all(not call.error for call in response.calls)
    assert len({call.call_id for call in response.calls}) == 2


def test_model_response_without_calls_is_the_end_of_the_turn():
    response = parse_model_response("  Готово: тесты зелёные.  ")

    assert response.text == "Готово: тесты зелёные."
    assert response.calls == []


def test_model_response_finds_json_in_the_middle_of_text():
    response = parse_model_response(
        "Сначала посмотрю конфиг " + _call("read_file", path="app.conf") + " и потом решу."
    )

    assert response.text == "Сначала посмотрю конфиг"
    assert response.calls[0].arguments == {"path": "app.conf"}


def test_model_response_tolerates_extra_whitespace_inside_and_around_tags():
    response = parse_model_response(
        '< tool_call >\n\n   {  "name" :  "list_dir" ,  "arguments" : { "path" : "." } }   \n\n</ TOOL_CALL >'
    )

    assert [(call.name, call.arguments) for call in response.calls] == [("list_dir", {"path": "."})]
    assert response.text == ""


def test_model_response_strips_a_markdown_fence_around_the_tag():
    response = parse_model_response(
        "Смотрю.\n```json\n" + _call("read_file", path="a.py") + "\n```\nДальше по результату."
    )

    assert response.text == "Смотрю."
    assert response.calls[0].name == "read_file"


def test_model_response_strips_a_markdown_fence_inside_the_tag():
    response = parse_model_response(
        '<tool_call>\n```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```\n</tool_call>'
    )

    assert response.calls[0].arguments == {"path": "a.py"}
    assert response.text == ""


def test_model_response_keeps_a_code_block_that_does_not_touch_a_tag():
    response = parse_model_response(
        "Было:\n```python\nx = 1\n```\nПравлю.\n" + _call("read_file", path="x.py")
    )

    assert "```python\nx = 1\n```" in response.text


def test_model_response_reads_an_unclosed_tag_to_the_end_of_its_json():
    response = parse_model_response(
        'Читаю.\n<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}\nПосле вызова.'
    )

    assert response.calls[0].arguments == {"path": "a.py"}
    assert response.text == "Читаю."


def test_model_response_drops_a_result_reported_after_the_first_call():
    response = parse_model_response(
        "Создам файл и поменяю строку.\n"
        + _call("write_file", path="a.txt", content="1\n2\n3")
        + "\nФайл создан, вторая строка заменена. Готово."
    )

    assert response.text == "Создам файл и поменяю строку."
    assert [call.name for call in response.calls] == ["write_file"]


def test_model_response_without_a_call_keeps_the_whole_text_as_the_answer():
    response = parse_model_response("Готово: файл создан.")

    assert response.text == "Готово: файл создан."
    assert response.calls == []


def test_model_response_ends_an_unclosed_tag_at_the_next_tag():
    response = parse_model_response(
        '<tool_call>{"name": "read_file", "arguments": {"path": "a.py"}}\n'
        '<tool_call>{"name": "read_file", "arguments": {"path": "b.py"}}</tool_call>'
    )

    assert [call.arguments["path"] for call in response.calls] == ["a.py", "b.py"]


def test_model_response_turns_a_broken_call_into_an_error_without_losing_the_rest():
    response = parse_model_response(
        '<tool_call>{"name": "read_file", "arguments": {"path": }}</tool_call>'
        + _call("list_dir", path=".")
    )

    broken, good = response.calls
    assert broken.name == "read_file"
    assert broken.arguments is None
    assert "Tool call 1 was not run" in broken.error and "invalid JSON" in broken.error
    assert (good.name, good.arguments, good.error) == ("list_dir", {"path": "."}, "")


def test_model_response_reports_a_call_without_a_name():
    (call,) = parse_model_response('<tool_call>{"arguments": {"path": "a"}}</tool_call>').calls

    assert call.arguments is None
    assert "no tool name" in call.error


def test_model_response_reports_a_tag_without_json():
    (call,) = parse_model_response("<tool_call>read_file a.py</tool_call>").calls

    assert "no JSON object" in call.error


def test_model_response_accepts_argument_strings_and_openai_shaped_calls():
    response = parse_model_response(
        '<tool_call>{"name": "read_file", "arguments": "{\\"path\\": \\"a.py\\"}"}</tool_call>'
        '<tool_call>{"type": "function", "function": {"name": "list_dir", "arguments": {"path": "."}}}</tool_call>'
        '<tool_call>{"tool": "git_status", "args": {}}</tool_call>'
    )

    assert [(call.name, call.arguments) for call in response.calls] == [
        ("read_file", {"path": "a.py"}),
        ("list_dir", {"path": "."}),
        ("git_status", {}),
    ]


def test_model_response_repairs_smart_quotes_and_trailing_commas_inside_a_tag():
    (call,) = parse_model_response('<tool_call>{“name”: “read_file”, “arguments”: {“path”: “a.py”,},}</tool_call>').calls

    assert (call.name, call.arguments, call.error) == ("read_file", {"path": "a.py"}, "")


def test_model_response_reads_the_pre_v2_object_as_calls():
    response = parse_model_response(
        json.dumps(
            {
                "thought": "ignored",
                "plan_step": "inspect",
                "user_message": "Читаю файл",
                "actions": [{"type": "read_file", "args": {"path": "a.py"}, "description": "Read"}],
                "step_summary": "ignored",
            }
        )
    )

    assert response.text == "Читаю файл"
    assert [(call.name, call.arguments) for call in response.calls] == [("read_file", {"path": "a.py"})]


def test_model_response_never_runs_a_json_example_inside_an_answer():
    response = parse_model_response('Пример ответа: {"actions": [{"type": "write_file", "args": {}}]} — вот так.')

    assert response.calls == []


def test_model_response_reads_a_bare_call_object_without_tags():
    (call,) = parse_model_response('```json\n{"name": "read_file", "arguments": {"path": "a.py"}}\n```').calls

    assert (call.name, call.arguments) == ("read_file", {"path": "a.py"})
