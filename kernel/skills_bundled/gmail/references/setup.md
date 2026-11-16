# Gmail browser connection

Enter this once inside Kvist on macOS, Linux or Windows:

```text
/gmail
```

The command installs the pinned community Gmail server in Kvist's private configuration
folder, asks for your Google OAuth Desktop app JSON path, opens the system browser for
Google consent, verifies the Gmail account and saves the MCP connection. It needs Node.js
with npm. For the initial Google setup, enable the Gmail API in a Google Cloud project,
configure its consent screen (add yourself as a test user if appropriate), and download
OAuth credentials of type **Desktop app**. Kvist does not ship a shared Google OAuth app.

The login uses a loopback callback, state and PKCE. The command prints a fallback link if
the OS browser launcher fails. Open it on the same computer while the command is running.
The authorization expires after five minutes. Closing or declining it does not replace an
existing connection. Refresh credentials are stored privately outside the workspace.

After connection, use this inside Kvist:

```text
Найди последние письма от Анны в Gmail и подготовь ответ
```

The agent selects the skill and attaches its MCP server automatically for the task; first attachment shows the usual
server-tool approval. Reading and saved drafts need no separate send permission.
**Every send** (`send_email`, `send_draft`, `reply_all`) needs a separate human confirmation
in Kvist, even with automatic approval enabled. Before sending a saved draft, read and show
its current recipients and content so the person can review what its ID refers to. A denied
send does not reach the server; unattended runs cannot send through these tools.

The skill is bundled. If you previously removed it, restore it with:

```text
kvist --skill-op install --skill-path bundled:gmail
```

Restart after restoring a removed skill. A connection created outside an ongoing task is
available to its next task. Enter /gmail again to reauthorize or change accounts.
`kvist --mcp-op list` shows registration, not proof that an expired login still works.
`kvist --mcp-op remove --mcp-name gmail` removes registration; revoke access in your Google
account if you also want to invalidate previously issued tokens.

## Scope and troubleshooting

The connector requests `gmail.modify` for reading, drafts and mailbox operations; it does
not request settings or permanent-delete scope. Sending is separated by Kvist's runtime
confirmation gate, not by OAuth (Google's modify scope includes sending).

This integration uses [@artymclabin/gmail-mcp 1.2.4](https://github.com/ArtyMcLabin/Gmail-MCP-Server).
Its Node process runs with the OS user's rights (`sandbox: none`); it is not isolated by
`workspace: none`. Login and runtime files are under `<config_dir>/plugins/services/gmail`.
Do not copy these credentials into a prompt or repository. The managed command sets exact
credential paths and a private working directory; it does not depend on a shell's PATH or
current project directory.

Missing Node/npm: install them, then reconnect. Google denied access: check the Desktop
client type, test users and Gmail API enablement. An expired refresh token requires browser
consent again. Network or installation failures are reported in the Kvist interface. No browser
on an SSH/headless machine: run setup on the machine where Kvist and your browser run;
a remote browser cannot access another machine's loopback listener automatically.

The generic `--mcp-op add` remains available for other servers. Set `confirm_tools` to every
sending tool's exact raw MCP name, and keep those names out of `readonly_tools`. Those
fields are person-managed policy. Tools absent from the attached schemas cannot be called;
a changed tool catalog requires a new attachment approval.

Google's desktop flow: https://developers.google.com/identity/protocols/oauth2/native-app

After successful login, `/gmail` disappears from command suggestions. The saved login is
reused automatically. You can still type `/gmail` manually to reconnect or switch accounts.
Ordinary tasks never require this command.
