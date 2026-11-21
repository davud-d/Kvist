"""Small personal-account stdio MCP server; runs in a managed Telethon environment."""
from __future__ import annotations

import asyncio
import getpass
import io
import json
from pathlib import Path
import sys
import time
from html import escape

if __package__:
    from .service_browser import LoginPage, launch, private_json
else:
    from service_browser import LoginPage, launch, private_json


def tool(name, description, properties, required=()):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "additionalProperties": False, "properties": properties, "required": list(required)}}


CHAT = {"type": "string", "description": "Exact signed chat ID returned by list_chats, or verified username."}
TEXT = {"type": "string", "minLength": 1, "maxLength": 4096}
LIMIT = {"type": "integer", "minimum": 1, "maximum": 100, "default": 20}
TOOLS = [
    tool("get_me", "Read the connected personal account identity.", {}),
    tool("list_chats", "List dialogs with exact signed IDs; paginate with offset. Does not mark read.",
         {"limit": LIMIT, "offset": {"type": "integer", "minimum": 0, "maximum": 10000}}),
    tool("get_messages", "Read or search one chat. Use offset_id to paginate older messages. Does not mark read.",
         {"chat_id": CHAT, "limit": LIMIT, "offset_id": {"type": "integer", "minimum": 0},
          "search": {"type": "string", "maxLength": 500}}, ("chat_id",)),
    tool("draft_message", "Save a text draft in this Telegram chat without sending.",
         {"chat_id": CHAT, "text": TEXT}, ("chat_id", "text")),
    tool("send_message", "Send plain text. Kvist asks the person to approve this exact call each time.",
         {"chat_id": CHAT, "text": TEXT, "reply_to": {"type": "integer", "minimum": 1}}, ("chat_id", "text")),
]


def validate(name, arguments):
    definition = next((entry for entry in TOOLS if entry["name"] == name), None)
    if definition is None or not isinstance(arguments, dict):
        raise ValueError("Unknown tool or invalid arguments")
    schema = definition["inputSchema"]
    if set(arguments) - schema["properties"].keys() or set(schema["required"]) - arguments.keys():
        raise ValueError("Unexpected or missing tool arguments")
    for key, value in arguments.items():
        rule = schema["properties"][key]
        if rule["type"] == "integer":
            if type(value) is not int or value < rule.get("minimum", 0) or value > rule.get("maximum", 2**63 - 1):
                raise ValueError("Integer argument outside allowed range")
        elif not isinstance(value, str) or len(value) < rule.get("minLength", 0) or len(value) > rule.get("maxLength", 500):
            raise ValueError("Invalid text argument")


def _login_prompt(title, secret=False):
    return getpass.getpass(title) if secret else input(title)


def _login_browser(url):
    if "login-ui" in sys.argv:
        print(json.dumps({"type": "browser", "url": url}), flush=True)
    else:
        launch(url)


def _normalize_phone(phone):
    phone = phone.strip()
    if phone.startswith("+"):
        # People type numbers the way they are printed: "+7 999 123-45-67".
        phone = "+" + "".join(filter(str.isdigit, phone))
    if not phone.startswith("+") or not phone[1:].isdigit():
        raise ValueError("Enter the phone number with + and country code")
    return phone


def _delivery(sent):
    return type(getattr(sent, "type", None)).__name__.removeprefix("SentCodeType")


async def phone_login(client):
    from telethon import errors

    phone = _normalize_phone(_login_prompt("Telegram phone number with country code / Номер с кодом страны: "))
    sent = await client.send_code_request(phone)
    while True:
        # Telegram picks the channel itself (service chat in another signed-in app, SMS, call); naming it
        # tells the person where to look, and "r" asks Telegram for the next channel when nothing arrived.
        resend = " 'r' = resend by next method / повторить другим способом;" if getattr(sent, "next_type", None) else ""
        code = _login_prompt(
            f"Login code / Код входа (sent via {_delivery(sent)}: the Telegram service chat in another signed-in app, "
            f"or SMS;{resend}): ", secret=True).strip()
        if code.lower() != "r" or not resend:
            break
        from telethon.tl.functions.auth import ResendCodeRequest
        sent = await client(ResendCodeRequest(phone, sent.phone_code_hash))
    if not code:
        raise ValueError("Login code is required")
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except errors.SessionPasswordNeededError:
        password = _login_prompt("Telegram two-step verification password / Пароль 2FA: ", secret=True)
        await client.sign_in(password=password)


