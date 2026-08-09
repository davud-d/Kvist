# Kvist UI API — building frontends

*(Русская версия: [UI_API.ru.md](UI_API.ru.md))*

This document is the **complete contract** for putting any frontend on top of the
Kvist agent core — a terminal, a web app, an Electron/JS GUI, a Telegram bot, or
an IDE plugin. It is written to be sufficient on its own: a person or an AI can
implement a frontend from this file alone, **without reading the Python core**.

---

## 1. The idea

The agent core is frontend-agnostic. It never touches the terminal directly. It
only:

- **emits** what it produces — live status, plan/step reasoning, streaming text,
  the final answer, errors; and
- **asks** for what it needs — a menu choice, a line of text, a secret, or a
  yes/no confirmation of a dangerous command — and **blocks** until answered.

The terminal is just the default frontend. Everything else is a thin adapter over
the same contract. There are two ways to consume it.

---

## 2. Two integration modes

### Mode A — implement the protocols in-process (same Python process)

Write a class with the `AgentUI` methods (§5) and, if you handle dangerous
commands, an `ApprovalGate` (§6), then inject them:

```python
agent    = LocalAgent(client, executor, config, ui=my_ui)   # agent.py
executor = SafeExecutor(workspace, approvals=my_gate)        # executor.py
```

`main.build_agent(..., ui=my_ui)` also accepts an injected UI and reuses
provider/model/credential resolution. This mode is **synchronous**: the agent
calls your methods on its own thread, so input methods must block until the user
answers. Good for a Python GUI that already runs the agent on a worker thread.

### Mode B — the event stream (recommended for GUI / web / Telegram / IDE / other languages)

Turn the boundary into **data**: the core emits JSON-serializable events and reads
JSON responses. This is what separate-process and non-Python frontends want. The
pieces (all stdlib, no extra dependencies):

- `frontend_protocol.py` — the typed events (each has `to_dict()`) + `PROTOCOL_VERSION`
  + a thread-safe `FrontendChannel`.
- `event_stream_ui.EventStreamUI(channel)` — an `AgentUI` that emits events;
  input methods block on `channel.request(...)`.
- `event_session.EventSession(channel, agent)` — runs `agent.run(...)` on a worker
  thread and exposes the events; `EventApprovalGate(channel)` turns confirmations
  into events too.
- `event_stdio.py` — a ready-made newline-delimited-JSON transport.
- `frontends.py` — the universal `--frontend NAME` dispatcher (§9).

**A non-Python frontend does not import any of this.** It just launches the core
as a subprocess and speaks JSON (§8).

---

## 3. Launching a frontend

```bash
kvist --workspace /path/to/project --frontend stdio      # event stream over stdio
kvist --workspace /path/to/project                       # default terminal REPL
```

Optional per-run stdio overrides:

```bash
kvist --workspace /path/to/project --frontend stdio \
  --provider omniroute \
  --fast-model ds-web/deepseek-v4-flash \
  --smart-model ds-web/deepseek-v4-pro \
  --planner-model anthropic/claude-sonnet-4.6 \
  --role devops \
  --approval-mode ask
```

Model overrides are atomic: supply all three model flags or none. `--role` is
one of `auto`, `coder`, `devops`, or `chat`. `--approval-mode` is `ask` or
`deny`; there is deliberately no unsafe `allow-all` mode.

- `--frontend terminal` (default) → the interactive terminal REPL.
- `--frontend stdio` → newline-delimited JSON on stdout/stdin (§8).
- `--frontend <name>` → an external frontend `kvist_frontends/<name>.py` (§9).

(During development, without the built binary, use
`.venv/bin/python main.py --workspace … --frontend stdio`.)

---

## 4. Protocol version & compatibility rules (READ THIS)

- The **first event on every stream is `hello`**, carrying `protocol_version`
  (currently `1`). Check it; refuse or adapt on mismatch.
- **`protocol_version` bumps only on a breaking change** to existing event/response
  shapes.
- **Adding a new event `type` is NOT breaking.** A frontend **MUST ignore event
  types it does not recognise** (forward-compatibility). Switch on `type` and let
  unknown types fall through.
- Fields may be *added* to an existing event without a version bump; do not assume
  an exact key set — read the keys you need.

---

## 5. `AgentUI` — the output + input surface (`ui.py`)

A structural `typing.Protocol` (no subclassing needed). Methods, grouped:

**Live status / reasoning (fire-and-forget):**
`start_live()`, `stop_live()`,
`update_live(user_message, status, step, progress=0, plan_step="working", is_error=False)`,
`print_plan(plan_info)`, `print_step_header(step, command)`, `debug_print(message)`.

**Responses:**
`print_final(final_message, user_messages=None)`, `print_user_markdown(message)`,
`print_result(message)`, `print_error(message)`, `print_sandbox_notice(note)`,
`render_user_markdown(message, fallback_style="") -> str`,
and streaming via `live_markdown()` — a context manager yielding an
`update(message)` callback (each call replaces the whole rendered response with
the new cumulative text).

