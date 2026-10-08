# Kvist Local Web

`kvist-web` is a local control plane for Kvist workspaces, durable sessions,
concurrent agents, and SSH-backed remote agents. It is a frontend over the
existing Kvist core, not a second agent implementation.

## Run

From an installed package:

```bash
kvist-web
kvist-web --no-open
kvist-web --host 127.0.0.1 --port 9671
kvist-web --status
kvist-web --stop
```

From a source checkout, install the project into the same virtual environment
as the controller. This is required because agent processes start in the selected
workspace, where the repository is not automatically on Python’s import path.
After installing the project requirements:

```bash
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
.venv/bin/python kernel/__main__.py --web-controller
```

The default address is `http://127.0.0.1:9671`. A non-loopback bind is refused
unless both `--allow-remote` and `--access-token VALUE` are supplied. In that
mode the browser uses HTTP Basic authentication; the value is the password.
The token is never put in a URL.

## Architecture

```text
browser (HTTP + SSE, cookie + CSRF)
  -> local WebController
      -> LocalRuntime
          -> one lazy `kvist --frontend stdio-multi` per active session
          -> SessionStore / session event journal
      -> RemoteRuntime
          -> verified SSH connection
              -> `kvist_web.bridge_cli rpc` (fixed stdin/stdout protocol)
                  -> detached loopback-only remote bridge
                      -> LocalRuntime + independent session processes
```

The controller loads only configuration, workspace/session metadata and
durable history at startup. Building the LLM client and executor happens only
when the first prompt is sent to a session. Selecting a workspace or session
does not own, stop, or suspend a process.

The browser is a replaceable client of the versioned HTTP/SSE boundary. The
complete contract is documented in [`API.md`](API.md), and every browser
transport call is centralized in `kvist_web/client/utils/api.js`; UI components have no
dependency on controller, subprocess, SSH, or storage implementations.

The browser receives transient streaming events through SSE. Significant
events are replayed from Kvist's existing append-only
`.kvist/sessions/<name>.events.jsonl` journal. Every record already has a
monotonic `seq`; `/api/v1/history?after=N` is the recovery path after any browser,
SSE, SSH, or controller reconnect. The web state stores only
`last_read_event_id`, never a duplicate transcript.

## Remote lifecycle

An SSH connection may use an alias from `~/.ssh/config`, SSH agent, key file,
or password. Unknown host keys stop with a SHA-256 fingerprint. The user must
verify and explicitly accept it; accepted keys are written to the normal
`~/.ssh/known_hosts` file. Host-key verification is never globally disabled.

Bootstrap detects the remote OS and Python, checks the Kvist version, installs
or upgrades only when missing/incompatible, starts the bridge if needed, and
checks the bridge protocol version. It is safe to repeat. The bridge binds an
ephemeral **remote loopback** port and keeps its authentication descriptor in
`~/.kvist/web-bridge/` with private permissions. SSH invokes only a fixed RPC
proxy command; no public remote port is opened.

The remote bridge, not the SSH connection, owns agent processes. Therefore:

1. SSH may disconnect or the local computer may power off.
2. The remote bridge continues draining core events and the Kvist core keeps
   writing the session journal.
3. A later `kvist-web` reconnects, reconciles process state, and requests all
   journal records after the browser's last durable sequence.

General Kvist preferences synchronize automatically after bootstrap. API
credentials require an explicit checkbox selection in **Settings**. They move
inside authenticated SSH stdin and never through argv, URLs, frontend dumps,
or logs. SSH passwords use the OS keyring; if no keyring backend is available,
they remain in controller memory for that run instead of falling back to a
plaintext config file.

## Native automatic startup

```bash
kvist-web --install-system
kvist-web --uninstall-system
```

- Linux: `~/.config/systemd/user/kvist-web.service`, `Restart=on-failure`, no
  browser. It starts at login. Kvist does **not** enable systemd linger; use
  `loginctl enable-linger "$USER"` yourself only if you understand and want
  pre-login/background user services.
- macOS: `~/Library/LaunchAgents/ai.kvist.web.plist`, a background LaunchAgent
  with dedicated log files and no browser.
- Windows: a Task Scheduler task named `Kvist Web`, installed from an owned XML
  file under `%LOCALAPPDATA%\Kvist\web`, triggered at user logon.

Uninstall removes only those exact Kvist-owned units/tasks and files. Repeated
install and uninstall operations are idempotent. Each native entry pins both
its working directory and its initial Kvist workspace to the account home, so
login never accidentally starts from `/`, a temporary installer directory, or
`C:\\Windows\\System32`.

## Modules

