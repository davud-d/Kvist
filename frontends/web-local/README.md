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

From a source checkout:

```bash
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
