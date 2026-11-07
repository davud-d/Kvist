# -*- coding: utf-8 -*-
"""What Kvist Cloud's runner writes for a ChatGPT customer is what the core reads, and the names still match."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "cloud" / "runner"))
from kvist_runner import chatgpt as runner_side  # noqa: E402

from kernel.config import user_config  # noqa: E402
from kernel.providers import chatgpt_auth  # noqa: E402

ACCESS = {"access_token": "access-token-0123456789", "expires_at": 4_000_000_000.0,
          "scopes": ["openid", chatgpt_auth.PLAN_SCOPE], "email": "me@example.com"}


def test_the_runner_uses_the_cores_names():
    assert runner_side.STORE_KEY == chatgpt_auth.STORE_KEY == user_config.CHATGPT_OAUTH_KEY
    assert runner_side.CONFIG_NAME == user_config.USER_CONFIG_FILENAME


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    monkeypatch.setenv(user_config.CONFIG_DIR_ENV, str(tmp_path / ".kvist"))
    return tmp_path / ".kvist"


def test_the_core_accepts_the_lent_token_as_a_signed_in_account(config_dir):
    runner_side.lend(config_dir, ACCESS)
    assert chatgpt_auth.has_credentials()
    assert chatgpt_auth.access_token() == ACCESS["access_token"]


def test_the_core_picks_up_a_renewed_token_without_restarting(config_dir):
    runner_side.lend(config_dir, ACCESS)
    assert chatgpt_auth.access_token() == ACCESS["access_token"]
    runner_side.lend(config_dir, {**ACCESS, "access_token": "renewed-token-0123456789"})
    assert chatgpt_auth.access_token() == "renewed-token-0123456789"
