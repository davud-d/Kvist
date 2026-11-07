# -*- coding: utf-8 -*-
"""The customer's ChatGPT access token, written where Kvist reads it.

The server renews the token and sends only the short-lived access token; Kvist is told it is "lent"
(no refresh token on this machine), which is exactly how a core serving someone else's frontend holds one.
The two names below are Kvist's (``kernel/providers/chatgpt_auth.STORE_KEY``, the config file name); a test in
the cloud suite fails if they drift apart, since the runner does not import the core.
"""
from __future__ import annotations

import contextlib
import json
import os
import tempfile
from pathlib import Path

STORE_KEY = "chatgpt_oauth"
CONFIG_NAME = "config.json"
ACCOUNT = "kvist-cloud"


class AccessError(ValueError):
    """The server sent a token record that is not one."""


def lend(config_dir: Path, access: dict) -> None:
    """Writes ``access`` into Kvist's config (``config_dir`` is its KVIST_CONFIG_DIR), keeping the rest of it."""
    token, ends, scopes = access.get("access_token"), access.get("expires_at"), access.get("scopes")
    if not isinstance(token, str) or not token or not isinstance(ends, int | float) or not isinstance(scopes, list):
        raise AccessError("not a ChatGPT access record")
    record = {"access_token": token, "expires_at": float(ends), "scopes": [str(s) for s in scopes],
              "email": str(access.get("email", "")), "lent": True}
    path = config_dir / CONFIG_NAME
    try:
        current = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        current = {}
    except json.JSONDecodeError:
        # Never overwrite a file this code cannot read: Kvist reports it, the person fixes or removes it.
        raise AccessError("Kvist's config is not JSON") from None
    if not isinstance(current, dict):
        raise AccessError("Kvist's config is not an object")
    current[STORE_KEY] = {"active": ACCOUNT, "accounts": {ACCOUNT: record}}
    config_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    # Replaced whole, so Kvist never reads half a file while a task is running.
    fd, temporary = tempfile.mkstemp(dir=config_dir, prefix=".config-")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(current, handle)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temporary)
        raise
