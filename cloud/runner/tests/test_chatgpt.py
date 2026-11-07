# -*- coding: utf-8 -*-
"""The ChatGPT token the server lends: written where Kvist reads it, kept private, hidden from events."""
from __future__ import annotations

import asyncio
import json
import stat
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from kvist_runner.chatgpt import STORE_KEY, AccessError, lend
from kvist_runner.config import RunnerConfig
from kvist_runner.runner import Runner

ACCESS = {"access_token": "access-token-0123456789", "expires_at": 4_000_000_000.0,
          "scopes": ["openid", "chatgpt.tokens.use.direct"], "email": "me@example.com"}


def test_the_token_is_written_as_a_lent_account_and_the_file_is_private(tmp_path):
    lend(tmp_path / ".kvist", ACCESS)
    path = tmp_path / ".kvist" / "config.json"
    store = json.loads(path.read_text(encoding="utf-8"))[STORE_KEY]
    record = store["accounts"][store["active"]]
    assert record["access_token"] == ACCESS["access_token"] and record["lent"] is True
    assert "refresh_token" not in record
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_the_rest_of_the_config_is_kept_and_a_renewal_replaces_the_token(tmp_path):
    directory = tmp_path / ".kvist"
    directory.mkdir()
    (directory / "config.json").write_text(json.dumps({"llm_provider": "chatgpt"}), encoding="utf-8")
    lend(directory, ACCESS)
    lend(directory, {**ACCESS, "access_token": "renewed-token-0123456789"})
    saved = json.loads((directory / "config.json").read_text(encoding="utf-8"))
    assert saved["llm_provider"] == "chatgpt"
    assert [r["access_token"] for r in saved[STORE_KEY]["accounts"].values()] == ["renewed-token-0123456789"]
    assert [p.name for p in directory.iterdir()] == ["config.json"]


def test_a_config_that_cannot_be_read_is_not_overwritten(tmp_path):
    directory = tmp_path / ".kvist"
    directory.mkdir()
    (directory / "config.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(AccessError):
        lend(directory, ACCESS)
    assert (directory / "config.json").read_text(encoding="utf-8") == "{not json"


@pytest.mark.parametrize("bad", [{}, {**ACCESS, "access_token": ""}, {**ACCESS, "expires_at": "soon"},
                                 {**ACCESS, "scopes": "chatgpt"}])
def test_something_that_is_not_a_token_record_is_refused(tmp_path, bad):
    with pytest.raises(AccessError):
        lend(tmp_path / ".kvist", bad)
    assert not (tmp_path / ".kvist" / "config.json").exists()


def test_a_renewed_token_reaches_the_file_and_is_hidden_in_running_tasks(tmp_path):
    runner = Runner(RunnerConfig(machine_id=1, gateway_url="ws://unused", token="t" * 20, root=tmp_path,
                                 kvist_command=(sys.executable,)))
    hidden: list[str] = []
    runner.tasks[7] = SimpleNamespace(hide=hidden.append)
    asyncio.run(runner.handle({"type": "chatgpt_access", **ACCESS}))
    assert hidden == [ACCESS["access_token"]]
    saved = json.loads(Path(tmp_path / "home" / ".kvist" / "config.json").read_text(encoding="utf-8"))
    assert STORE_KEY in saved