**Input (blocking, return the answer):**
`choose_option(title, options, current_value="", help_text="") -> str | None`,
`prompt_text(title, current_value="", help_text="") -> str | None`,
`prompt_secret(title, current_value="", help_text="") -> str | None`.

**Attribute:** `verbose: bool`.

Defaults: `terminal_banner.TerminalBannerUI` (Rich; degrades to a plain log stream
without a TTY).

---

## 6. `ApprovalGate` — human-in-the-loop confirmations (`approvals.py`)

Structural `Protocol` with two methods:

- `confirm_command(command: list[str], reason: str) -> bool` — a **dangerous shell
  command** (argv + reason).
- `confirm(title: str, body: str, question: str) -> bool` — a **file overwrite** or
  policy prompt.

Default `TerminalApprovalGate` renders a prompt and reads y/N; it is **fail-closed
off a TTY** (raises rather than silently allowing). Inject a different gate via
`SafeExecutor(workspace, approvals=...)`. In the event stream, `EventApprovalGate`
turns each call into an `input_request` of `kind == "confirm"` and returns
`bool(approved)` — so a disconnected/closed frontend **denies** (stays fail-closed).

---

## 7. The event catalog

Every event is a JSON object with a `"type"` field. Outbound (core → frontend):

| `type` | Fields | Meaning |
|---|---|---|
| `hello` | `protocol_version:int`; optional `provider:str, models:object, role:str, approval_mode:str` | handshake and effective run configuration; always first |
| `live_start` / `live_stop` | — | live status begins / ends |
| `status` | `user_message:str, status:str, step:int, progress:int, plan_step:str, is_error:bool` | per-step progress |
| `step` | `step:int, plan_step:str, thought:str, user_message:str, action_type:str, action_args:object, progress:int` | the action chosen for a step |
| `plan` | `goal:str, complexity:str, plan:string[], success_criteria:string[], risks:string[]` | the task plan |
| `sandbox_notice` | `note:str` | one-time sandbox honesty notice |
| `stream_start` | `stream_id:str` | a streaming response begins |
| `stream_chunk` | `stream_id:str, text:str` | current cumulative text (a **snapshot**, not a delta) |
| `stream_commit` | `stream_id:str, text:str` | final streamed text (once) |
| `message` | `markdown:str` | a non-streamed Markdown message |
| `final` | `message:str, user_messages:string[]` | the run's final answer (terminal event) |
| `debug` | `message:str` | verbose diagnostics |
| `error` | `message:str` | an error line (also emitted if the run raises) |

Inbound request / response:

| `input_request` field | Notes |
|---|---|
| `request_id:str` | correlate your reply with this |
| `kind:str` | one of `choice`, `text`, `secret`, `confirm` |
| `title:str` | short prompt |
| `body:str` | longer text (e.g. a diff, or argv+reason for `confirm`) |
| `options:object[]\|null` | for `choice`: `[{"value","label"[,"description"]}, …]` |
| `current_value:str` | current/default value (empty for `secret`) |
| `help_text:str` | optional hint |

**Your reply is one JSON object:**

- `choice` / `text` / `secret` → `{"request_id": "...", "value": "<answer>"}`
  (or `"value": null` to cancel).
- `confirm` → `{"request_id": "...", "approved": true|false}`.

**Redaction & secrets:** outbound text fields are secret-redacted before they enter
the stream. A `secret` value is **never emitted outbound** — it travels only inbound
as your `value`. Do not log secret responses.

---

## 8. The stdio-JSON transport (`--frontend stdio`)

The cross-process, cross-language boundary. Rules:

- **Framing:** one JSON object per line (newline-delimited JSON / NDJSON), UTF-8.
- **Direction:** events → **stdout**; your responses → **stdin** (one JSON object
  per line).
- **The goal** is the **first line you write to stdin** (the parent drives the run),
  unless passed on the command line.
- **stdout is pure JSON.** The core redirects everything else (logs, stray prints,
  warnings) to **stderr**, so you can parse stdout line-by-line safely.
- **Ordering:** stdout is strictly ordered. The first line is always `hello`; a run
  ends with a `final` (or `error`) event, after which the stream closes.
- **Blocking model:** the agent runs on a worker thread and blocks on each
  `input_request` until it sees a matching `input_response`. If you never answer, it
  waits; if the process/stream closes, pending confirmations **deny** (fail-closed).

### Sample transcript (real capture; `>>>` = stdout, `<<<` = your stdin)