async def terminal_password(client):
    await client.sign_in(password=_login_prompt("Telegram two-step verification password: ", secret=True))


async def qr_login(client, page, on_password):
    from telethon import errors
    import qrcode
    from qrcode.image.svg import SvgPathImage

    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        qr = await client.qr_login()
        svg = io.BytesIO()
        qrcode.make(qr.url, image_factory=SvgPathImage).save(svg)
        page.show(f"{_STYLE}<meta http-equiv='refresh' content='5'><h1>Connect Telegram to Kvist</h1>"
                  f"<p>Already signed in to Telegram on this computer? <a href='{escape(qr.url)}'>"
                  "Click to confirm here</a> — it opens that same link a phone camera would scan, so your "
                  "own Telegram app can open it directly. / Уже вошли в Telegram на этом компьютере? "
                  "Нажмите ссылку — она подтвердит вход тем же способом, что и сканирование QR.</p>"
                  "<p>Or on a phone: Telegram → Settings → Devices → Link Desktop Device, then scan this "
                  "code. / Либо на телефоне: Настройки → Устройства → Подключить устройство, затем "
                  "отсканируйте код.</p>"
                  + svg.getvalue().decode().split("?>")[-1])
        try:
            await qr.wait(timeout=min(30, max(1, deadline - time.monotonic())))
            return
        except asyncio.TimeoutError:
            continue
        except errors.SessionPasswordNeededError:
            page.show("<h1>Two-step verification</h1><p>Enter your Telegram password in Kvist.</p>")
            await on_password(client)
            return


_STYLE = ("<style>body{font:16px system-ui,sans-serif;max-width:26em;margin:4em auto;padding:0 1em}"
          "input{width:100%;box-sizing:border-box;font-size:1.1em;padding:.5em;margin:.3em 0 1em}"
          "button{font-size:1em;padding:.6em 1.2em;margin-right:.5em;cursor:pointer}"
          ".err{color:#b00020}.hint{color:#555}</style>")


def _form(title, hint, fields, buttons, error=""):
    """fields: (name, label, input type, value); buttons: (action, label)."""
    rows = "".join(f"<label>{escape(label)}<input name='{name}' type='{kind}' value='{escape(value)}' "
                   f"autocomplete='off' {'autofocus' if index == 0 else ''}></label>"
                   for index, (name, label, kind, value) in enumerate(fields))
    actions = "".join(f"<button name='action' value='{action}'>{escape(label)}</button>" for action, label in buttons)
    problem = f"<p class='err'>{escape(error)}</p>" if error else ""
    return f"{_STYLE}<h1>{escape(title)}</h1><p class='hint'>{hint}</p>{problem}<form method='post'>{rows}{actions}</form>"


async def _ask(page, html):
    return await asyncio.to_thread(page.ask, html)


async def browser_password(client, page):
    from telethon import errors

    error = ""
    while True:
        form = await _ask(page, _form("Two-step verification / Пароль 2FA", "Your Telegram cloud password.",
                                      [("password", "Password", "password", "")], [("ok", "Sign in")], error))
        try:
            await client.sign_in(password=form["password"])
            return
        except errors.PasswordHashInvalidError as exc:
            error = str(exc)


async def browser_code_login(client, page, phone):
    from telethon import errors
    from telethon.tl.functions.auth import ResendCodeRequest

    sent = await client.send_code_request(phone)
    error = ""
    while True:
        hint = (f"The code was sent via <b>{escape(_delivery(sent))}</b>: look in the “Telegram” service chat "
                "in another app where you are signed in, or wait for the SMS.")
        # Always offered: when Telegram names no next channel the click shows its real answer instead of hiding it.
        buttons = [("code", "Sign in / Войти"),
                   ("resend", "Send the code another way / Отправить другим способом")]
        form = await _ask(page, _form("Login code / Код входа", hint, [("code", "Code", "text", "")], buttons, error))
        error = ""
        try:
            if form.get("action") == "resend":
                sent = await client(ResendCodeRequest(phone, sent.phone_code_hash))
                continue
            await client.sign_in(phone=phone, code=form.get("code", "").strip(), phone_code_hash=sent.phone_code_hash)
            return
        except errors.SessionPasswordNeededError:
            await browser_password(client, page)
            return
        except errors.RPCError as exc:
            error = f"{type(exc).__name__}: {exc}"


