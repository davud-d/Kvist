# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import pytest

from parser import parse_agent_command, CommandValidationError


class TestParseAgentCommand:
    def _make_payload(self, **overrides) -> str:
        base = {
            "thought": "тестовая мысль",
            "plan_step": "шаг 1",
            "progress": 10,
            "user_message": "делаю что-то",
            "action": {
                "type": "read_file",
                "args": {"path": "hello.py"},
            },
        }
        base.update(overrides)
        return json.dumps(base, ensure_ascii=False)

    def test_valid_read_file(self):
        cmd = parse_agent_command(self._make_payload())
        assert cmd.thought == "тестовая мысль"
        assert cmd.plan_step == "шаг 1"
        assert cmd.progress == 10
        assert cmd.action.type == "read_file"
        assert cmd.action.args == {"path": "hello.py"}

    def test_valid_final_answer(self):
        payload = self._make_payload(
            action={"type": "final_answer", "args": {"message": "готово"}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "final_answer"
        assert cmd.action.args["message"] == "готово"

    def test_valid_write_file(self):
        payload = self._make_payload(
            action={"type": "write_file", "args": {"path": "out.txt", "content": "data"}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "write_file"

    def test_valid_replace_in_file(self):
        payload = self._make_payload(
            action={
                "type": "replace_in_file",
                "args": {"path": "f.py", "old_text": "a", "new_text": "b"},
            }
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "replace_in_file"

    def test_valid_run_shell(self):
        payload = self._make_payload(
            action={"type": "run_shell", "args": {"command": ["git", "status"]}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "run_shell"

    def test_valid_web_search(self):
        payload = self._make_payload(
            action={"type": "web_search", "args": {"query": "python docs"}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "web_search"

    def test_valid_fetch_url(self):
        payload = self._make_payload(
            action={"type": "fetch_url", "args": {"url": "https://docs.python.org/"}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "fetch_url"

    def test_valid_list_tree(self):
        payload = self._make_payload(
            action={"type": "list_tree", "args": {"path": ".", "max_depth": 3}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.args["max_depth"] == 3

    def test_valid_search_code(self):
        payload = self._make_payload(
            action={"type": "search_code", "args": {"query": "def main"}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "search_code"

    def test_valid_read_multiple_files(self):
        payload = self._make_payload(
            action={"type": "read_multiple_files", "args": {"paths": ["a.py", "b.py"]}}
        )
        cmd = parse_agent_command(payload)
        assert cmd.action.type == "read_multiple_files"

    # --- Ошибки валидации ---

    def test_missing_thought(self):
        payload = self._make_payload(thought="")
        with pytest.raises(CommandValidationError, match="thought"):
            parse_agent_command(payload)

    def test_missing_plan_step(self):
        payload = self._make_payload(plan_step="")
        with pytest.raises(CommandValidationError, match="plan_step"):
            parse_agent_command(payload)

    def test_progress_out_of_range(self):
        payload = self._make_payload(progress=150)
        with pytest.raises(CommandValidationError, match="progress"):
            parse_agent_command(payload)

    def test_progress_negative(self):
        payload = self._make_payload(progress=-1)
        with pytest.raises(CommandValidationError, match="progress"):
            parse_agent_command(payload)

    def test_unknown_action_type(self):
        payload = self._make_payload(
            action={"type": "hack_system", "args": {}}
        )
        with pytest.raises(CommandValidationError, match="action.type"):
            parse_agent_command(payload)

    def test_extra_keys_rejected(self):
        payload = self._make_payload()
        data = json.loads(payload)
        data["extra_field"] = "not allowed"
        with pytest.raises(CommandValidationError, match="Лишние поля"):
            parse_agent_command(json.dumps(data))

    def test_read_file_missing_path(self):
        payload = self._make_payload(
            action={"type": "read_file", "args": {}}
        )
        with pytest.raises(CommandValidationError, match="path"):
            parse_agent_command(payload)

    def test_write_file_missing_content(self):
        payload = self._make_payload(
            action={"type": "write_file", "args": {"path": "f.py"}}
        )
        with pytest.raises(CommandValidationError, match="content"):
            parse_agent_command(payload)

    def test_run_shell_empty_command(self):
        payload = self._make_payload(
            action={"type": "run_shell", "args": {"command": []}}
        )
        with pytest.raises(CommandValidationError):
            parse_agent_command(payload)

    def test_run_shell_timeout_too_large(self):
        payload = self._make_payload(
            action={"type": "run_shell", "args": {"command": ["ls"], "timeout": 999}}
        )
        with pytest.raises(CommandValidationError, match="timeout"):
            parse_agent_command(payload)

    def test_fetch_url_non_http(self):
        payload = self._make_payload(
            action={"type": "fetch_url", "args": {"url": "ftp://evil.com/"}}
        )
        with pytest.raises(CommandValidationError, match="http"):
            parse_agent_command(payload)

    # --- Извлечение JSON из текста ---

    def test_json_in_markdown_fence(self):
        inner = self._make_payload()
        text = f"```json\n{inner}\n```"
        cmd = parse_agent_command(text)
        assert cmd.thought == "тестовая мысль"

    def test_json_with_surrounding_text(self):
        inner = self._make_payload()
        text = f"Вот мой ответ:\n{inner}\nконец"
        cmd = parse_agent_command(text)
        assert cmd.thought == "тестовая мысль"

    def test_curly_quotes_handled(self):
        raw = self._make_payload()
        mangled = raw.replace('"', '\u201c', 3).replace('"', '\u201d', 3)
        # Должно быть обработано sanitizer-ом
        cmd = parse_agent_command(mangled)
        assert cmd.action.type == "read_file"

    def test_garbage_input_raises(self):
        with pytest.raises(CommandValidationError):
            parse_agent_command("просто текст без JSON")

    def test_empty_input_raises(self):
        with pytest.raises(CommandValidationError):
            parse_agent_command("")
