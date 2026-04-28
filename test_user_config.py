# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

if "openai" not in sys.modules:
    openai_stub = types.ModuleType("openai")

    class AuthenticationError(Exception):
        pass

    openai_stub.AuthenticationError = AuthenticationError
    openai_stub.OpenAI = Mock()
    sys.modules["openai"] = openai_stub

from user_config import load_api_key, validate_api_key


class UserConfigTests(unittest.TestCase):
    def write_config(self, payload: dict[str, str]) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "config.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_load_api_key_accepts_documented_key_name(self) -> None:
        path = self.write_config({"openai_api_key": "  sk-test  "})

        self.assertEqual(load_api_key(path), "sk-test")

    def test_load_api_key_accepts_legacy_env_style_key_name(self) -> None:
        path = self.write_config({"OPENAI_API_KEY": "  sk-legacy  "})

        self.assertEqual(load_api_key(path), "sk-legacy")

    def test_validate_api_key_uses_models_api_without_chat_completion(self) -> None:
        client = Mock()

        with patch("user_config.OpenAI", return_value=client) as openai:
            validate_api_key("sk-test", "unused-model")

        openai.assert_called_once_with(api_key="sk-test")
        client.models.list.assert_called_once_with()
        client.chat.completions.create.assert_not_called()


if __name__ == "__main__":
    unittest.main()
