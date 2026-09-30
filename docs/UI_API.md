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
  --role devops \
  --approval-mode ask
```

Model overrides are atomic: supply both model flags or neither. `--role` is
one of `auto`, `coder`, `devops`, or `chat`. `--approval-mode` is `ask` or
`deny`; there is deliberately no unsafe `allow-all` mode.

- `--frontend terminal` (default) → the interactive terminal REPL.
- `--frontend stdio` → newline-delimited JSON on stdout/stdin (§8).
- `--frontend <name>` → an external frontend `kvist_frontends/<name>.py` (§9).
- `--frontend stdio --resume` → resume the active session's checkpoint instead
  of submitting a goal (no stdin goal line is read; §8a).
- `--session-op …` → one headless session lifecycle operation, JSON out (§8a).
- `--commit-message` → a commit message for a change payload on stdin, JSON
  out (§8b).
- `--acp` → the Agent Client Protocol on stdin/stdout, for hosts such as Zed
  (§8c). The workspace arrives with `session/new`, so `--workspace` is not used.
  `--login` is valid only with it: configure provider credentials in that
  terminal and exit. This mode is not an NDJSON event stream and does not use
  `PROTOCOL_VERSION`; it is a JSON-RPC peer, and what it can and cannot do for
  a given host is recorded in [`frontends/zed/README.md`](../frontends/zed/README.md).

(During development, without the built binary, use
`.venv/bin/python -m kernel --workspace … --frontend stdio`.)

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
`update_live(user_message, status, step, progress=0, plan_step="working", is_error=False)`
(`progress` is derived by the runtime from the phase of the response's calls; the model never reports one),
`print_plan(markdown)` (the plan the model proposes, before the person is asked to accept it), `print_step_header(step, command)`, `debug_print(message)`.

**Responses:**
`print_final(final_message, user_messages=None)`, `print_user_markdown(message)`,
`print_result(message)`, `print_error(message)`, `print_sandbox_notice(note)`,
`render_user_markdown(message, fallback_style="") -> str`,
and streaming via `live_markdown()` — a context manager yielding an
`update(message)` callback (each call replaces the whole rendered response with
the new cumulative text). The callback may also carry `update.reasoning(message)`,
a second channel that streams the model's cumulative thinking text; callers probe it
with `getattr(update, "reasoning", None)` so UIs without a reasoning channel still work.

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
| `hello` | `protocol_version:int`; optional `provider:str, models:object, role:str, approval_mode:str, session:str, has_checkpoint:bool, effort:str, commands:string[]` | handshake and effective run configuration; always first. `effort` (the reasoning-effort level) and `commands` (the names `command` accepts, `resume` included) are sent by `--frontend stdio-multi`. |
| `manifest` | `roles:object[], effort_levels:object[], providers:object[], model_roles:string[], default_models:object, action_types:string[], slash_commands:object[], api_settings:object[], styles:object, themes:object` | UI-facing metadata catalog built by `core_manifest.build_manifest`; sent once after `hello` so frontends can populate pickers dynamically without hardcoding. |
| `live_start` / `live_stop` | — | live status begins / ends |
| `status` | `user_message:str, status:str, step:int, progress:int, plan_step:str, is_error:bool` | per-step progress; during a call `user_message` is the call's label built by the core ("reads src/x.py") |
| `step` | `step:int, plan_step:str, thought:str, user_message:str, action_type:str, action_args:object, reasoning_seconds:number, action_description:str, call_id:str, batch_id:str` | one tool call of a model response. `reasoning_seconds` is how long the model spent thinking before it (0 when it did not think). `action_description` is the call's label, built by the core from its arguments ("reads src/x.py", `search "role" in src/`; a `run_shell` label is the purpose the model gave). `batch_id` names the model response the call came from: the calls of one response share it and may be shown as one group. `call_id` names the call; its `action_result` carries the same one. `thought` is always `""` (the model no longer writes one). `user_message` is the response's own text for the person on the first `step` of its batch and `""` on the others, so a frontend that shows it per step shows it once. |
| `action_result` | `step:int, action_type:str, ok:bool, summary:str, output:str, exit_code:int\|null, path:str\|null, first_line:int\|null, call_id:str, batch_id:str` | the outcome of one executed action (match it to its `step` by `call_id`, or by `step`). Read-only calls of one response run in parallel, so several `step` events can arrive before their results; the results still arrive in call order. A writing call skipped because an earlier write of the same response failed gets `ok:false` and the summary "Skipped: an earlier write failed". `summary` is one short line ("Wrote 3 lines", "Command exited with code 0", "2 matches"). `output` is the redacted command output (`run_shell`, `git_status`, `git_diff`, `delegate_task`) or the error text of a failed action; it is capped at 4000 characters, keeping the head and the tail with an `[... N characters omitted ...]` marker between them. `exit_code` is set for `run_shell` only (`null` when denied or timed out). `path` and `first_line` are set for `write_file`/`append_file`/`replace_in_file`: `first_line` is the 1-based line of the resulting file where the written text starts (`1` for `write_file`, the first line of the appended text for `append_file`, the first replaced occurrence for `replace_in_file`) so a diff can be numbered; it is `null` for a failed write. Not sent for `final_answer`/`report_to_user`/`refuse`, nor for actions a runtime guard rejected before they ran (repeat, read-only or corrective-action blocks); a web-search/URL budget refusal is sent as `ok:false`. |
| `action_output` | `step:int, output:str, call_id:str` | live output of a running `run_shell` command, sent while it runs and before its `action_result`. `output` is a **snapshot** of the most recent output (at most 4000 characters, redacted as a whole), not a delta, so replace what you show. Events come at most about every 120 ms, and the last one is never dropped. It is a preview only: the complete, capped output still arrives with the `action_result`, which ends the live output for that `step`. A command that prints nothing sends none. |
| `route` | `role:str, source:str` | the role the turn actually runs under, sent once per turn before the plan or reply. `role` is `coder`, `reader`, `devops` or `chat`. `source` is `explicit` (the caller passed the role), `direct` / `llm` / `keyword_fallback` / `default` (how the router decided) or `resume` (a bare "continue" after a stopped run inherited that run's role). |
| `plan` | `markdown:str` | the plan the model proposes for the person's decision (plan mode); short Markdown |
| `sandbox_notice` | `note:str` | one-time sandbox honesty notice |
| `stream_start` | `stream_id:str` | a streaming response begins |
| `stream_chunk` | `stream_id:str, text:str` | current cumulative answer text (a **snapshot**, not a delta) |
| `stream_commit` | `stream_id:str, text:str` | final streamed answer text (once) |
| `reasoning_start` | `stream_id:str` | the model started thinking; same `stream_id` as the answer it precedes |
| `reasoning_chunk` | `stream_id:str, text:str` | current cumulative **thinking** text (a snapshot, separate channel from the answer) |
| `reasoning_commit` | `stream_id:str, duration_seconds:number` | thinking ended (the answer is about to stream); carries how long it took |
| `message` | `markdown:str` | a non-streamed Markdown message |
| `final` | `message:str, user_messages:string[], outcome:str` | the run's final answer (terminal event). `outcome` is `"done"` when the run finished and passed its completion checks, `"incomplete"` otherwise (an honest "not done", a stop, the step budget, a refusal). |
| `usage` | `input_tokens:int, output_tokens:int, context_tokens:int, context_limit:int, model:str, kind:str, step:int` | token usage of one completed model call. `input_tokens`/`output_tokens` are **spend**: sum them over the run. `context_tokens` is the **size of that one prompt** — the conversation itself — so *replace* the previous value instead of summing (it is 0 for calls that do not carry the conversation, e.g. `kind` `report`: keep the last known size). Because every step resends the context, the summed spend runs far ahead of `context_tokens` — showing the sum as "tokens" reads as a huge conversation that does not exist. `context_limit` is the budget the core trims a prompt to. |
| `debug` | `message:str` | verbose diagnostics |
| `error` | `message:str` | an error line (also emitted if the run raises) |
| `turn_end` | — | `--frontend stdio-multi` only: the turn ended (after `final`, `error`, or a chat reply that has no `final`); the process stays up for the next goal |
| `session` | `session:str, has_checkpoint:bool`; optional `closed:true, closed_notice:str` | `--frontend stdio-multi` only: the first event of a `resume` turn — the session the interrupted run continues in, which may differ from the one in `hello` — and the session a `session`/`new` command switched to (§8c). `closed` and `closed_notice` are present only for a session `end_session` closed (§8a, "Closed sessions"): show `closed_notice` in place of the input box. |
| `session_ended` | `session_id:str, warned_msg_id:int, closed_msg_id:int, reason:str, closed_notice:str` | the model closed the conversation for good with `end_session` (§8a, "Closed sessions"). Sent during the turn, before its `final`, which carries the closing words for the person. `session_id` is the session name; `warned_msg_id`/`closed_msg_id` are the numbers of the person's messages that got the warning and that closed it; `reason` is the model's one-line reason, for logs rather than for the person; `closed_notice` is the core's text for the place of the input box, in the session's language. Recorded in the session's journal. |
| `session_title` | `session:str, title:str` | a session's title became known: generated in the background from its first completed turn (never the first user message — see §8a), or set by hand with the `rename` command (§8c). Additive, out-of-band — it can arrive well after the turn's `turn_end`, and `session` may name a different session than the one currently active if the user switched sessions while generation was still running. |
| `command_result` | `name:str, ok:bool, message:str, effects:object` | `--frontend stdio-multi` only: the outcome of an inbound `command` message (§8c). It is the last event of that command; **no `turn_end` follows it**. |

### Thinking / reasoning models

Some models "think" before answering, sometimes for minutes. Kvist surfaces that
thinking on a **separate channel** so it never contaminates the answer:

- **Detection is dynamic, not a static flag.** A frontend learns the model is
  thinking the moment a `reasoning_start` arrives — not from the model name. If the
  answer streams with no `reasoning_*` events, the model simply did not think; render
  it as usual.
- **The reasoning `stream_id` equals the answer's `stream_id`.** Attach the thinking
  UI to the reply it precedes.
- **Ordering** for a thinking reply: `stream_start` → `reasoning_start` →
  `reasoning_chunk`* → `reasoning_commit` → `stream_chunk`* → `stream_commit`.
  `reasoning_commit` is always emitted before the first `stream_chunk`, so thinking
  text and answer text never interleave. A typical GUI shows a live grey block while
  thinking, then collapses it into a "Thought for Ns" label (from `duration_seconds`)
  once the answer begins.
- **Capture is passive and provider-agnostic**, and only happens when streaming is on.
  Kvist reads whatever reasoning tokens the provider already emits (`reasoning` /
  `reasoning_content`); it does not request extra thinking. Note that OpenAI's own
  reasoning models (o-series) do **not** stream the thinking text over
  `chat.completions` — you will get `reasoning_commit` (the elapsed time) but the grey
  block stays empty, which is honest.
- In the structured coder/devops loop the model's thinking for each step is streamed
  the same way, but as a stream of its own: `reasoning_start` → `reasoning_chunk`* →
  `reasoning_commit`, all with one `stream_id` that has **no** `stream_start` and no
  answer. The commit comes when the model starts writing the action, and the `step`
  event that follows carries `reasoning_seconds: 0` because the time was already
  reported. A step whose model did not think keeps its `reasoning_seconds` on the `step`
  event as before.

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

**Plan mode.** The model may offer a plan before it changes anything, and it does
so through the dialogs above, never through a dedicated request:

1. `input_request` `choice` — the offer. `title` is the model's reason plus one
   line saying a plan first is safer and starting right away is faster but riskier;
   the two `options` have the values `plan` and `go`. Cancelling counts as `go`.
2. `plan` event `{"markdown": str}` — the proposed plan (short Markdown). Show it
   as a card *before* the next request; it is also recorded in the session journal.
3. `input_request` `choice` titled "Accept this plan?" — values `accept` and
   `revise`. On `revise` the core sends a `text` request ("What should change in
   the plan?"); the answer goes to the model, which proposes a new plan (another
   `plan` event and another `choice`). Cancelling either dialog, or an empty
   text, ends the turn on the plan: the `final` message is the plan plus "not
   accepted" and nothing was changed.

The core asks only through interactive frontends: a frontend without `ask_choice`
(headless, `run_task.py`) never receives the offer, and the model is not told plan
mode exists. Over ACP the two-option `choice` dialogs are permission buttons; an
ACP client without form elicitation refuses the `text` question, so a refused
plan there ends the turn on the plan. Frontend
authors need nothing beyond rendering the `plan` card and the ordinary `choice`
and `text` dialogs; a long `title` must wrap instead of being cut.

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
>>> {"type": "hello", "protocol_version": 1, "provider": "omniroute", "models": {"fast": "ds-web/deepseek-v4-flash", "smart": "ds-web/deepseek-v4-pro"}, "role": "devops", "approval_mode": "ask", "session": "default", "has_checkpoint": false}
>>> {"type": "status", "user_message": "Choosing provider", "status": "working", "step": 1, "progress": 10, "plan_step": "inspect", "is_error": false}
>>> {"type": "input_request", "request_id": "b7dca6b0…", "kind": "choice", "title": "Pick provider", "body": "", "options": [{"value": "openai", "label": "OpenAI"}, {"value": "freeflow", "label": "FreeFlow"}], "current_value": "", "help_text": ""}
<<< {"request_id": "b7dca6b0…", "value": "freeflow"}
>>> {"type": "final", "message": "picked freeflow …", "user_messages": []}
```