```text
frontends/web-local/
├── kvist_web/
│   ├── client/         package-owned static browser client
│   │   ├── features/   navigation, session reducer, palette
│   │   ├── state/      small browser state store
│   │   └── utils/      authenticated API client
│   ├── bridge/         detached supervisor service and socket client
│   ├── installers/     Linux, macOS and Windows autostart backends
│   ├── runtime/        runtime interface, local and remote implementations
│   ├── ssh/            transport, trust flow and bootstrap
│   ├── controller.py   control-runtime orchestration
│   ├── events.py       bounded transient event fan-out
│   ├── http_api.py     HTTP/SSE and browser security boundary
│   ├── processes.py    independent lazy session supervisors
│   ├── protocol.py     typed controller/bridge messages
│   ├── security.py     CSRF, redaction and OS keyring
│   └── storage.py      web-only preferences and read cursors
└── tests/
```

## Verify

```bash
.venv/bin/python -m pytest -q frontends/web-local/tests
.venv/bin/python -m pytest -q kernel/tests/test_session_events.py kernel/tests/test_session_store.py
.venv/bin/python -m pytest -q scripts/installer/tests
.venv/bin/python -m ruff check .
.venv/bin/python -m compileall -x '\.agent-workspace/|/workspace/' -q .
```

For a credential-free browser smoke test:

```bash
kvist-web --no-open
curl http://127.0.0.1:9671/health
```

Sending a real agent prompt needs the same provider configuration Kvist CLI
already uses. Remote bootstrap needs Python 3.11+ and the matching Kvist release
available to remote `pip`; bootstrap reports a clear error without replacing a
working installation when that prerequisite is unavailable.

## Browser interface

The desktop layout is a session sidebar and one conversation canvas. Execution
selection (Local or an SSH server) and model information live in the composer.
Files, Diff, Terminal, Activity, Context and Usage open temporary dialogs; Escape
returns to the conversation. Ctrl/Cmd+K opens commands; Ctrl/Cmd+P searches sessions
and workspaces. Read receipts advance only after viewing the conversation tail.

The frontend remains native ES modules without a build step or runtime UI
framework. `features/connections.js` owns server/workspace dialogs,
`features/views.js` owns temporary session tools, and `utils/ui.js` provides shared
accessible controls and safe Markdown. The transport adapter
retains the v1 API and now renews expired browser credentials without reloading.

The composer opens `features/configuration.js`: provider/model discovery and
first-run AnyModel registration use the Python core services on the selected host.
POST `/api/v1/configuration` saves defaults; `/api/v1/configuration/models` fetches
the catalog without saving credentials. `/api/v1/command` forwards only model and
reasoning commands to an idle session. GET `/api/v1/ssh-aliases` lists concrete
Host aliases using the same parser as the existing SSH transport (no Include
expansion). Private SSH keys still use paths on the controller machine. Files and Terminal show session-reported data rather than arbitrary file
browsing or an interactive shell. See [design.md](design.md) for the design and
contract audit, and [browser tests](tests/browser/README.md) for visual regression
commands and coverage.

Older remote bridges keep working for sessions. The provider dialog requests the
`web_configuration` capability before using the new API; when absent it asks to
update that server, without interrupting its tasks or forcing an upgrade.

## Custom server setup and live logs

You can add a standalone, user-authored `kvist_web/ssh/setup_server.py` beside
`ssh/config.py`. The Web frontend does not generate that script. On an explicit
Connect, after SSH authentication and Python 3.11+ discovery, it uploads the file
to a private temporary server location and runs it with the discovered Python:
`python -u <uploaded-path> --mode <ssh-config|password|key|agent>
--connection-json <uploaded-json>`. Its stdout and stderr stream into the
connection dialog. Return exit code 0 to continue the existing Kvist version,
installation and bridge checks; a nonzero exit stops connection setup. The script
has a ten-minute timeout. Uploaded temporary files are cleaned up afterwards.

The connection JSON contains the server id, display name, host, port, username,
SSH alias and auth method. `ssh-config` mode is for a selected `~/.ssh/config`
alias and includes `ssh_alias`; `password` mode includes the submitted password;
`key` mode includes `private_key_path`; `agent` mode covers manual host/user
connections that rely on the local SSH agent or default keys. The password is
not placed on the remote process command line.

If the file is absent, standard bootstrap continues. Automatic reconnects do not
run the custom script. Adding it later takes effect on the next explicit Connect;
its contents remain yours to write. The script should be self-contained and print
log lines with newlines (Python runs unbuffered).

Connection logs use the existing authenticated SSE transport and are separate
from conversation history. Each browser attempt has its own ID. Output is redacted
and limited to the latest 64 KiB; the final POST response includes a bounded tail
for recovery when live output was interrupted. The dialog retains the finished
log until Open workspace or Escape, instead of disappearing on success.