async def _save_account(client, api_id, api_hash, path):
    if not await client.is_user_authorized():
        raise RuntimeError("Telegram login expired; enter /telegram again.")
    me = await client.get_me()
    if me.bot:
        raise RuntimeError("A personal Telegram account is required.")
    private_json(path, {"api_id": api_id, "api_hash": api_hash,
                        "session": client.session.save(), "user_id": me.id})
    print("Telegram connected. Every send requires confirmation in Kvist.", file=sys.stderr)


async def login_in_browser(path: Path):
    """The whole login (keys, number, code, 2FA, QR) on one local page; Telegram's errors show on it."""
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession

    client = None
    form, error = {}, ""
    with LoginPage() as page:
        _login_browser(page.url)
        try:
            while True:
                form = await _ask(page, _form(
                    "Connect Telegram to Kvist / Подключить Telegram",
                    "API ID and hash come from <a href='https://my.telegram.org/apps' rel='noreferrer' "
                    "target='_blank'>my.telegram.org/apps</a>.",
                    [("api_id", "API ID", "text", form.get("api_id", "")),
                     ("api_hash", "API hash", "password", form.get("api_hash", "")),
                     ("phone", "Phone with country code / Номер с кодом страны", "text", form.get("phone", ""))],
                    # QR first: it needs no code, so it works when Telegram's code never arrives.
                    [("qr", "Sign in with QR (recommended) / Войти по QR"), ("code", "Get a code / Получить код")], error))
                error = ""
                try:
                    api_id, api_hash = int(form["api_id"]), form["api_hash"].strip()
                    if api_id <= 0 or not api_hash:
                        raise ValueError("API ID and hash are required")
                    phone = _normalize_phone(form["phone"]) if form["action"] == "code" else ""
                    if client:
                        await client.disconnect()
                    # A fresh session starts on a default data center; Telegram answers the first login request
                    # with PHONE_MIGRATE and Telethon needs a second attempt, so retries must stay enabled here.
                    client = TelegramClient(StringSession(), api_id, api_hash, flood_sleep_threshold=0)
                    await client.connect()
                    if form["action"] == "qr":
                        await qr_login(client, page, lambda c: browser_password(c, page))
                    else:
                        await browser_code_login(client, page, phone)
                    break
                except (ValueError, errors.RPCError) as exc:
                    error = f"{type(exc).__name__}: {exc}"
            await _save_account(client, api_id, api_hash, path)
            page.show(f"{_STYLE}<h1>Telegram connected ✓</h1><p>You can close this tab and return to Kvist. "
                      "/ Можно закрыть вкладку и вернуться в Kvist.</p>")
            await asyncio.sleep(1)
        finally:
            if client:
                await client.disconnect()


async def login(path: Path):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    api_id = int(_login_prompt("Telegram API ID (my.telegram.org/apps): "))
    api_hash = _login_prompt("Telegram API hash: ", secret=True).strip()
    if api_id <= 0 or not api_hash:
        raise ValueError("API ID and hash are required")
    # A fresh session starts on a default data center; Telegram answers the first login request with
    # PHONE_MIGRATE and Telethon needs a second attempt on the right DC, so retries must stay enabled here.
    client = TelegramClient(StringSession(), api_id, api_hash, flood_sleep_threshold=0)
    await client.connect()
    try:
        method = _login_prompt("Login method / Способ входа: 1 = code / код (no QR), 2 = QR [1]: ").strip() or "1"
        if method == "1":
            await phone_login(client)
        elif method == "2":
            with LoginPage() as page:
                _login_browser(page.url)
                await qr_login(client, page, terminal_password)
        else:
            raise ValueError("Choose 1 for code login or 2 for QR login")
        await _save_account(client, api_id, api_hash, path)
    finally:
        await client.disconnect()


def message_row(message):
    return {"id": message.id, "chat_id": str(message.chat_id), "sender_id": message.sender_id,
            "date": message.date.isoformat() if message.date else None, "text": message.message or "",
            "has_media": bool(message.media)}