---

## 8a. Named sessions for frontends

Every run executes in the workspace's **active named session** (conversation
history, chat history, and the resumable `/resume` checkpoint live per
session). The `hello` event reports it via two additive v1 fields:
`session` (active session name) and `has_checkpoint` (whether a resumable
checkpoint exists in it).

**Never read or write `.kvist/` files directly.** Session lifecycle management
is a separate, LLM-free one-shot mode of the same binary:

```bash
kvist --workspace WS --session-op status
kvist --workspace WS --session-op list
kvist --workspace WS --session-op history --session-name feature [--history-limit N] [--history-before SEQ]
kvist --workspace WS --session-op launch
kvist --workspace WS --session-op new
kvist --workspace WS --session-op create --session-name feature
kvist --workspace WS --session-op switch --session-name feature
kvist --workspace WS --session-op delete --session-name old-work
kvist --workspace WS --session-op reset
kvist --workspace WS --session-op input-history                    # last 100 inputs of the workspace: {"ok":true,"entries":[oldest, …, newest]}
echo "text" | kvist --workspace WS --session-op input-history-add  # UTF-8 on stdin
```

Each invocation prints **one JSON object** on stdout and exits `0` on success
or `1` on failure:

```json
{"ok": true, "active_session": "feature", "sessions": ["default", "feature"],
 "checkpoint": {"goal": "…", "step": 6, "progress": 40},
 "resumable_session": "feature", "warnings": []}
```

