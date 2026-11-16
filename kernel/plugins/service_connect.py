"""Person-only, interactive Gmail/Telegram setup. Never an agent tool."""
from __future__ import annotations

import base64
from contextvars import ContextVar
import hashlib
import json
import os
from pathlib import Path
import secrets
import shutil
import subprocess
import sys
import threading
import time
from urllib.parse import parse_qs, urlencode
from urllib.request import Request, urlopen

from kernel.plugins.mcp import save_server
from kernel.plugins.paths import plugins_root
from kernel.plugins.service_browser import LoginPage, launch, private_json

GMAIL_PACKAGE = "@artymclabin/gmail-mcp@1.2.4"
TELEGRAM_DEPS = ("Telethon==1.45.0", "qrcode==8.2")
GMAIL_SCOPE = "https://www.googleapis.com/auth/gmail.modify"
_UI = ContextVar("service_login_ui", default=None)


def _prompt(title: str, secret: bool = False) -> str:
    ui = _UI.get()
    if ui is None:
        raise RuntimeError("Use /gmail or /telegram inside Kvist to sign in.")
    value = (ui.prompt_secret if secret else ui.prompt_text)(title)
    if value is None:
        raise RuntimeError("Account login cancelled.")
    return value


def _launch(url: str) -> None:
    from kernel.plugins.service_browser import open_browser

    ui = _UI.get()
    if ui is None:
        launch(url)
    else:
        opened = open_browser(url)
        ui.print_narration(("Browser opened. " if opened else "Open this link on this computer: ") + url)


def _run(command: list[str], **kwargs) -> None:
    try:
        subprocess.run(command, check=True, stdout=sys.stderr, timeout=600, **kwargs)
    except (subprocess.SubprocessError, OSError):
        raise RuntimeError("Service setup failed. Check the installation output and retry /gmail or /telegram.") from None


def _google_token(data: dict) -> dict:
    request = Request("https://oauth2.googleapis.com/token", urlencode(data).encode(),
                      {"Content-Type": "application/x-www-form-urlencoded"})
    try:
        with urlopen(request, timeout=30) as response:
            return json.load(response)
    except (OSError, ValueError):
        raise RuntimeError("Google token exchange failed; repeat browser authorization.") from None


def google_login(keys: dict, *, timeout: float = 300) -> dict:
    """Desktop OAuth with PKCE, a random loopback port and state binding."""
    client = keys.get("installed", {})
    if not client.get("client_id") or not client.get("client_secret"):
        raise ValueError("Choose a Google OAuth Desktop app JSON file (installed client credentials).")
    state, verifier = secrets.token_urlsafe(32), secrets.token_urlsafe(64)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    answer: dict[str, str] = {}
    arrived = threading.Event()

    def callback(query):
        values = parse_qs(query)
        received = values.get("state", [""])
        if len(received) != 1 or not secrets.compare_digest(received[0], state):
            return 400, "<h1>Invalid login state</h1><p>Return to the original login tab.</p>"
        if arrived.is_set():
            return 409, "<p>This login was already handled.</p>"
        if values.get("error"):
            answer["error"] = "Google authorization was declined."
        elif len(values.get("code", [])) == 1:
            answer["code"] = values["code"][0]
        else:
            return 400, "<p>No authorization code was received.</p>"
        arrived.set()
        return 200, "<h1>Kvist</h1><p>Return to Kvist to see the connection result.</p>"

    with LoginPage(callback) as page:
        parameters = dict(client_id=client["client_id"], redirect_uri=page.url, response_type="code",
                          scope=GMAIL_SCOPE, access_type="offline", prompt="consent", state=state,
                          code_challenge=challenge, code_challenge_method="S256")
        _launch("https://accounts.google.com/o/oauth2/v2/auth?" + urlencode(parameters))
        if not arrived.wait(timeout):
            raise RuntimeError("Google login timed out; enter /gmail again.")
        if "error" in answer:
            raise RuntimeError(answer["error"])
        tokens = _google_token(dict(code=answer["code"], client_id=client["client_id"],
                                   client_secret=client["client_secret"], redirect_uri=page.url,
                                   grant_type="authorization_code", code_verifier=verifier))
    if not tokens.get("access_token") or not tokens.get("refresh_token"):
        raise RuntimeError("Google did not issue a persistent login; repeat authorization with consent.")
    if GMAIL_SCOPE not in tokens.get("scope", GMAIL_SCOPE).split():
        raise RuntimeError("Gmail access was not granted; reconnect and allow Gmail access.")
    tokens["expiry_date"] = int(time.time() * 1000) + int(tokens.pop("expires_in", 3600)) * 1000
    return {"tokens": tokens, "scopes": ["gmail.modify"]}


