"""Small personal-account stdio MCP server; runs in a managed Telethon environment."""
from __future__ import annotations

import asyncio
import getpass
import io
import json
from pathlib import Path
import sys
import time

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
    if "login-ui" in sys.argv:
        print(json.dumps({"type": "prompt", "title": title, "secret": secret}), flush=True)
        line = sys.stdin.readline()
        if not line:
            raise RuntimeError("Login cancelled")
        return json.loads(line)
    return getpass.getpass(title) if secret else input(title)


def _login_browser(url):
    if "login-ui" in sys.argv:
        print(json.dumps({"type": "browser", "url": url}), flush=True)
    else:
        launch(url)


async def phone_login(client):
    from telethon import errors

    phone = _login_prompt("Telegram phone number with country code / Номер с кодом страны: ").strip()
    if phone.startswith("+"):
        # People type numbers the way they are printed: "+7 999 123-45-67".
        phone = "+" + "".join(filter(str.isdigit, phone))
    if not phone.startswith("+") or not phone[1:].isdigit():
        raise ValueError("Enter the phone number with + and country code")
    sent = await client.send_code_request(phone)
    code = _login_prompt(
        "Login code / Код входа (check the Telegram service chat on your signed-in computer; "
        "delivery by SMS is decided by Telegram): ", secret=True).strip()
    if not code:
        raise ValueError("Login code is required")
    try:
        await client.sign_in(phone=phone, code=code, phone_code_hash=sent.phone_code_hash)
    except errors.SessionPasswordNeededError:
        password = _login_prompt("Telegram two-step verification password / Пароль 2FA: ", secret=True)
        await client.sign_in(password=password)


async def login(path: Path):
    from telethon import TelegramClient, errors
    from telethon.sessions import StringSession
    import qrcode
    from qrcode.image.svg import SvgPathImage

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
                deadline = time.monotonic() + 300
                opened = False
                while time.monotonic() < deadline:
                    qr = await client.qr_login()
                    svg = io.BytesIO()
                    qrcode.make(qr.url, image_factory=SvgPathImage).save(svg)
                    page.html = ("<meta http-equiv='refresh' content='5'><h1>Connect Telegram to Kvist</h1>"
                                 "<p>On your phone: Telegram → Settings → Devices → Link Desktop Device.</p>"
                                 "<p>Scan this QR code. / На телефоне: Настройки → Устройства → Подключить устройство.</p>"
                                 + svg.getvalue().decode().split("?>")[-1])
                    if not opened:
                        _login_browser(page.url)
                        opened = True
                    try:
                        await qr.wait(timeout=min(30, max(1, deadline - time.monotonic())))
                        break
                    except asyncio.TimeoutError:
                        continue
                    except errors.SessionPasswordNeededError:
                        page.html = "<h1>Two-step verification</h1><p>Enter your Telegram password in Kvist.</p>"
                        password = _login_prompt("Telegram two-step verification password: ", secret=True)
                        await client.sign_in(password=password)
                        break
        else:
            raise ValueError("Choose 1 for code login or 2 for QR login")
        if not await client.is_user_authorized():
            raise RuntimeError("Telegram login expired; enter /telegram again.")
        me = await client.get_me()
        if me.bot:
            raise RuntimeError("A personal Telegram account is required.")
        private_json(path, {"api_id": api_id, "api_hash": api_hash,
                            "session": client.session.save(), "user_id": me.id})
        print("Telegram connected. Every send requires confirmation in Kvist.", file=sys.stderr)
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
        asyncio.run(login(Path(filename)) if mode in ("login", "login-ui") else serve(Path(filename)))
    except KeyboardInterrupt:
        sys.exit(130)
    except Exception as error:
        # Login errors name what to fix (bad number, flood wait, wrong code) and carry no secrets;
        # serve errors stay opaque because they may echo message content.
        detail = f" ({error})" if sys.argv[1:2] in (["login"], ["login-ui"]) else ""
        print(f"Telegram {type(error).__name__}{detail}: connection failed; enter /telegram in Kvist to reconnect.",
              file=sys.stderr)
        sys.exit(1)