`checkpoint` describes the **active** session only; `resumable_session` names
whichever session holds the newest resumable checkpoint (`null` if none), which
after a `launch` is normally not the active one.

**Session titles.** The `title` in each `status`/`list` item above is derived
fresh on every call (first goal / first chat message / checkpoint goal, or the
session name). Separately, the core also generates and *stores* a short title
in the session document itself: once, in the background, from the session's
first completed turn (never the first user message alone — the model sees the
goal plus the first 20 lines of the reply). A model that does not answer in
time falls back to the user's own first message, cut to 40 characters at a
word boundary, so a session is never left untitled. `rename` (§8c) sets one by
hand and blocks the background generation for that session from then on. The
stored title (or the same fallback, computed on the fly for a session that
predates this or has none stored yet) is what the interactive `session`
command's picker shows, and what a `session_title` event (§7) announces the
moment it becomes known — which can be well after the turn that triggered it
has already ended.

```json
{"ok": false, "error": "Unknown session: 'ghost'"}
```

Semantics (identical to the terminal `/session` and `/new` commands):

- `status` — active session, session list, checkpoint summary (`null` if none).
  Each `session_items` entry carries `name`, `title`, `updated_at`,
  `has_checkpoint`, `journal_bytes` (size of the session's event journal, `0`
  if none), `closed: true` and `closed_notice` for a closed session (absent
  otherwise), and the model-facing `session_history` / `chat_history`. Those two
  are the whole history of every session, so `status` grows with the
  workspace (~570 KB for 36 sessions).