def _gmail(root: Path) -> dict:
    node, npm = shutil.which("node"), shutil.which("npm")
    if not node or not npm:
        raise RuntimeError("Install Node.js (with npm), then enter /gmail again.")
    key_path = Path(_prompt("Google OAuth Desktop app JSON file path").strip().strip('"')).expanduser()
    keys = json.loads(key_path.read_text(encoding="utf-8"))
    if not keys.get("installed", {}).get("client_id"):
        raise ValueError("Expected a Google OAuth Desktop app JSON file. Enable the Gmail API in its project.")
    runtime = root / "gmail-1.2.4"
    script = runtime / "node_modules/@artymclabin/gmail-mcp/dist/index.js"
    if not script.is_file():
        print(f"Installing {GMAIL_PACKAGE}…", file=sys.stderr)
        _run([npm, "install", "--prefix", str(runtime), "--ignore-scripts", "--no-audit", "--no-fund", GMAIL_PACKAGE])
    credentials = google_login(keys)
    # Verify the token before replacing an existing connection.
    request = Request("https://gmail.googleapis.com/gmail/v1/users/me/profile",
                      headers={"Authorization": "Bearer " + credentials["tokens"]["access_token"]})
    try:
        with urlopen(request, timeout=30) as response:
            profile = json.load(response)
    except (OSError, ValueError):
        raise RuntimeError("Gmail profile check failed. Enable Gmail API and reconnect.") from None
    account = profile.get("emailAddress")
    if not account:
        raise RuntimeError("Gmail did not return an account identity.")
    # Keep a previous connected account intact until all authorization checks pass.
    login = root / ("account-" + secrets.token_hex(8))
    private_json(login / "oauth.json", keys)
    private_json(login / "credentials.json", credentials)
    return dict(command=[node, "--input-type=module", "--eval",
                         "process.chdir(process.env.KVIST_GMAIL_HOME); await import(process.env.KVIST_GMAIL_MODULE)"],
                env={"HOME": str(Path.home()), "KVIST_GMAIL_HOME": str(login),
                "KVIST_GMAIL_MODULE": script.as_uri(),
                "GMAIL_OAUTH_PATH": str(login / "oauth.json"),
                "GMAIL_CREDENTIALS_PATH": str(login / "credentials.json")},
                readonly_tools=["search_emails", "read_email", "get_thread", "list_email_labels"],
                confirm_tools=["send_email", "send_draft", "reply_all"],
                description=f"Gmail ({account}): read, draft; every send requires human confirmation.")


def _telegram(root: Path) -> dict:
    runtime = root / "telegram-env"
    python = runtime / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
    if not python.is_file():
        base_python = sys.executable if not getattr(sys, "frozen", False) else (
            shutil.which("python3") or shutil.which("python"))
        if not base_python:
            raise RuntimeError("Install Python 3.11 or newer, then reconnect Telegram.")
        _run([base_python, "-m", "venv", str(runtime)])
    print("Preparing the Telegram runtime…", file=sys.stderr)
    _run([str(python), "-m", "pip", "install", "--disable-pip-version-check", *TELEGRAM_DEPS])
    # Standalone helper works in source, wheel and frozen Kvist distributions.
    for filename in ("telegram_service.py", "service_browser.py"):
        shutil.copyfile(Path(__file__).with_name(filename), root / filename)
    credentials = root / ("account-" + secrets.token_hex(8) + ".json")
    _telegram_login([str(python), str(root / "telegram_service.py"), "login-ui", str(credentials)])
    if not credentials.is_file():
        raise RuntimeError("Telegram login did not finish; enter /telegram again.")
    return dict(command=[str(python), str(root / "telegram_service.py"), "serve", str(credentials)],
                env={"HOME": str(Path.home()), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
                readonly_tools=["get_me", "list_chats", "get_messages"],
                confirm_tools=["send_message"],
                description="Personal Telegram: dialogs and history; every send requires human confirmation.")


def _telegram_login(command: list[str]) -> None:
    """Relay helper prompts through the frontend, never through the agent or stdout protocol."""
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                               text=True, encoding="utf-8")
    try:
        for line in process.stdout:
            event = json.loads(line)
            if event.get("type") == "prompt":
                answer = _prompt(event["title"], event.get("secret", False))
                process.stdin.write(json.dumps(answer) + "\n")
                process.stdin.flush()
            elif event.get("type") == "browser":
                _launch(event["url"])
        if process.wait(timeout=10):
            raise RuntimeError("Telegram login failed or expired. Use /telegram to retry.")
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        process.stdin.close()
        process.stdout.close()


def connected_services() -> set[str]:
    from kernel.plugins.mcp import load_servers

    return {name for name in load_servers() if name in ("gmail", "telegram")}


def connect_service(name: str, *, ui=None) -> dict:
    if ui is None:
        raise RuntimeError("Use /gmail or /telegram inside Kvist to sign in.")
    token = _UI.set(ui)
    try:
        return _connect_service(name)
    finally:
        _UI.reset(token)


def _connect_service(name: str) -> dict:
    if name not in ("gmail", "telegram"):
        raise ValueError("Browser connection is available for gmail or telegram.")
    root = (plugins_root() / "services" / name).resolve()
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    raw = _gmail(root) if name == "gmail" else _telegram(root)
    # Windows needs its system directory for child networking/runtime DLL discovery.
    if sys.platform == "win32":
        for key in ("SYSTEMROOT", "WINDIR", "USERPROFILE", "APPDATA", "LOCALAPPDATA"):
            if key in os.environ:
                raw["env"][key] = os.environ[key]
    raw.update(sandbox="none", network=True, workspace="none")
    save_server(name, raw)
    return {"ok": True, "connected": name,
            "note": f"{name} is ready. Ask Kvist normally; it selects the skill automatically. "
                    "Sending always asks for separate human confirmation."}
