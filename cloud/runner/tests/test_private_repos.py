# -*- coding: utf-8 -*-
"""GitHub clone credentials never become a remote URL, command argument or workspace file."""
from __future__ import annotations

import base64
from pathlib import Path

import pytest

from kvist_runner import workspace

TOKEN = "gho_private_repo_canary_123456789"


@pytest.mark.parametrize("url", ["https://evil.example/repo.git", "https://github.com.evil.example/o/r",
                               "https://github.com@evil.example/o/r", "https://github.com:444/o/r",
                               "https://127.0.0.1/o/r", "https://github.com/o/../r", "https://github.com/o/r?token=x"])
async def test_credentials_never_reach_untrusted_hosts(tmp_path, url):
    with pytest.raises(workspace.WorkspaceError, match="repo_not_allowed"):
        await workspace.prepare(tmp_path, 1, url, False, github_token=TOKEN)


async def test_private_clone_uses_ephemeral_scoped_auth(monkeypatch, tmp_path):
    calls = []
    class Process:
        returncode = 0
        async def communicate(self):
            return b"", b""

    async def spawn(*args, **kwargs):
        calls.append((args, kwargs))
        Path(args[-1]).mkdir()
        return Process()

    monkeypatch.setattr(workspace.asyncio, "create_subprocess_exec", spawn)
    result = await workspace.prepare(tmp_path, 1, "https://github.com/owner/private.git", False, github_token=TOKEN)
    args, kwargs = calls[0]
    assert TOKEN not in str(args)
    env = kwargs["env"]
    values = {env[f"GIT_CONFIG_KEY_{i}"]: env[f"GIT_CONFIG_VALUE_{i}"] for i in range(int(env["GIT_CONFIG_COUNT"]))}
    auth = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    assert values["http.https://github.com/.extraHeader"] == f"Authorization: Basic {auth}"
    assert values["http.followRedirects"] == "false"
    assert values["credential.helper"] == ""
    assert list(result.iterdir()) == []


async def test_git_errors_scrub_raw_and_encoded_token(monkeypatch, tmp_path):
    auth = base64.b64encode(f"x-access-token:{TOKEN}".encode()).decode()
    class Process:
        returncode = 1
        async def communicate(self):
            return b"", f"failed {TOKEN} Authorization: Basic {auth}".encode()

    async def spawn(*args, **kwargs):
        return Process()

    monkeypatch.setattr(workspace.asyncio, "create_subprocess_exec", spawn)
    with pytest.raises(workspace.WorkspaceError) as raised:
        await workspace.prepare(tmp_path, 1, "https://github.com/o/r.git", False, github_token=TOKEN)
    assert TOKEN not in raised.value.detail and auth not in raised.value.detail
    assert "[redacted]" in raised.value.detail


async def test_project_symlink_is_not_reused(tmp_path):
    (tmp_path / "project").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(workspace.WorkspaceError, match="bad_folder"):
        await workspace.prepare(tmp_path, 1, "", False, project="project", github_token=TOKEN)