- `list` — the cheap `status`: the same payload without `session_history` and
  `chat_history` in the items. Use it for a session picker; the rest of
  `status` is unchanged and still what the Electron GUI reads.
- `history` — the event journal of one session (below); a closed session's
  payload also carries `closed: true` and `closed_notice`.
- `launch` — **run this once when your app starts.** A launch always opens its
  own fresh session instead of reopening the previous one. An interrupted run is
  not lost: `status` names the session that still holds it in
  `resumable_session`, and `--frontend stdio --resume` switches into that
  session on its own.
- `new` — create **and activate** a fresh session under a name Kvist allocates
  (the terminal `/new`). The outgoing session is kept and stays switchable.
- `create` — the same, under a name you choose; fails if it exists.
- `switch` — activate an **existing** session; a missing or corrupt target
  fails without changing the active session. Switching never creates sessions
  and never touches workspace files.
- `delete` — remove an inactive session (file + recovery copy). Deleting the
  active session is refused.
- `reset` — empty the active session in place (history, chat, checkpoint); the
  name is kept, other sessions are untouched. Unlike `new`, this **destroys**
  the conversation it is called on. A closed session is refused
  (`{"ok": false}`): emptying it would reopen it.

### Closed sessions

In an interactive frontend (the classic terminal, `--frontend stdio-multi`,
`--acp`) the model has one more tool, `end_session`, for a person who insults
it or people after a warning. The core decides everything but that one
judgement: the warning (a `message` event with fixed text), whether it is still
fresh, and the closing. A closed session:

- is **read-only for good**. The core refuses every goal and every `resume` in
  it before routing or any model call: the answer is one `final` event
  (`outcome: "incomplete"`) with a fixed reply, followed by `turn_end` in
  stdio-multi, and nothing is written to the session or its journal;
- **stays readable**: `history`, `list`, `status` and switching to it work as
  usual, and `list`/`status` items, the `history` payload and the `session`
  event carry `closed: true` and `closed_notice` — the core's text for the place
  of the input box, in the session's language, so a frontend never writes its own;
- is skipped by `resumable_session`, cannot be `reset`, and leaves the files and
  git alone. A new session (`new`, `launch`) starts without it.

### Restoring a session's feed: `--session-op history`

The session document keeps only what the model needs (goal, role, a 200-char
result). Separately, the core keeps a **journal of what the user saw**: the
same events the §8 stream carries, recorded as they were sent, so a frontend
redraws an old session by replaying them through the reducer it already has.
It covers every turn, including ones run from the terminal or by another
frontend.

```bash
kvist --workspace WS --session-op history --session-name feature --history-limit 500
```

```json
{"ok": true, "session": "feature", "has_more": true, "total_events": 1240,
 "skipped_lines": 0, "journal_bytes": 913442,
 "events": [
   {"seq": 812, "turn": 9, "ts": 1758560000.1, "event": {"type": "user", "text": "fix the tests"}},
   {"seq": 813, "turn": 9, "ts": 1758560000.9, "event": {"type": "route", "role": "coder", "source": "llm"}},
   {"seq": 820, "turn": 9, "ts": 1758560014.2, "event": {"type": "action_result", "step": 3, "action_type": "run_shell", "ok": true, "summary": "Command exited with code 0", "output": "12 passed\n", "exit_code": 0, "path": null, "first_line": null}}
 ]}
```

- `events` are in order, oldest first; `event` is exactly a §8 event object.
  `seq` is unique and increasing within the session, `turn` numbers the user
  requests (1-based), `ts` is the wall-clock time it was recorded.
- **Recorded** (already redacted): `route`, `plan`, `sandbox_notice`, `step`
  (with the full `action_args`, so a `run_shell` step has its argv and a
  `write_file` step its content or `old_text`/`new_text`), `action_result`
  (output, exit code, path, `first_line`), thoughts as
  `reasoning_start` / `reasoning_chunk` / `reasoning_commit` (**one**
  `reasoning_chunk`, holding the final text), the answer as `stream_commit`,
  `message` or `final` (in full), `error`, and per turn one `usage` event
  (`kind: "turn"`, summed tokens).
- **Not recorded** (live-only): `status`, `live_start`/`live_stop`,
  `stream_chunk`, `action_output`, `debug`, per-step `usage`, `hello`,
  `input_request`, `command_result`.
- **Added by the journal**: `user` — `{"type": "user", "text": …}`, the
  request that opened the turn (`"resumed": true` on a `/resume`), which the
  stream never carries; `turn_end` (with `"stopped_by_user": true` when the
  user stopped the turn) closes every turn; `journal_notice` says something was
  left out: `{"omitted_events": N}` inside a turn, `{"omitted_turns": N}` as the
  first event of the journal once old turns were dropped.
- Paging: the newest `--history-limit` events (default 500, max 5000) come
  back; `has_more` says older ones exist, and `--history-before SEQ` returns the
  page that ends just before `seq` `SEQ`. A page may start mid-turn.
