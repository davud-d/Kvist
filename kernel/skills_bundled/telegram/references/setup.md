# Telegram personal-account browser connection

Enter this once inside Kvist on macOS, Linux or Windows:

```text
/telegram
```

Kvist prepares a private Python environment with pinned Telethon and QR-code dependencies.
A frozen Kvist build needs a separately installed Python 3.11+ with venv/pip. Enter your
API ID and API hash from https://my.telegram.org/apps (the hash is hidden in the Kvist interface).
Choose 1 to sign in by phone number and code without QR (the default), or 2 for QR.
For code login, enter the account phone number with country code, then the code Telegram
sends. Check the service chat in an already signed-in Telegram Desktop/Web session.
Telegram decides how to deliver the code; SMS delivery is not guaranteed. If you cannot
access any authorized session or the delivery destination, this flow cannot bypass that
account verification. For QR login, the command opens a local browser page with a QR code. On your phone, open
Telegram → Settings → Devices → Link Desktop Device and scan it. If two-step verification
is enabled, enter its password in the Kvist interface, not in chat. The session is saved only
after login and personal-account identity verification succeed.

This is a **personal account**, not a bot token. Telegram does not provide Google's browser
consent flow for full chat access; the browser displays a locally generated QR code and
you approve it in Telegram. Nothing is sent to an external QR rendering service. QR codes
refresh automatically during a five-minute login window. If launching the browser fails,
open the printed local link on the same computer while the command is running.

Once connected, use this in Kvist:

```text
Ответь менеджеру в Telegram, что я пришлю документы завтра.
```

The agent selects the bundled skill and attaches the configured MCP server automatically. Its first attachment has the normal
server-tool approval. Available tools: `get_me`, `list_chats`, `get_messages`, `draft_message`
and `send_message`. Inspection does not mark messages read. `draft_message` saves a chat
draft without sending; preparing reply text in Kvist needs no Telegram mutation.

**Every send** needs a separate human confirmation in Kvist showing the chat ID, text and
reply target, even in automatic-approval mode. Resolve and show the recipient's identity
before proposing a send. Denied calls never reach Telegram. No interactive person means
no send. This initial connector handles text; media uploads, forwarding and administration
are not exposed. Do not invent tools for them.

If you removed the bundled instructions skill, restore it, then restart Kvist:

```text
kvist --skill-op install --skill-path bundled:telegram
```

A newly saved connection is visible to the next task. Repeat connect to replace an expired
login or change accounts. `kvist --mcp-op remove --mcp-name telegram` removes registration;
terminate the corresponding session in Telegram's Devices settings to revoke account access.

## Storage and failures

The server and login files are in `<config_dir>/plugins/services/telegram`. The server runs
with your OS rights (`sandbox: none`), and `workspace: none` is not filesystem isolation.
Treat its credential files as account access: do not put them in a prompt, export or
repository. They are separate from chat history and model-visible tool output.

Install failure: check Python/venv/pip and internet access. QR expired: reconnect. Wrong
API credentials or two-step password: correct them and reconnect. A flood-wait response
means wait rather than loop retries. A failed send has an unknown outcome until recent
chat history is checked. The tool does not retry sends automatically.

For an SSH/headless machine, a browser on another machine cannot use this loopback page
without a deliberately configured tunnel. Run the normal setup locally. The server uses
local stdio MCP, not HTTP/SSE. The account must already be authorized before MCP starts;
the model cannot run the interactive login flow.

Telethon's QR login API: https://docs.telethon.dev/en/stable/modules/client.html#telethon.client.auth.AuthMethods.qr_login

After successful login, `/telegram` disappears from command suggestions. The saved login is
reused automatically. You can still type `/telegram` manually to reconnect or switch accounts.
Ordinary tasks never require this command.
