"""Browser onboarding: OS dispatch, OAuth boundary and standalone Telegram behavior."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import io
import json
from pathlib import Path
from types import SimpleNamespace
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import Request, urlopen

import pytest

from kernel.plugins import service_browser as browser
from kernel.plugins import service_connect as connect
from kernel.plugins import telegram_service as telegram


@pytest.mark.parametrize("platform, executable", [("linux", "/usr/bin/xdg-open"), ("darwin", "/usr/bin/open"),
                                                  ("win32", None)])
def test_browser_uses_native_launcher_without_shell(monkeypatch, platform, executable):
    calls = []
    monkeypatch.setattr(browser.sys, "platform", platform)
    monkeypatch.setattr(browser.shutil, "which", lambda _: "/usr/bin/xdg-open")
    monkeypatch.setattr(browser.os, "startfile", lambda url: calls.append(url), raising=False)
    monkeypatch.setattr(browser.subprocess, "run", lambda args, **kw: calls.append((args, kw)) or SimpleNamespace(returncode=0))
    url = "http://127.0.0.1:1234/login?state=a&code=b"
    assert browser.open_browser(url)
    if executable:
        assert calls[0][0] == [executable, url]
        assert "shell" not in calls[0][1]
    else:
        assert calls == [url]


def test_no_browser_is_reported_and_non_web_urls_are_refused(monkeypatch):
    monkeypatch.setattr(browser.sys, "platform", "linux")
    monkeypatch.setattr(browser.shutil, "which", lambda _: None)
    monkeypatch.setattr(browser.webbrowser, "open", lambda *_a, **_kw: False)
    assert not browser.open_browser("https://accounts.google.com/")
    with pytest.raises(ValueError):
        browser.open_browser("file:///etc/passwd")


def test_loopback_page_rejects_other_hosts_and_never_caches():
    with browser.LoginPage() as page:
        with urlopen(page.url, timeout=2) as response:
            assert response.headers["Cache-Control"] == "no-store"
            assert response.headers["Referrer-Policy"] == "no-referrer"
            assert b"Kvist" in response.read()
        with pytest.raises(HTTPError) as error:
            urlopen(Request(page.url, headers={"Host": "attacker.example"}), timeout=2)
        assert error.value.code == 403
        with pytest.raises(HTTPError):
            urlopen(page.url + "/other", timeout=2)


def test_google_state_pkce_and_persistent_credentials(monkeypatch):
    captured = {}

    def open_google(url):
        query = parse_qs(urlsplit(url).query)
        captured.update(query)
        callback = query["redirect_uri"][0]
        with pytest.raises(HTTPError):
            urlopen(callback + "?state=wrong&code=bad", timeout=2)
        with urlopen(callback + "?" + urlencode({"state": query["state"][0], "code": "correct-code"}), timeout=2):
            pass

    def exchange(data):
        assert data["code"] == "correct-code"
        challenge = base64.urlsafe_b64encode(hashlib.sha256(data["code_verifier"].encode()).digest()).rstrip(b"=").decode()
        assert captured["code_challenge"] == [challenge]
        assert captured["code_challenge_method"] == ["S256"]
        assert data["redirect_uri"] == captured["redirect_uri"][0]
        return {"access_token": "test-access", "refresh_token": "test-refresh", "expires_in": 3600,
                "scope": connect.GMAIL_SCOPE}

    monkeypatch.setattr(connect, "_launch", open_google)
    monkeypatch.setattr(connect, "_google_token", exchange)
    result = connect.google_login({"installed": {"client_id": "desktop", "client_secret": "test"}}, timeout=1)
    assert result["tokens"]["refresh_token"] == "test-refresh"
    assert "expiry_date" in result["tokens"]
    assert result["scopes"] == ["gmail.modify"]


@pytest.mark.parametrize("denied", [False, True])
def test_google_timeout_and_denial_do_not_exchange_tokens(monkeypatch, denied):
    def launch(url):
        query = parse_qs(urlsplit(url).query)
        if denied:
            with urlopen(query["redirect_uri"][0] + "?" + urlencode({"state": query["state"][0], "error": "access_denied"}), timeout=2):
                pass
    monkeypatch.setattr(connect, "_launch", launch)
    monkeypatch.setattr(connect, "_google_token", lambda _: pytest.fail("Must not exchange tokens"))
    with pytest.raises(RuntimeError, match="declined|timed out"):
        connect.google_login({"installed": {"client_id": "desktop", "client_secret": "test"}}, timeout=0.01)


def test_connect_is_not_available_to_noninteractive_agent_calls(monkeypatch):
    monkeypatch.setattr(connect.sys, "stdin", io.StringIO())
    monkeypatch.setattr(connect, "_gmail", lambda _: pytest.fail("Must not begin authorization"))
    with pytest.raises(RuntimeError, match="inside Kvist"):
        connect.connect_service("gmail")


def test_failed_login_preserves_existing_configuration(tmp_path, monkeypatch):
    from kernel.plugins.mcp import save_server, load_servers

    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path))
    save_server("gmail", {"command": ["old-server"]})
    monkeypatch.setattr(connect.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    def fail(_):
        raise RuntimeError("Login declined")
    monkeypatch.setattr(connect, "_gmail", fail)
    with pytest.raises(RuntimeError):
        connect.connect_service("gmail", ui=object())
    assert load_servers()["gmail"].command == ["old-server"]


def test_successful_connect_registers_confirmation_policy(tmp_path, monkeypatch):
    from kernel.plugins.mcp import load_servers

    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path))
    monkeypatch.setattr(connect.sys, "stdin", SimpleNamespace(isatty=lambda: True))
    monkeypatch.setattr(connect, "_telegram", lambda _: {"command": ["python", "telegram.py"], "env": {},
                        "readonly_tools": ["get_messages"], "confirm_tools": ["send_message"]})
    assert connect.connect_service("telegram", ui=object())["ok"]
    assert load_servers()["telegram"].confirm_tools == {"send_message"}


def test_private_credentials_are_replaced_without_backups(tmp_path):
    path = tmp_path / "private" / "credentials.json"
    browser.private_json(path, {"token": "old"})
    browser.private_json(path, {"token": "new"})
    assert json.loads(path.read_text()) == {"token": "new"}
    assert list(path.parent.iterdir()) == [path]
    if browser.os.name != "nt":
        assert path.stat().st_mode & 0o077 == 0


def test_telegram_read_keeps_signed_chat_id_and_never_sends():
    calls = []
    class Client:
        async def get_input_entity(self, peer):
            assert peer == -100123456
            return peer
        async def get_messages(self, entity, **kw):
            calls.append(kw)
            return []
    result = asyncio.run(telegram.execute(Client(), "get_messages", {"chat_id": "-100123456", "offset_id": 99}))
    assert result == {"messages": [], "next_offset_id": None}
    assert calls == [{"limit": 20, "offset_id": 99, "search": ""}]


def test_telegram_send_checks_reply_target_and_uses_plain_text():
    class Client:
        async def get_input_entity(self, peer):
            return peer
        async def get_messages(self, *_a, **_kw):
            return None
        async def send_message(self, peer, text, **kw):
            assert peer == 123 and text == "<b>literal</b>"
            assert kw == {"reply_to": None, "parse_mode": None, "link_preview": False}
            return SimpleNamespace(id=7, chat_id=123, sender_id=1, date=None, message=text, media=None)
    with pytest.raises(ValueError, match="Reply target"):
        asyncio.run(telegram.execute(Client(), "send_message", {"chat_id": "123", "text": "reply", "reply_to": 99}))
    assert asyncio.run(telegram.execute(Client(), "send_message", {"chat_id": "123", "text": "<b>literal</b>"}))["id"] == 7


@pytest.mark.parametrize("name, args", [("send_message", {"chat_id": "1", "text": ""}),
    ("get_messages", {"chat_id": "1", "limit": True}), ("get_messages", {"chat_id": "1", "limit": 1000}),
    ("get_me", {"secret": "value"}), ("unknown", {})])
def test_telegram_rejects_invalid_calls_before_api(name, args):
    with pytest.raises(ValueError):
        asyncio.run(telegram.execute(object(), name, args))


def test_standalone_telegram_stdio_handshake_and_calls(tmp_path, monkeypatch, capsys):
    import sys

    disconnected = []
    class Client:
        def __init__(self, *_args, **_kw):
            pass
        async def connect(self):
            pass
        async def disconnect(self):
            disconnected.append(True)
        async def is_user_authorized(self):
            return True
        async def get_me(self):
            return SimpleNamespace(id=42, username="test", first_name="Test")
    monkeypatch.setitem(sys.modules, "telethon", SimpleNamespace(TelegramClient=Client))
    monkeypatch.setitem(sys.modules, "telethon.sessions", SimpleNamespace(StringSession=lambda session: session))
    requests = [
        {"id": 1, "method": "initialize", "params": {}},
        {"method": "notifications/initialized"},
        {"id": 2, "method": "tools/list"},
        {"id": 3, "method": "tools/call", "params": {"name": "get_me", "arguments": {}}},
        {"id": 4, "method": "tools/call", "params": {"name": "send_message", "arguments": {}}},
    ]
    monkeypatch.setattr(telegram.sys, "stdin", io.StringIO("\n".join(json.dumps(r) for r in requests) + "\n"))
    credential_path = tmp_path / "session.json"
    credential_path.write_text(json.dumps({"api_id": 1, "api_hash": "private", "session": "private", "user_id": 42}))
    asyncio.run(telegram.serve(credential_path))
    replies = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [r["id"] for r in replies] == [1, 2, 3, 4]
    assert replies[0]["result"]["protocolVersion"] == "2025-06-18"
    assert {t["name"] for t in replies[1]["result"]["tools"]} == {t["name"] for t in telegram.TOOLS}
    assert json.loads(replies[2]["result"]["content"][0]["text"])["id"] == 42
    assert replies[3]["result"]["isError"]
    assert "private" not in json.dumps(replies)
    assert disconnected == [True]


def test_login_commands_disappear_after_connection_but_remain_callable(tmp_path, monkeypatch):
    from kernel import main
    from kernel.plugins.mcp import save_server
    from kernel.protocol.core_manifest import build_manifest
    from kernel.protocol.slash_commands import SlashCommandCompleter
    from prompt_toolkit.document import Document

    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path))
    def names():
        return {row["name"] for row in build_manifest(commands=main.STDIO_COMMANDS)["slash_commands"]}
    assert {"gmail", "telegram"} <= names()
    save_server("gmail", {"command": ["test-server"]})
    assert "gmail" not in names() and "telegram" in names()
    assert list(SlashCommandCompleter().get_completions(Document('/gmail'), None)) == []
    assert "gmail" in main.STDIO_COMMANDS


def test_slash_login_uses_frontend_dialogs_and_never_submits_a_model_task(tmp_path, monkeypatch):
    from kernel import main
    from kernel.plugins.mcp import save_server
    from kernel.protocol.event_stdio import CommandContext

    monkeypatch.setenv("KVIST_CONFIG_DIR", str(tmp_path))
    ui = SimpleNamespace(prompt_text=lambda title: "chosen.json")
    def login(name, *, ui):
        assert name == "gmail" and ui.prompt_text("OAuth file") == "chosen.json"
        save_server(name, {"command": ["test-server"]})
        return {"note": "Connected"}
    monkeypatch.setattr(connect, "connect_service", login)
    ctx = CommandContext(session=object(), ui=ui, rebuild=None, routed_role=None)
    result = main.STDIO_COMMANDS["gmail"](ctx)
    assert result.ok and result.effects == {"connected_services": ["gmail"]}
    ctx.args = ["send something"]
    assert not main.STDIO_COMMANDS["gmail"](ctx).ok


def test_helper_secrets_are_relayed_only_to_child_stdin(monkeypatch):
    writes = []
    class Output(io.StringIO):
        def close(self):
            writes.append(self.getvalue())
            super().close()
    child = SimpleNamespace(stdout=io.StringIO(json.dumps({"type": "prompt", "title": "Password", "secret": True})+'\n'),
                            stdin=Output(), wait=lambda timeout: 0, poll=lambda: 0)
    monkeypatch.setattr(connect.subprocess, "Popen", lambda *_a, **_kw: child)
    monkeypatch.setattr(connect, "_prompt", lambda title, secret: "private" if secret else pytest.fail("Must be secret"))
    connect._telegram_login(["helper"])
    assert writes == ['"private"\n']


def test_helper_failure_reason_reaches_the_person(monkeypatch):
    def popen(*_a, stderr, **_kw):
        stderr.write("Telegram PhoneNumberInvalidError (The phone number is invalid): connection failed\n")
        return SimpleNamespace(stdout=io.StringIO(), stdin=io.StringIO(), wait=lambda timeout: 1, poll=lambda: 1)
    monkeypatch.setattr(connect.subprocess, "Popen", popen)
    with pytest.raises(RuntimeError, match="phone number is invalid"):
        connect._telegram_login(["helper"])


@pytest.mark.parametrize('two_factor', [False, True])
def test_telegram_code_login_without_qr_and_with_optional_password(monkeypatch, two_factor):
    import sys
    class PasswordNeeded(Exception):
        pass
    monkeypatch.setitem(sys.modules, 'telethon', SimpleNamespace(errors=SimpleNamespace(SessionPasswordNeededError=PasswordNeeded)))
    answers = iter([('+1 234-567-89', False), ('12345', True), ('private-password', True)])

    def prompt(title, secret=False):
        # The phone number is an identifier the person must see while typing; code and password stay hidden.
        value, hidden = next(answers)
        assert secret is hidden, title
        return value

    monkeypatch.setattr(telegram, '_login_prompt', prompt)
    calls = []
    class Client:
        async def send_code_request(self, phone):
            assert phone == '+123456789'
            return SimpleNamespace(phone_code_hash='request-hash')
        async def sign_in(self, **kw):
            calls.append(kw)
            if two_factor and 'code' in kw:
                raise PasswordNeeded()
    asyncio.run(telegram.phone_login(Client()))
    assert calls[0] == {'phone': '+123456789', 'code': '12345', 'phone_code_hash': 'request-hash'}
    assert calls[1:] == ([{'password': 'private-password'}] if two_factor else [])


def test_telegram_rejects_invalid_number_before_requesting_code(monkeypatch):
    import sys
    monkeypatch.setitem(sys.modules, 'telethon', SimpleNamespace(errors=SimpleNamespace()))
    monkeypatch.setattr(telegram, '_login_prompt', lambda *a, **kw: 'invalid')
    with pytest.raises(ValueError, match='country code'):
        asyncio.run(telegram.phone_login(object()))


def test_telegram_code_can_be_resent_by_the_next_method(monkeypatch):
    import sys
    resend_module = SimpleNamespace(ResendCodeRequest=lambda phone, code_hash: ('resend', phone, code_hash))
    monkeypatch.setitem(sys.modules, 'telethon', SimpleNamespace(errors=SimpleNamespace()))
    monkeypatch.setitem(sys.modules, 'telethon.tl', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'telethon.tl.functions', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'telethon.tl.functions.auth', resend_module)
    answers = iter(['+123456789', 'r', '54321'])
    titles = []

    def prompt(title, secret=False):
        titles.append(title)
        return next(answers)

    monkeypatch.setattr(telegram, '_login_prompt', prompt)
    calls = []

    class Client:
        async def send_code_request(self, phone):
            return SimpleNamespace(phone_code_hash='first', type=SimpleNamespace(), next_type=object())

        async def __call__(self, request):
            calls.append(request)
            return SimpleNamespace(phone_code_hash='second', type=SimpleNamespace(), next_type=None)

        async def sign_in(self, **kw):
            calls.append(kw)

    asyncio.run(telegram.phone_login(Client()))
    assert calls == [('resend', '+123456789', 'first'),
                     {'phone': '+123456789', 'code': '54321', 'phone_code_hash': 'second'}]
    assert "'r'" in titles[1] and "'r'" not in titles[2]


def test_telegram_browser_login_shows_errors_and_resends_on_the_page(monkeypatch):
    import sys
    import threading
    from urllib.parse import urlencode

    class RPCError(Exception):
        pass

    class CodeInvalid(RPCError):
        pass

    class PasswordNeeded(RPCError):
        pass

    resend_module = SimpleNamespace(ResendCodeRequest=lambda phone, code_hash: ('resend', phone, code_hash))
    monkeypatch.setitem(sys.modules, 'telethon', SimpleNamespace(errors=SimpleNamespace(
        RPCError=RPCError, SessionPasswordNeededError=PasswordNeeded)))
    monkeypatch.setitem(sys.modules, 'telethon.tl', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'telethon.tl.functions', SimpleNamespace())
    monkeypatch.setitem(sys.modules, 'telethon.tl.functions.auth', resend_module)

    class Client:
        async def send_code_request(self, phone):
            return SimpleNamespace(phone_code_hash='first', type=SimpleNamespace(), next_type=object())

        async def __call__(self, request):
            return SimpleNamespace(phone_code_hash='second', type=SimpleNamespace(), next_type=None)

        async def sign_in(self, **kw):
            if kw['code'] != '54321':
                raise CodeInvalid('The code is invalid')

    replies = []

    def person(page):
        def post(**fields):
            request = Request(page.url, data=urlencode(fields).encode(), method='POST')
            with urlopen(request, timeout=10) as response:
                replies.append(response.read().decode())
        wait_for_form(page)
        post(action='code', code='00000')
        post(action='resend')
        post(action='code', code='54321')

    def wait_for_form(page):
        import time
        while 'Login code' not in page.html:
            time.sleep(0.01)

    with browser.LoginPage() as page:
        thread = threading.Thread(target=person, args=(page,))
        thread.start()
        asyncio.run(telegram.browser_code_login(Client(), page, '+123456789'))
        page.show('connected')  # the real flow ends with this page; the last POST waits for it
        thread.join(timeout=10)
    assert 'CodeInvalid: The code is invalid' in replies[0]
    assert 'Send the code another way' in replies[0]
    assert replies[2] == '<!doctype html><html><meta charset=\'utf-8\'><title>Kvist login</title><meta name=\'viewport\' content=\'width=device-width,initial-scale=1\'><body>connected</body></html>'