- Limits (a truncated string keeps both ends and says
  `[... N characters omitted ...]`): 20 000 characters per field (written
  content, arguments, thoughts), 100 000 per answer, 200 items per list, 2 000
  events and 2 MiB per turn (answers, errors and `turn_end` have a small
  reserve above that), 8 MiB and 500 turns per session — beyond which the
  **oldest whole turns** are dropped.
- `skipped_lines` counts journal lines that could not be parsed (a crash can
  tear the last one); they cost only themselves. A missing journal is an empty
  history, so sessions from before the journal existed read as `events: []`.
  An unknown session is `{"ok": false}`.
- The journal is display-only: it never reaches a model prompt. It lives and
  dies with its session — `delete` and `reset` remove it, `new`/`create`/`switch`
  leave it alone. If it cannot be written, the turn still runs and a `debug`
  event says so.

To **resume** the active session's checkpoint, run the event frontend with
`--resume` instead of writing a goal line:

```bash
kvist --workspace WS --frontend stdio --resume
```

The stream is the ordinary §8 stream; if there is no checkpoint **in any
session**, the core emits an `error` event ("No saved checkpoint to resume.")
and ends the run. Otherwise the core activates the session that holds it, so
the `hello` event's `session` may differ from the one you launched in.

---

## 8b. Commit messages for IDE frontends

One call, no event stream: write the changes to stdin as **UTF-8** JSON, close
stdin, read one JSON line from stdout.

```bash
kvist --commit-message < payload.json
```

```json
{
  "files": [{"path": "src/app.py", "patch": "--- a/src/app.py\n+++ b/src/app.py\n..."}],
  "recent_subjects": ["Fix retry backoff", "Add AnyModel stub"]
}
```

- `files` — one entry per changed file; `path` is relative to its VCS root,
  `patch` is its unified diff. Send what the user selected for this commit.
- `recent_subjects` — optional, the latest commit subjects (at most 15 are
  used). The message follows their convention: Conventional Commits if the
  project uses them, plain imperative sentences otherwise.

Reply (ASCII-escaped, so the console code page does not matter):

```json
{"ok": true, "message": "Retry provider calls on 429\n\nThe gateway ..."}
{"ok": false, "error": "Nothing to describe: no changed files with a diff."}
```

Exit code is 0 on success and 1 on failure. Guarantees a frontend can rely on:

- The message is always English, and its subject is at most 72 characters.
- The saved provider and its **fast** model are used. A missing key is an
  error, never a prompt, so the call cannot hang waiting for input.
- Nothing is written to the workspace — no `.kvist/` session, no `KVIST.md`.
- Secret files (`.env`, keys, certificates, `.ssh/`, ...) and `.kvist/` are
  dropped before the model sees anything; no need to filter them yourself.
- A large diff is cut to fit the model; the call never fails for size.
- Anything else the core prints goes to stderr; log it, do not parse it.

---

## 8c. In-process commands (`--frontend stdio-multi`)

The terminal's slash-commands and setup wizards run inside the same long-lived
process too. A frontend does not re-implement them: it sends one message, draws
every dialog the command opens with a generic `input_request` view, and reads
the outcome from `command_result`.

```json
<<< {"type": "command", "name": "role", "args": []}
>>> {"type": "input_request", "request_id": "b7dc…", "kind": "choice", "title": "Select Agent Role", "body": "", "options": [{"value": "auto", "label": "Auto (automatic role detection)", "description": "…"}, {"value": "coder", "label": "Coder", "description": "…"}], "current_value": "auto", "help_text": "…"}
<<< {"request_id": "b7dc…", "value": "coder"}
>>> {"type": "command_result", "name": "role", "ok": true, "message": "Role set to: coder / Роль: coder", "effects": {"role": "coder"}}
```

**Multi-line goal.** A goal is a plain stdin line; text with line breaks goes as
`{"type": "goal", "text": str}` (`stdio-multi`), and `text` is queued as the goal.

**Inbound message:** `{"type": "command", "name": str, "args": string[]}`.
`name` is the slash-command name without the slash (case-insensitive); `args`
is optional and answers the command's first question up front, so
`{"type":"command","name":"role","args":["coder"]}` opens no dialog at all.
Dialogs use the ordinary `input_request` / response pair of §7; a `secret`
answer travels only inbound and is never echoed: not in an event, a `message`,
an `effects` value or a log. Destructive steps (`revert`, `del-provider`,
`full-reset`) ask with `kind: "confirm"` and a `body`; only `approved: true`
counts as yes, anything else (`false`, no answer, EOF) does nothing.