async def execute(client, name, args):
    validate(name, args)
    if name == "get_me":
        me = await client.get_me()
        return {"id": me.id, "username": me.username, "name": me.first_name}
    if name == "list_chats":
        offset, limit = args.get("offset", 0), args.get("limit", 20)
        dialogs = await client.get_dialogs(limit=offset + limit + 1)
        return {"chats": [{"chat_id": str(d.id), "name": d.name, "unread_count": d.unread_count}
                          for d in dialogs[offset:offset + limit]],
                "next_offset": offset + limit if len(dialogs) > offset + limit else None}
    peer = args["chat_id"]
    if peer.lstrip("-").isdigit():
        peer = int(peer)
    entity = await client.get_input_entity(peer)
    if name == "get_messages":
        messages = await client.get_messages(entity, limit=args.get("limit", 20),
                                             offset_id=args.get("offset_id", 0), search=args.get("search", ""))
        return {"messages": [message_row(m) for m in messages],
                "next_offset_id": messages[-1].id if messages else None}
    if name == "draft_message":
        from telethon.tl.functions.messages import SaveDraftRequest

        await client(SaveDraftRequest(peer=entity, message=args["text"]))
        return {"draft_saved": True, "chat_id": args["chat_id"], "sent": False}
    reply_to = args.get("reply_to")
    if reply_to and not await client.get_messages(entity, ids=reply_to):
        raise ValueError("Reply target does not exist in this chat")
    message = await client.send_message(entity, args["text"], reply_to=reply_to, parse_mode=None, link_preview=False)
    return {"sent": True, **message_row(message)}


async def serve(path: Path):
    from telethon import TelegramClient
    from telethon.sessions import StringSession

    credentials = json.loads(path.read_text(encoding="utf-8"))
    client = TelegramClient(StringSession(credentials["session"]), credentials["api_id"], credentials["api_hash"],
                            request_retries=0, flood_sleep_threshold=0)
    await client.connect()
    try:
        if not await client.is_user_authorized() or (await client.get_me()).id != credentials["user_id"]:
            raise RuntimeError("Telegram account must be reconnected")
        while line := await asyncio.to_thread(sys.stdin.readline):
            request = {}
            try:
                request = json.loads(line)
                if not isinstance(request, dict):
                    raise ValueError("Invalid request")
                if "id" not in request:
                    continue
                method, params = request.get("method"), request.get("params", {})
                if method == "initialize":
                    result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}},
                              "serverInfo": {"name": "kvist-telegram", "version": "0.1.0"}}
                elif method == "tools/list":
                    result = {"tools": TOOLS}
                elif method == "ping":
                    result = {}
                elif method == "tools/call":
                    try:
                        data = await execute(client, params.get("name"), params.get("arguments", {}))
                        result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}]}
                    except Exception as error:
                        # Do not echo credentials, message text or opaque provider responses on errors.
                        result = {"isError": True, "content": [{"type": "text", "text":
                            f"Telegram operation failed ({type(error).__name__}). Reconcile sends before retrying."}]}
                else:
                    print(json.dumps({"jsonrpc": "2.0", "id": request["id"],
                                      "error": {"code": -32601, "message": "Unknown method"}}), flush=True)
                    continue
                response = {"jsonrpc": "2.0", "id": request["id"], "result": result}
            except (ValueError, TypeError, AttributeError):
                response = {"jsonrpc": "2.0", "id": request.get("id") if isinstance(request, dict) else None,
                            "error": {"code": -32600, "message": "Invalid request"}}
            print(json.dumps(response, ensure_ascii=False), flush=True)
    finally:
        await client.disconnect()


if __name__ == "__main__":
    try:
        mode, filename = sys.argv[1:]
        if mode not in ("login", "login-ui", "serve"):
            raise ValueError("Expected login or serve")
        asyncio.run({"login": login, "login-ui": login_in_browser, "serve": serve}[mode](Path(filename)))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        # Login errors name what to fix (bad number, flood wait, wrong code) and carry no secrets;
        # serve errors stay opaque because they may echo message content.
        detail = f" ({error})" if sys.argv[1:2] in (["login"], ["login-ui"]) else ""
        print(f"Telegram {type(error).__name__}{detail}: connection failed; enter /telegram in Kvist to reconnect.",
              file=sys.stderr)
        sys.exit(1)
