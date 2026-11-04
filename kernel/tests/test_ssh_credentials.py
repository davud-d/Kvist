# -*- coding: utf-8 -*-
"""The person's provider on a server: lent by the frontend, held in the daemon's memory, never written to the server's disk."""
from __future__ import annotations

import json
import threading

import pytest

from kernel.config import user_config
from kernel.ssh import credentials, wire
from kernel.tests.ssh_support import FakeCoreDaemon
from kernel.tests.test_ssh_daemon import is_type
from kernel.tests.test_ssh_proxy import server_tools, start_proxy  # noqa: F401  (fixture and helper)

LENT = {"llm_provider": "deepseek", "deepseek_api_key": "sk-lent-by-the-frontend", "llm_models": {"deepseek": {"fast": "f", "smart": "s"}}}


@pytest.fixture
def config_dir(tmp_path, monkeypatch):
    folder = tmp_path / "config"
    folder.mkdir()
    monkeypatch.setenv("KVIST_CONFIG_DIR", str(folder))
    monkeypatch.delenv("AGENT_LLM_PROVIDER", raising=False)
    yield folder
    user_config._held_in_memory = None


def write_config(folder, payload: dict) -> None:
    (folder / "config.json").write_text(json.dumps(payload), encoding="utf-8")


def on_disk(folder) -> dict:
    return json.loads((folder / "config.json").read_text(encoding="utf-8"))


def test_a_held_provider_wins_over_the_machines_own_and_nothing_of_it_reaches_the_disk(config_dir):
    write_config(config_dir, {"llm_provider": "openai", "openai_api_key": "sk-the-servers-own", "role_selection": "chat"})
    user_config.hold_provider_in_memory(LENT)

    assert user_config.get_saved_provider_or_none() == "deepseek"
    assert user_config.load_available_credentials(("deepseek_api_key",)) == {"deepseek_api_key": "sk-lent-by-the-frontend"}
    assert user_config.load_available_credentials(("openai_api_key",)) == {}

    # The things a session does to its settings: a model choice, a key typed in a wizard, a style that has nothing to do with providers.
    user_config.save_models_for_provider("deepseek", {"fast": "f2", "smart": "s2"})
    user_config.set_provider_secret("deepseek_api_key", "sk-typed-in-the-session")
    user_config.save_role_selection("coder")

    saved = on_disk(config_dir)
    assert saved["role_selection"] == "coder"
    assert (saved["llm_provider"], saved["openai_api_key"]) == ("openai", "sk-the-servers-own")
    assert "deepseek" not in json.dumps(saved) and "sk-lent" not in json.dumps(saved) and "sk-typed" not in json.dumps(saved)
    # What the session changed is what it sees afterwards.
    assert user_config.get_saved_models("deepseek") == {"fast": "f2", "smart": "s2"}
    assert user_config.load_available_credentials(("deepseek_api_key",)) == {"deepseek_api_key": "sk-typed-in-the-session"}


def test_without_a_held_provider_the_config_works_as_it_always_did(config_dir):
    user_config.set_provider_secret("deepseek_api_key", "sk-mine")
    user_config.save_models_for_provider("deepseek", {"fast": "f", "smart": "s"})

    assert on_disk(config_dir)["deepseek_api_key"] == "sk-mine"
    assert user_config.get_saved_models("deepseek") == {"fast": "f", "smart": "s"}