```
<<< do the thing
>>> {"type": "hello", "protocol_version": 1, "provider": "omniroute", "models": {"fast": "ds-web/deepseek-v4-flash", "smart": "ds-web/deepseek-v4-pro", "planner": "anthropic/claude-sonnet-4.6"}, "role": "devops", "approval_mode": "ask"}
>>> {"type": "status", "user_message": "Choosing provider", "status": "working", "step": 1, "progress": 10, "plan_step": "inspect", "is_error": false}
>>> {"type": "input_request", "request_id": "b7dca6b0…", "kind": "choice", "title": "Pick provider", "body": "", "options": [{"value": "openai", "label": "OpenAI"}, {"value": "freeflow", "label": "FreeFlow"}], "current_value": "", "help_text": ""}
<<< {"request_id": "b7dca6b0…", "value": "freeflow"}
>>> {"type": "final", "message": "picked freeflow …", "user_messages": []}
```

---

## 9. Universal frontend dispatch (`frontends.py`)

All non-terminal frontends share the same wiring (`FrontendChannel` →
`EventStreamUI` → `SafeExecutor(approvals=EventApprovalGate)` → `build_agent(ui=…)` →
`EventSession`), so `frontends.py` builds the session once and hands it to a
**launcher** — any `launch(session)` callable.

- Built-in: `stdio`.
- **Add a frontend = one file, no core edit.** Create `kvist_frontends/<name>.py`
  exposing `def launch(session): …`, then run `kvist --frontend <name>`. The module
  is imported **lazily** (a heavy toolkit like Qt/aiohttp loads only when selected).

A launcher receives a ready `EventSession` and drives it:

```python
def launch(session):
    session.submit(goal)                 # start the run (worker thread)
    for event in session.events():       # blocks/polls until the run ends
        data = event.to_dict()
        if data["type"] == "input_request":
            session.respond(data["request_id"], value=...)      # or approved=True/False
        else:
            render(data)
    session.close()
```

`EventSession` API: `submit(user_goal, routed_role=None)`, `events()` (iterator; ends
when the worker finishes and the queue drains), `respond(request_id, *, value=None,
approved=None)`, `close(timeout=5.0)`, `is_running()`.

---

## 10. Another language (Electron / JS / anything)

The JSON boundary means the "beauty" can be in any language while the Python core
runs as a **child process** — the standard "engine behind stdio-RPC" pattern (like
LSP/DAP/git). Your app never imports Python; it only speaks the JSON in §7–§8.

```javascript
const { spawn } = require("child_process");
const readline = require("readline");

const proc = spawn("kvist", ["--workspace", ws, "--frontend", "stdio"]);
proc.stdin.write("do the thing\n");                 // the goal = first stdin line
readline.createInterface({ input: proc.stdout }).on("line", (line) => {
  const ev = JSON.parse(line);
  switch (ev.type) {
    case "hello":        checkVersion(ev.protocol_version); break;
    case "stream_chunk": appendStreaming(ev.text);          break;  // live typing effect
    case "status":       showStatus(ev);                    break;
    case "final":        showFinal(ev.message);             break;
    case "error":        showError(ev.message);             break;
    case "input_request":
      askUser(ev).then((answer) => {
        const reply = ev.kind === "confirm"
          ? { request_id: ev.request_id, approved: answer }
          : { request_id: ev.request_id, value: answer };
        proc.stdin.write(JSON.stringify(reply) + "\n");
      });
      break;
    // default: ignore unknown event types (forward-compat)
  }
});
```

**Packaging:** build the core into a frozen binary (`kvist.spec` / PyInstaller) and
ship it inside your app — end users then need no Python at all. Electron just spawns
the `kvist` binary.

**Browser (not Electron):** stdio does not apply; write a socket/websocket launcher
(`kvist_frontends/websocket.py`) that pumps the same `FrontendChannel` over a socket.
The core does not change — only the transport.

---

## 11. Notes, limits, and guarantees (v1)

- **Credentials.** `build_agent` resolves provider credentials up front (from
  `~/.kvist/config.json`); a headless frontend needs them **pre-configured**.
  Credential entry over events is not wired yet — configure the provider once via the
  terminal, or ship a config. If a provider/model is missing and there is no TTY, the
  core exits with a non-zero status and a message on stderr (fail-closed).
- **Approvals are fail-closed.** With no injected gate you get terminal prompts; over
  events, a closed/silent frontend denies dangerous commands. `--approval-mode deny`
  rejects every approval request without showing a prompt.
- **The agent loop is unchanged.** Frontends never call agent internals — they only
  render events and answer requests.
- **The terminal is a special default.** Its REPL adds slash-commands, sessions, and
  config wizards beyond the event surface; those are not (yet) exposed as events.
- **One process = one run/session** in v1. For multiple concurrent clients, run one
  process per session or write a multiplexing socket transport.

---

## 12. Files

Core seam: `ui.py`, `approvals.py`, `terminal_banner.py`.
Event protocol: `frontend_protocol.py`, `event_stream_ui.py`, `event_session.py`,
`event_stdio.py`, `frontends.py`.
Tests: `tests/test_frontend_protocol.py`, `tests/test_event_stream_ui.py`,
`tests/test_event_session.py`, `tests/test_frontends.py`.
