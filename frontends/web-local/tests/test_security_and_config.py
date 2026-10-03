from __future__ import annotations

import json
import logging

from kvist_web import config_sync
from kvist_web.logging_setup import JsonFormatter
from kvist_web.protocol import ServerSpec
from kvist_web.security import BrowserAuth, SecretStore, redact


def test_browser_csrf_and_secret_redaction():
    auth = BrowserAuth()
    session = auth.create()
    assert auth.resolve(session.cookie) is session
    assert auth.verify_csrf(session, session.csrf) is True
    assert auth.verify_csrf(session, "wrong") is False
    assert "super-secret" not in redact("password=super-secret")


def test_ssh_password_is_not_serialized_in_server_config():
    spec = ServerSpec.from_dict(
        {
            "id": "gpu-box",
            "name": "GPU box",
            "host": "example",
            "auth_method": "password",
            "password": "never-store-this",
        }
    )
    assert "password" not in spec.to_dict()


def test_config_sync_requires_explicit_secret_selection(monkeypatch):
    source = {
        "llm_provider": "openai",
        "openai_api_key": "secret-key",
        "reasoning_effort": "high",
        "web_local": {"servers": ["private-host"]},
    }
    monkeypatch.setattr(config_sync, "load_user_config", lambda: source)

    without = config_sync.build_sync_payload([])
    with_secret = config_sync.build_sync_payload(["openai_api_key"])

    assert without["secrets"] == {}
    assert without["general"]["reasoning_effort"] == "high"
    assert "web_local" not in without["general"]
    assert with_secret["secrets"]["openai_api_key"] == "secret-key"


def test_structured_logs_redact_secrets_and_keep_process_context():
    record = logging.LogRecord(
        "kvist.web.ssh",
        logging.ERROR,
        __file__,
        1,
        "Authorization: Bearer secret-token and sk-proj-abcdefghijklmnop",
        (),
        None,
    )
    record.component = "ssh"
    record.server = "gpu-box"
    record.process_id = 4121
    payload = json.loads(JsonFormatter().format(record))

    assert payload["component"] == "ssh"
    assert payload["server"] == "gpu-box"
    assert payload["process"] == "4121"
    assert "secret-token" not in payload["message"]
    assert "sk-proj-abcdefghijklmnop" not in payload["message"]