def test_this_computers_provider_is_lent_with_its_models_and_its_own_key_only(config_dir):
    write_config(
        config_dir,
        {
            "llm_provider": "deepseek",
            "deepseek_api_key": "sk-d",
            "deepseek_base_url": "https://d.example/v1",
            "openai_api_key": "sk-other-provider",
            "tavily_api_key": "tv-search",
            "llm_models": {"deepseek": {"fast": "f", "smart": "s"}, "openai": {"fast": "x", "smart": "y"}},
        },
    )

    settings = credentials.from_this_computer()

    assert settings == {
        "llm_provider": "deepseek",
        "llm_models": {"deepseek": {"fast": "f", "smart": "s"}},
        "deepseek_api_key": "sk-d",
        "deepseek_base_url": "https://d.example/v1",
    }


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"llm_provider": "chatgpt"},
        {"llm_provider": "deepseek"},
    ],
    ids=["nothing set up", "chatgpt sign-in cannot be lent", "the key is missing"],
)
def test_nothing_is_lent_when_there_is_nothing_usable(config_dir, monkeypatch, payload):
    for name in ("DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    write_config(config_dir, payload)

    assert credentials.from_this_computer() is None


def test_a_daemon_waiting_for_a_provider_asks_for_it_and_builds_nothing_until_it_arrives(tmp_path, config_dir):
    core = FakeCoreDaemon(tmp_path)
    try:
        daemon = core.daemon
        arrived: list[bool] = []
        waiting = threading.Thread(target=lambda: arrived.append(daemon.wait_for_credentials()), daemon=True)
        waiting.start()
        client = core.client()

        assert client.wait_for(is_type(wire.ATTACHED))["needs_credentials"] is True
        client.send({"type": wire.CREDENTIALS, "settings": LENT})
        waiting.join(timeout=5)

        assert arrived == [True]
        assert user_config.get_saved_provider_or_none() == "deepseek"
        # A second frontend is not asked again, and what it might send is ignored.
        later = core.client("b")
        assert later.wait_for(is_type(wire.ATTACHED))["needs_credentials"] is False
        later.send({"type": wire.CREDENTIALS, "settings": {**LENT, "llm_provider": "openai"}})
        assert user_config.get_saved_provider_or_none() == "deepseek"
    finally:
        core.close()


def test_a_frontend_with_nothing_to_lend_tells_the_daemon_to_use_its_own(tmp_path, config_dir):
    core = FakeCoreDaemon(tmp_path)
    try:
        waiting = threading.Thread(target=core.daemon.wait_for_credentials, daemon=True)
        waiting.start()
        client = core.client()
        client.wait_for(is_type(wire.ATTACHED))

        client.send({"type": wire.CREDENTIALS, "use_server": True})
        waiting.join(timeout=5)

        assert not waiting.is_alive() and not user_config.provider_is_held_in_memory()
    finally:
        core.close()


def test_a_daemon_that_is_told_to_stop_while_it_waits_does_not_hang(tmp_path, config_dir):
    core = FakeCoreDaemon(tmp_path)
    try:
        result: list[bool] = []
        waiting = threading.Thread(target=lambda: result.append(core.daemon.wait_for_credentials()), daemon=True)
        waiting.start()

        core.daemon.request_stop()
        waiting.join(timeout=5)

        assert result == [False]
    finally:
        core.close()


def test_the_proxy_lends_the_provider_when_the_daemon_asks_and_the_server_disk_stays_clean(server_tools, tmp_path, config_dir, monkeypatch):  # noqa: F811
    write_config(config_dir, {"llm_provider": "openai", "openai_api_key": "sk-the-servers-own"})
    monkeypatch.setattr(credentials, "from_this_computer", lambda: dict(LENT))
    core = FakeCoreDaemon(tmp_path)
    core.daemon.needs_credentials = True
    waiting = threading.Thread(target=core.daemon.wait_for_credentials, daemon=True)
    waiting.start()
    try:
        _, frontend, thread, _ = start_proxy(core.workspace)

        frontend.wait_for(lambda line: line["type"] == "control")
        waiting.join(timeout=10)
        frontend.leave()
        thread.join(timeout=10)

        assert user_config.get_saved_provider_or_none() == "deepseek"
        assert "sk-lent" not in (config_dir / "config.json").read_text(encoding="utf-8")
        assert all("sk-lent" not in line for line in core.paths.log.read_text(encoding="utf-8").splitlines())
    finally:
        core.close()


# --- switching providers in a session, and ChatGPT -------------------------------------------------------------

CHATGPT_SCOPE = "chatgpt.tokens.use.direct"


def signed_in_to_chatgpt(config_dir, *, expires_in: float = 3600) -> None:
    import time

    record = {
        "access_token": "access-1", "refresh_token": "refresh-secret", "id_token": "id", "expires_at": time.time() + expires_in,
        "scopes": [CHATGPT_SCOPE], "email": "me@example.org", "client_id": "c", "subject": "s",
    }
    write_config(config_dir, {"llm_provider": "chatgpt", "chatgpt_oauth": {"active": "c:s", "accounts": {"c:s": record}}})


def test_chatgpt_is_lent_as_an_access_token_and_the_refresh_token_never_leaves_this_computer(config_dir):
    signed_in_to_chatgpt(config_dir)

    settings = credentials.from_this_computer()

    assert settings is not None and settings["llm_provider"] == "chatgpt"
    assert "refresh-secret" not in json.dumps(settings)
    assert credentials.chatgpt_expiry(settings) is not None


def test_a_core_holding_a_lent_chatgpt_token_uses_it_and_never_tries_to_renew_it_itself(config_dir, monkeypatch):
    import time

    from kernel.providers import chatgpt_auth as auth

    monkeypatch.setattr(auth, "_token_request", lambda data: pytest.fail("a lent token must not be refreshed on the server"))
    signed_in_to_chatgpt(config_dir)
    settings = credentials.from_this_computer()
    (config_dir / "config.json").unlink()
    credentials.hold(settings)

    assert auth.has_credentials() and auth.access_token() == "access-1"

    # It runs out: the person is told what to do, not shown a browser.
    lent = user_config._held_in_memory["chatgpt_oauth"]["accounts"]["c:s"]
    lent["expires_at"] = time.time() - 5
    with pytest.raises(auth.ChatGPTAuthError, match="run out"):
        auth.access_token()


def test_picking_a_provider_the_core_was_not_given_asks_this_computer_for_it_and_never_opens_a_browser_on_the_server(config_dir, monkeypatch):
    from kernel.providers import chatgpt_auth as auth

    monkeypatch.setattr(auth, "login", lambda *a, **k: pytest.fail("no browser on the server"))
    asked: list[str] = []
    user_config.hold_provider_in_memory(LENT)

    def lender(provider: str) -> bool:
        asked.append(provider)
        return False

    user_config.set_credential_lender(lender)
    try:
        with pytest.raises(auth.ChatGPTAuthError, match="not signed in on your computer"):
            user_config.ensure_provider_credentials("chatgpt", "m")
    finally:
        user_config.set_credential_lender(None)

    assert asked == ["chatgpt"]


def test_a_provider_already_held_is_not_asked_for_again(config_dir):
    asked: list[str] = []
    user_config.hold_provider_in_memory(LENT)
    user_config.set_credential_lender(lambda provider: asked.append(provider) or True)
    try:
        user_config.ensure_provider_credentials("deepseek", "m", required_keys=("deepseek_api_key",))
    finally:
        user_config.set_credential_lender(None)

    assert asked == []


def test_the_daemon_asks_only_the_driver_and_keeps_what_comes_back_in_memory(tmp_path, config_dir):
    core = FakeCoreDaemon(tmp_path)
    try:
        driver = core.client("driver")
        driver.wait_for(is_type("control"))
        watcher = core.client("watcher")
        watcher.wait_for(is_type("control"))
        user_config.hold_provider_in_memory(LENT)
        result: list[bool] = []
        asking = threading.Thread(target=lambda: result.append(core.daemon.borrow("anthropic")), daemon=True)
        asking.start()

        need = driver.wait_for(is_type(wire.NEED))
        driver.send({"type": wire.CREDENTIALS, "for": need["provider"], "settings": {"llm_provider": "anthropic", "anthropic_api_key": "sk-ant-lent"}})
        asking.join(timeout=5)

        assert result == [True]
        assert user_config.load_available_credentials(("anthropic_api_key",)) == {"anthropic_api_key": "sk-ant-lent"}
        # The provider in use is not changed by lending another one, and the first one's key is still there.
        assert user_config.get_saved_provider_or_none() == "deepseek"
        assert user_config.load_available_credentials(("deepseek_api_key",))
        assert wire.NEED not in watcher.types()
    finally:
        core.close()


def test_a_renewed_chatgpt_token_replaces_the_one_that_was_about_to_run_out(tmp_path, config_dir):
    from kernel.providers import chatgpt_auth as auth

    signed_in_to_chatgpt(config_dir)
    credentials.hold(credentials.from_this_computer())
    core = FakeCoreDaemon(tmp_path)
    try:
        client = core.client()
        client.wait_for(is_type(wire.ATTACHED))
        renewed = credentials.from_this_computer()
        renewed["chatgpt_oauth"]["accounts"]["c:s"]["access_token"] = "access-2"

        client.send({"type": wire.CREDENTIALS, "renew": {"chatgpt_oauth": renewed["chatgpt_oauth"]}})
        deadline = __import__("time").monotonic() + 5
        while auth.access_token() != "access-2" and __import__("time").monotonic() < deadline:
            __import__("time").sleep(0.05)

        assert auth.access_token() == "access-2"
    finally:
        core.close()


def test_through_the_proxy_a_provider_picked_in_a_session_comes_from_this_computer(server_tools, tmp_path, config_dir, monkeypatch):  # noqa: F811
    # Daemon and proxy share this process here, and the held settings are process-wide, so the proxy's own read of the config is replaced.
    asked: list[str | None] = []

    def from_this_computer(wanted=None):
        asked.append(wanted)
        return {"llm_provider": wanted, "anthropic_api_key": "sk-ant-local"}

    monkeypatch.setattr(credentials, "from_this_computer", from_this_computer)
    core = FakeCoreDaemon(tmp_path)
    user_config.hold_provider_in_memory({"llm_provider": "deepseek", "deepseek_api_key": "sk-d"})
    try:
        _, frontend, thread, _ = start_proxy(core.workspace)
        frontend.wait_for(lambda line: line["type"] == "control")

        assert core.daemon.borrow("anthropic") is True

        assert asked == ["anthropic"]
        assert user_config.load_available_credentials(("anthropic_api_key",)) == {"anthropic_api_key": "sk-ant-local"}
        assert user_config.get_saved_provider_or_none() == "deepseek"
        frontend.leave()
        thread.join(timeout=10)
    finally:
        core.close()