**Rules**

- A command runs **between turns** only. While a turn (or another command) is
  running the core answers at once with `ok:false` and does not run it.
- Every accepted command ends with exactly one `command_result`; unlike a turn
  there is **no `turn_end`**. An unknown name, a missing name or malformed
  `args` also get a `command_result` with `ok:false`.
- `ok:false` means the command failed (`message` says why). A dialog the user
  cancelled is `ok:true` with `effects` `{"cancelled": true}` and nothing
  changed. stdin EOF cancels an open dialog the same way.
- `message` is human text, always `English / Русский`, secret-redacted.
- `effects` says what changed, so the frontend can refresh its own state. Keys
  are present only for what the command touched.

**Commands and their `effects`**

| `name` | `args` | `effects` |
|---|---|---|
| `role` | `[auto\|coder\|reader\|devops\|chat]` | `{"role": str}` — also the role every following goal is submitted with; saved as the global preference |
| `effort` | `[false\|low\|medium\|high\|xhigh\|max]` | `{"effort": str, "advisory": str}` — `advisory` is how the level maps onto the active provider (may be empty) |
| `verbose` | `[true\|false]` (none = toggle) | `{"verbose": bool}` — this process only |
| `style` | `[chat\|agents NAME]` | `{"style": {"type": str, "name": str, "applied_to_session": bool}}` — `false` means the running session keeps its style until the next session |
| `session` | `[switch NAME]`, `[new [NAME]]`, `[delete NAME]`; none = the picker | `{"session": str, "sessions": string[], "has_checkpoint": bool, "changed": bool}` |
| `new` | `[NAME]` | as `session` |
| `rename` | `NAME` (required; multiple args are joined with spaces) | `{"session": str, "title": str}` — sets the active session's title by hand; blocks background title generation for it from now on (§8a) |
| `models` | `[FAST SMART]`; none = the picker for the active provider | `{"provider": str, "models": {"fast","smart"}, "warnings": string[]}` — the agent was rebuilt with these models and they are saved |
| `changes` | — | `{}`; the report is the `message` |
| `provider` | `[PROVIDER_ID]`; none = the picker (with "+ Create custom provider…") | `{"provider": str, "models": {…}, "warnings": string[], "changed": bool}`; `changed:false` (same provider) carries only `provider` and `changed`. The agent was rebuilt and the choice saved. Building may open more dialogs: `text` (base URL), `secret` (API key), `choice`/`text` (models). |
| `change-api` | — | `{"provider", "models", "warnings", "key_saved": true}`. One `secret` dialog per key the active provider needs (an empty answer keeps that key; all empty = `{"cancelled": true}`). If the rebuild then fails: `ok:false`, `{"key_saved": true, "restart_required": true}` |
| `del-provider` | — | custom provider only. `confirm` first, then the replacement is picked with the provider picker: `{"deleted": str, "provider", "models", "warnings"}`. No replacement chosen: `ok:false`, `{"deleted": str, "restart_required": true}` (the running agent still uses the deleted provider until restart) |
| `full-reset` | — | two `confirm` dialogs, the second titled "Final confirmation"; then `~/.kvist/config.json` is wiped, the role route returns to auto, and a provider is picked again: `{"reset": true, "provider", "models", "warnings"}`. Nothing chosen: `ok:false`, `{"reset": true, "restart_required": true}` |
| `settings-api` | — | a menu loop of `choice` (one row per setting, "Reset all", "Done"), then `choice` on/off or `text` for a number (asked again until valid). `{"changed_keys": string[], "values": {key: value}, "restart_required": bool}`; `restart_required` is true when `dynamic_models` changed (it applies at the next launch) |
| `strime` | `[true\|false]` (none = toggle) | custom provider only (else `ok:false`): `{"stream": bool}` |
| `revert` | — | `confirm` whose `body` lists the files to restore. `{"reverted": [{"path", "action": "restored\|deleted\|skipped", "reason"}]}`; nothing tracked: `{"reverted": []}` and no dialog |

**`resume`** is not a wizard but a turn of the agent: `{"type":"command","name":"resume"}`
continues the newest interrupted run, in whichever session holds its checkpoint.
With no checkpoint in any session the answer is at once
`command_result {"name":"resume","ok":false,…}` and nothing else. Otherwise the
stream is exactly what a goal produces, with one extra event first:

```
>>> {"type": "session", "session": "chat-2026…", "has_checkpoint": true}
>>> … turn events (route/plan/step/action_result/… final or error) …
>>> {"type": "turn_end"}
```

There is **no `command_result`** for a resumed turn; `turn_end` ends it, and
`session` tells the frontend which conversation is now active. Like any turn it
takes no `command` while it runs. In a closed session (§8a) `resume` is a turn
without the `session` event: the core's closed-session reply as `final`, then
`turn_end`.

A `session` or `new` command that changed the active session also emits a
`session` event before its `command_result`, with `closed`/`closed_notice` when
the target is closed.

## 8d. The Agent Client Protocol (`--acp`)

`kvist --acp` is a second transport for the same core: a JSON-RPC 2.0 peer
speaking the [Agent Client Protocol](https://agentclientprotocol.com/) on
stdin/stdout, for hosts that already implement ACP — Zed is the one this is
built and documented against.

**None of the NDJSON contract above applies to this mode.** There is no `hello`,
no `PROTOCOL_VERSION`, and no `TurnEvent`: the wire types come from the ACP
schema (`session/new`, `session/prompt`, `session/update`, …). `--workspace` is
not used either — the working directory arrives with `session/new`, and a
session id is a stable function of that directory plus the session name, so an
id survives a restart. `--login` is accepted only with `--acp`: it configures
provider credentials in the launching terminal and exits. **Secrets never
travel over the ACP connection** — ACP forbids eliciting sensitive data in form
mode, so a missing key is reported with the command to run, never prompted for
on the wire.

What the mode implements is the same `LocalAgent` / `SafeExecutor` / session
store as every other frontend. Its own source files are `acp_frontend.py` (the
peer) and `acp_events.py` (Kvist events → ACP updates); the mapping from Kvist's
cumulative snapshots to ACP's append-only deltas lives in the latter.

Capabilities are declared only where the path works end to end — no NES, no MCP
servers, no image or audio prompts. What a given host can and cannot expose is
recorded per capability in [`frontends/zed/README.md`](../frontends/zed/README.md)
([RU](../frontends/zed/README.ru.md)).

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

`EventSession` API: `submit(user_goal, routed_role=None)`, `submit_resume()`
(continue the active session's checkpoint), `events()` (iterator; ends when the
worker finishes and the queue drains), `respond(request_id, *, value=None,
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
    case "hello":            checkVersion(ev.protocol_version);        break;
    case "stream_chunk":     appendStreaming(ev.stream_id, ev.text);   break;  // live typing effect
    case "reasoning_start":  showThinking(ev.stream_id);               break;  // model is thinking
    case "reasoning_chunk":  updateThinking(ev.stream_id, ev.text);    break;  // grey live thoughts
    case "reasoning_commit": collapseThinking(ev.stream_id, ev.duration_seconds); break; // "Thought for Ns"
    case "status":           showStatus(ev);                           break;
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
- **The terminal is a special default.** Its REPL adds slash-commands and config
  wizards beyond the event surface. Session lifecycle management, however, is
  fully available to headless frontends via `--session-op` and `--resume` (§8a) —
  with the same semantics as the terminal commands. Under `stdio-multi` the
  common slash-commands also run in-process (§8c).
- **One process = one run** in v1. A run executes in the workspace's active named
  session; switch sessions between runs with `--session-op switch`. For multiple
  concurrent clients, run one process per workspace or write a multiplexing
  socket transport. Session storage itself is safe under concurrent writers
  (per-path locks + atomic replace), but two agents working in one workspace at
  once is not a supported workflow.

---

## 12. Files

Core seam: `ui.py`, `approvals.py`, `terminal_banner.py`.
Event protocol: `frontend_protocol.py`, `event_stream_ui.py`, `event_session.py`,
`event_stdio.py`, `frontends.py`.
Headless sessions: `session_store.py` (behind `--session-op`).
ACP peer (§8d): `acp_frontend.py`, `acp_events.py`.
Tests: `kernel/tests/test_frontend_protocol.py`, `kernel/tests/test_event_stream_ui.py`,
`kernel/tests/test_event_session.py`, `kernel/tests/test_frontends.py`,
`kernel/tests/test_session_store.py`, `frontends/zed/tests/test_acp_frontend.py`,
`frontends/zed/tests/test_acp_events.py`.
