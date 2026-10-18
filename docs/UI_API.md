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
| `manifest` | `roles:object[], effort_levels:object[], providers:object[], model_roles:string[], default_models:object, action_types:string[], slash_commands:object[], api_settings:object[], styles:object[], themes:object` | UI-facing metadata catalog built by `core_manifest.build_manifest`; sent once after `hello` so frontends can populate pickers dynamically without hardcoding. `styles` lists the picks (`{value, description}`); each pick also sets the chat voice, which the core derives and the manifest does not carry. |
| `live_start` / `live_stop` | — | live status begins / ends |
| `status` | `user_message:str, status:str, step:int, progress:int, plan_step:str, is_error:bool` | per-step progress; during a call `user_message` is the call's label built by the core ("reads src/x.py") |
| `step` | `step:int, plan_step:str, thought:str, user_message:str, action_type:str, action_args:object, reasoning_seconds:number, action_description:str, call_id:str, batch_id:str` | one tool call of a model response. `reasoning_seconds` is how long the model spent thinking before it (0 when it did not think). `action_description` is the call's label, built by the core from its arguments ("reads src/x.py", `search "role" in src/`; a `run_shell` label is the purpose the model gave). `batch_id` names the model response the call came from: the calls of one response share it and may be shown as one group. `call_id` names the call; its `action_result` carries the same one. `thought` is always `""` (the model no longer writes one). `user_message` is the response's own text for the person on the first `step` of its batch and `""` on the others, so a frontend that shows it per step shows it once. |
| `action_result` | `step:int, action_type:str, ok:bool, summary:str, output:str, exit_code:int\|null, path:str\|null, first_line:int\|null, lines_added:int\|null, lines_removed:int\|null, patch:str\|null, call_id:str, batch_id:str` | the outcome of one executed action (match it to its `step` by `call_id`, or by `step`). Read-only calls of one response run in parallel, so several `step` events can arrive before their results; the results still arrive in call order. A writing call skipped because an earlier write of the same response failed gets `ok:false` and the summary "Skipped: an earlier write failed". `summary` is one short line ("Wrote 3 lines", "Command exited with code 0", "2 matches"). `output` is the redacted command output (`run_shell`, `git_status`, `git_diff`, `delegate_task`) or the error text of a failed action; it is capped at 4000 characters, keeping the head and the tail with an `[... N characters omitted ...]` marker between them. `exit_code` is set for `run_shell` only (`null` when denied or timed out). `path` and `first_line` are set for `write_file`/`append_file`/`replace_in_file`: `first_line` is the 1-based line of the resulting file where the written text starts (`1` for `write_file`, the first line of the appended text for `append_file`, the first replaced occurrence for `replace_in_file`) so a diff can be numbered; it is `null` for a failed write. `lines_added`/`lines_removed` are the write's line diff, counted by the executor that saw the text it replaced (`null` for anything but a successful write), for a frontend that shows per-file counts. `patch` is the same write as a unified diff (`--- a/PATH` / `+++ b/PATH` or `/dev/null`, then `@@ -a,b +c,d @@` hunks with three lines of context, secrets redacted), for a frontend that shows what changed in a file; hunk numbers are the file's own lines, so no `first_line` arithmetic is needed. It is `null` for anything but a successful write and for a write that changed nothing; past 200 000 characters it is cut at a line and ends with `\ patch truncated`. The model never sees it. Not sent for `final_answer`/`report_to_user`/`refuse`, nor for actions a runtime guard rejected before they ran (read-only or plan-authorization blocks); a web-search/URL budget refusal is sent as `ok:false`. |
| `action_output` | `step:int, output:str, call_id:str` | live output of a running `run_shell` command, sent while it runs and before its `action_result`. `output` is a **snapshot** of the most recent output (at most 4000 characters, redacted as a whole), not a delta, so replace what you show. Events come at most about every 120 ms, and the last one is never dropped. It is a preview only: the complete, capped output still arrives with the `action_result`, which ends the live output for that `step`. A command that prints nothing sends none. |
| `job_started` | `job_id:str, command:string[], purpose:str, call_id:str` | a `run_shell` call with `background: true` started a background job; the call itself answered at once with the job id and the process keeps running. `command` is the argv, `purpose` the model's own label, `call_id` the `step` call that started it, so a frontend may attach the job to that call's card. Recorded in the session journal. |
| `job_output` | `job_id:str, output:str` | live output of a running background job: a **snapshot** of its tail (redacted, capped), not a delta, so replace what you show. At most about every 120 ms, the last one is never dropped. Live-only, never recorded; the capped tail arrives again with `job_finished`. |
| `job_finished` | `job_id:str, status:str, exit_code:int\|null, output:str` | a background job ended. `status` is `exited` (`exit_code` set), `timed_out` or `stopped` (`job_stop`, or the core killed it when the turn ended; `exit_code` may be `null`). `output` is the redacted, capped tail. Without `--wake` a job still running when the agent answers is killed by the core, so every `job_started` gets its `job_finished` before `turn_end`. With `--wake` the job keeps running after the answer and its `job_finished` arrives between turns, followed by a `wake` turn. Recorded in the session journal when it arrives during a turn. |
| `job_notice` | `text:str, job_ids:string[]` | what the runtime told the **model** about background jobs (new output, completions) before its next request, exactly as the model saw it (redacted, capped). A frontend may show it as a muted note; it is not an instruction to the person. Recorded in the session journal. |
| `task_state` | `state:str, reason:str` | the task's life cycle (§8f): `running`, `pause_requested`, `paused`, `resuming`, `cancelling`, `cancelled`, `completed`, `failed`, `awaiting_input`, `idle`. Show "pausing…" for `pause_requested` and "paused" only when `paused` arrives. Live-only, never recorded. |
| `screen` | `screen_id:str, skill:str, title:str, state:str, url:str, stream_url:str, frame_url:str` | the agent's own virtual screen (a skill with the `display` capability, e.g. `browser`). `state`: `starting`, `running`, `paused`, `unavailable`, `closed` (URLs empty once closed). `stream_url` is an MJPEG stream for a small preview, `frame_url` one JPEG, `url` a page for a browser tab. All three are `http://127.0.0.1:<port>/…` addresses that carry their own access token and are view-only; open nothing but loopback addresses from them. Live-only. |
| `skill` | `skill:str, version:str, name:str, state:str` | a skill was attached to or detached from the task (`state`: `attached`, `detached`). Recorded in the journal. |
| `helper` | `agent_id:str, event:str, state:str, session:str, task:str, summary:str`; optional `stopped_by:str` | a short notice about a helper agent (`event`: `started`, `update_applied`, or the state a helper reached: `done`, `failed`, `stopped`, `paused`; `state`: `starting`, `running`, `paused`, `done`, `failed`, `stopped`; `stopped_by`: `person` when the person stopped it, `agent` or a core reason otherwise). Its full activity is the service session named in `session`, readable with `--session-op history`. Recorded in the journal. |
| `helper_activity` | `agent_id:str, phase:str, call_id:str, action_type:str, label:str, ok:bool\|null, summary:str, output:str` | one call of a helper as it happens: `phase` is `step` when it starts (`label` is the call's own description) and `result` when it ends (`ok`, `summary`, and `output`: its last three non-empty lines, redacted). Match the two by `call_id`. Live only: the helper's service session journals its full activity, the main journal keeps the `helper` notices. |
| `artifact` | `name:str, mime:str, path:str, caption:str, skill:str`; optional `preview:str` | a file or image a skill produced for the person; `path` is the absolute path of the saved copy under the workspace's `.kvist/artifacts/` (a frontend may show it as a link or read it; it never leaves the workspace), `preview` a small JPEG data URL (live only). Recorded in the journal without `preview`. |
| `route` | `role:str, source:str` | the role the turn actually runs under, sent once per turn before the plan or reply. `role` is `coder`, `reader`, `devops` or `chat`. `source` is `explicit` (the caller passed the role), `direct` / `llm` / `keyword_fallback` / `default` (how the router decided) or `resume` (a bare "continue" after a stopped run inherited that run's role). |
| `plan` | `markdown:str` | the plan the model proposes for the person's decision (plan mode); short Markdown |
| `sandbox_notice` | `note:str` | one-time sandbox honesty notice |
| `stream_start` | `stream_id:str` | a streaming response begins |
| `stream_chunk` | `stream_id:str, text:str` | current cumulative answer text (a **snapshot**, not a delta) |
| `stream_commit` | `stream_id:str, text:str` | final streamed answer text (once) |
| `reasoning_start` | `stream_id:str` | the model started thinking; same `stream_id` as the answer it precedes |
| `reasoning_chunk` | `stream_id:str, text:str` | current cumulative **thinking** text (a snapshot, separate channel from the answer) |
| `reasoning_commit` | `stream_id:str, duration_seconds:number` | thinking ended (the answer is about to stream); carries how long it took |
| `message` | `markdown:str`; optional `source:str` | a non-streamed Markdown message. `source` says what it is: `reply` (the assistant speaking to the person: a chat reply, a report), `notice` (a note of the runtime: a warning, a clean-up line) or `skill` (a skill's message). A missing `source` means `reply`. A frontend that reads answers aloud reads only replies. |
| `final` | `message:str, user_messages:string[], outcome:str`; optional `closed:true`, `paused:true` | the run's final answer (terminal event). `outcome` is `"done"` when the model finished the run without declaring it incomplete, `"incomplete"` otherwise (an honest "not done", a stop, the step budget, a refusal). `closed: true` marks end_session's closing words and a closed session's reply (§8a, "Closed sessions"): show `message` as an ordinary Kvist message, with no status or "not done" frame. `outcome` is then `"incomplete"`, for frontends that do not know the field. `paused: true` marks a turn the person paused (§8f): the task is kept and the next message continues it; `outcome` is `"incomplete"` and the turn is not a failure. |
| `usage` | `input_tokens:int, output_tokens:int, context_tokens:int, context_limit:int, model:str, kind:str, step:int` | token usage of one completed model call. `input_tokens`/`output_tokens` are **spend**: sum them over the run. `context_tokens` is the **size of that one prompt** — the conversation itself — so *replace* the previous value instead of summing. Both `context_*` values are the core's current answer from `context_meter.context_usage`, present on every `usage` event (including `report`), so a frontend assigns them as they are and keeps no merge logic. Because every step resends the context, the summed spend runs far ahead of `context_tokens` — showing the sum as "tokens" reads as a huge conversation that does not exist. `context_limit` is the budget the core trims a prompt to for the active model. |
| `context` | `context_tokens:int, context_limit:int, prompt:int, generated:int, pending:int` | live size of the context window, from `context_meter`. `context_tokens` is everything in the window now: `prompt` (system prompt, tool schemas, history, the request, earlier tool results as last sent) + `generated` (reasoning, text and tool-call arguments of the call in flight or just finished) + `pending` (a new message or tool results not sent yet). Assign it as it is. Sent at once when a request goes out, a call ends, a message or tool result is added, and at most every 2 s (`context_meter.REFRESH_SECONDS`) while a call streams. Live-only: not journaled; `usage` carries the same values at the end of each call. |
| `narration` | `text:str` | one short spoken line from the narrator: with `AGENT_VOICE_MODE=1`, while the strong model works on a step (never while the fast model does), a fast model voices what the work shows, in the person's language, at most once per `narrator_interval_seconds` and only after `narrator_delay_seconds` of the call. A voice frontend speaks it at once and drops it when the step's own output arrives. Live-only: not journaled, and it never enters a model request. |
| `debug` | `message:str` | verbose diagnostics |
| `error` | `message:str` | an error line (also emitted if the run raises) |
| `turn_end` | — | `--frontend stdio-multi` only: the turn ended (after `final`, `error`, or a chat reply that has no `final`); the process stays up for the next goal |
| `wake` | — | `--frontend stdio-multi --wake` only: the core starts a turn nobody typed, because helpers or background jobs an answered turn left running ended on their own (see "Wake-ups" below). The turn's events follow, then `turn_end`. It has no request text: its `job_notice` and `helper` events say what ended. |
| `session` | `session:str, has_checkpoint:bool`; optional `closed:true, closed_notice:str, title:str` | `--frontend stdio-multi` only: the first event of a `resume` turn — the session the interrupted run continues in, which may differ from the one in `hello` — and the session a `session`/`new` command switched to (§8c). `closed` and `closed_notice` are present only for a session `end_session` closed (§8a, "Closed sessions"): show `closed_notice` in place of the input box. `title` is the session's stored title, when it has one. |
| `session_ended` | `session_id:str, warned_msg_id:int, closed_msg_id:int, reason:str, closed_notice:str` | the model closed the conversation for good with `end_session` (§8a, "Closed sessions"). Sent during the turn, before its `final`, which carries the closing words for the person. `session_id` is the session name; `warned_msg_id`/`closed_msg_id` are the numbers of the person's messages that got the warning and that closed it; `reason` is the model's one-line reason, for logs rather than for the person; `closed_notice` is the core's text for the place of the input box, in the session's language. Recorded in the session's journal. |
| `session_title` | `session:str, title:str` | a session's title became known: generated in the background right after the first batch of actions (from the user's message alone) or, when the turn ends before that, from the first completed turn (see §8a), or set by hand with the `rename` command (§8c). Additive, out-of-band — it can arrive well after the turn's `turn_end`, and `session` may name a different session than the one currently active if the user switched sessions while generation was still running. |
| `command_result` | `name:str, ok:bool, message:str, effects:object` | `--frontend stdio-multi` only: the outcome of an inbound `command` message (§8c). It is the last event of that command; **no `turn_end` follows it**. |

**Background shell commands.** `run_shell` accepts an optional `background: true`. The call then returns at once with a job id instead of the command's output, the process keeps running, and its life shows as `job_started` / `job_output` / `job_finished` events. Two further `action_type` values appear in `step` and `action_result`: `job_output` (args `{job_id, wait?}`: read the job's new output, or wait up to `wait` seconds for it to end) and `job_stop` (args `{job_id}`: stop the job). The model's reads of a job are separate calls and do not change the job's events. A turn that ends any way but a final answer (stop all, an error, a limit) kills every job still running; so does a final answer when the frontend did not pass `--wake`.

**Wake-ups (`--wake`).** A frontend that can show a turn it did not start passes `--wake` with `--frontend stdio-multi` (the Ink app and the VS Code panel do). Then a final answer leaves running helpers and background jobs alive, and when one ends on its own (not stopped by `job_stop`, the person or stop all) the core runs a turn about it: `wake`, the turn's events (`job_notice`/`helper` first, then the model's answer), `turn_end`. The model keeps the role and language of the turn that left the work running; the turn is not one of the person's messages. A wake-up never starts while another turn or a command runs: the core checks again when that one ends, so a goal sent at the same moment as a `wake` runs right after the wake-up turn. Between turns the core writes events as they occur (a `job_finished`, a writing helper's approval request), instead of holding them for the next turn. `/new` and switching sessions stop what the old session left running. In the journal, a woken turn's `user` event has an empty `text` and `"woken": true`.

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
in the session document itself: once, in the background, as soon as the first
batch of actions has run (the model sees the user's message alone) or, when the
turn ends before that, from the first completed turn (the goal plus the first
20 lines of the reply). A model that does not answer in
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
  (`closed: true`, `outcome: "incomplete"`) with a fixed reply, followed by
  `turn_end` in stdio-multi, and nothing is written to the session or its
  journal;
- **stays readable**: `history`, `list`, `status` and switching to it work as
  usual, and `list`/`status` items, the `history` payload and the `session`
  event carry `closed: true` and `closed_notice` — the core's text for the place
  of the input box, in the session's language, so a frontend never writes its own;
- is skipped by `resumable_session`, cannot be `reset`, and leaves the files and
  git alone. A new session (`new`, `launch`) starts without it;
- **can be deleted like any other**: `delete` (the session-op and the `session
  delete` command) removes it and its journal when it is not the active one. It
  is the person's history: the lock forbids continuing the conversation, not
  keeping it.

In a chat turn the model uses it by replying with a tag and nothing else. Such a
reply is never shown: its stream ends with an empty `stream_commit`, and the
turn's answer arrives as the warning `message` or the closing `final`. An empty
`stream_commit` draws nothing.

The warning (`message`) and the closing words (`final` with `closed: true`) are
Kvist speaking: draw them as ordinary Kvist messages, never with a status frame,
a "not done" mark or an error colour. A warning is not an unfinished answer, and
a close is not a failed task.

What a frontend shows in a closed session, the same on every surface: a bar in
place of the input box, as tall as the box, with `closed_notice` in muted text —
no red, no alarm icon, no animation. It appears on a live `session_ended` and
when a session with `closed: true` is opened. Enter opens a new session through
the frontend's usual new-session path; every other key does nothing, and there
is no way to type into the session (no focus, no paste). The history stays
scrollable, selectable and copyable. Switching to another session brings the
input box back.

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
  `message` or `final` (in full), `error`, the background-job events
  `job_started` / `job_finished` / `job_notice`, and per turn one `usage` event
  (`kind: "turn"`, summed tokens).
- **Not recorded** (live-only): `status`, `live_start`/`live_stop`,
  `stream_chunk`, `action_output`, `job_output`, `debug`, per-step `usage`, `hello`,
  `input_request`, `command_result`.
- **Added by the journal**: `user` — `{"type": "user", "text": …}`, the
  request that opened the turn (`"resumed": true` on a `/resume`; empty `text`
  and `"woken": true` on a wake-up turn), which the
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
| `style` | `[NAME]` | `{"style": {"name": str, "applied_to_session": bool}}` — one pick sets the agent style and the chat style that goes with it; `false` means the running session keeps its styles until the next session |
| `session` | `[switch NAME]`, `[new [NAME]]`, `[delete NAME]`; none = the picker | `{"session": str, "sessions": string[], "has_checkpoint": bool, "changed": bool}` |
| `new` | `[NAME]` | as `session` |
| `export` | none = the pickers; `list`; `<agent> [all]`; `all`; add `everywhere` to include sessions from other folders. `<agent>` is `claude-code`, `codex`, `opencode`, `cursor`, `qwen-code`, `continue` or `aider` | Imports another agent's sessions as new Kvist sessions (named `cc-…`, `cx-…`, `oc-…`, `cu-…`, `qw-…`, `ct-…`, `ai-…`; the active session is not switched). Without arguments two `choice` dialogs: the agent (with counts), then the session (with "All from this folder" and "Other folders…"). `{"imported": [{"session","source","title","turns","turns_in_context"}], "already_imported": int, "empty": int, "failed": string[]}`; `list` has no `effects` and the counts are the `message`. A session imported before is skipped, never overwritten. A cancelled dialog is `{"cancelled": true}` |
| `rename` | `NAME` (required; multiple args are joined with spaces) | `{"session": str, "title": str}` — sets the active session's title by hand; blocks background title generation for it from now on (§8a) |
| `models` | `[FAST SMART]` or `[PROVIDER FAST SMART]`; none = the picker for the active provider | `{"provider": str, "models": {"fast","smart"}, "warnings": string[]}` — the agent was rebuilt with these models and they are saved; with `PROVIDER` the provider switches in the same rebuild and is saved too |
| `catalog` | — | `{"providers": [{"provider": str, "label": str, "current": bool, "chosen": {"fast","smart"}, "models": string[]}]}` — no dialog, nothing changed: every connected provider (its key or sign-in is saved), the current one first, with the models it uses and the ones it offers (its live `/v1/models` list when it answers, else the curated one), for a picker across providers that applies a choice with `models PROVIDER FAST SMART` |
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

## 8e. The tray link

`frontends/tray` is a resident icon for every core that lives in a terminal. A core connects to it with `kernel/tray_link.py`; nothing travels back to the core. This is not the §8 event stream.

- **Discovery.** The daemon writes `~/.kvist/tray.json` (`{"port", "token", "pid"}`, mode 0600) and listens on `127.0.0.1:<port>`. A core with no reachable daemon starts it once per process (`KVIST_TRAY_CMD`, a JSON argv, or the source checkout's `frontends/tray/node_modules/electron`) with `KVIST_CORE_CMD` set to its own command line.
- **Wire.** NDJSON. The first line is `{"type": "hello", "token": "..."}`; a wrong token closes the connection. Then one `{"type": "state", "pid", "workspace", "session", "state"}` per change, where `state` is `running` (a turn began) or `done` (it ended). Each message is a complete snapshot, so a sender may drop all but the newest.
- **Who sends.** Only terminal hosts: `main()` sets `KVIST_TRAY_HOST=1` before the terminal frontends start, and the Ink app's core inherits it. Cores started by a GUI, ACP or `run_task.py` never send.
- **Switch.** `tray_enabled` in `~/.kvist/config.json` (absent means on) is read before every send; `kvist --tray-op enable|disable|status` prints `{"ok", "enabled"}` and exits.
- **Bar clients.** A hello with `"role": "bar"` subscribes to `{"type": "summary", "state": "idle|running|done", "count", "tooltip"}` lines (sent at once and on every change) and may send `{"type": "toggle"}` to open or close the panel. `kernel/tray_bar.py` adds a module to the user's Waybar or Polybar config that runs `--tray-op bar-stream` and `--tray-op toggle`; the edit is idempotent and keeps `<file>.kvist-backup`.
- **Reading.** The daemon never reads session files; the mini chat calls `--session-op history`.

## 8f. Skills, the agent's screen, pause and helpers

Skills (installable packages, see [SKILLS.md](SKILLS.md)) reach a frontend through
the additive events `skill`, `screen`, `helper`, `artifact` and `task_state` (§7). A
frontend that ignores them keeps working.

**Inbound control messages** (`--frontend stdio`, `stdio-multi`; one JSON object per line):

| Message | Effect |
|---|---|
| `{"type": "cancel"}` | **Stop all.** Cancels the turn, the helpers and every resource of the task (processes, runners, virtual screens, ports). The checkpoint is kept, so `resume` continues. |
| `{"type": "pause"}` | **Pause.** The main agent and every helper stop at their next safe point. `task_state` goes `pause_requested` → `paused`; the turn ends with `final` carrying `paused: true`, then `turn_end`. The task keeps its dev server, virtual screen and helpers; the person's next message continues the same task. |
| `{"type": "helper_stop", "agent_id": str}` | Stops one helper. The main agent is told and does not restart it by itself; a helper stopped by the person stays stopped until the person writes again. An unknown id is reported as an `error` event. |

Frontends bind Escape to pause and Ctrl+C (or a separate "Stop all" button) to cancel.
A pause request while nothing runs does nothing.

**Previews.** A `screen` event's URLs point to the core's loopback viewer. Rules for
frontends: show `stream_url` in an `<img>` (or the frame URL periodically), let the person
open `url` in the system browser, never load the URLs in a page that can run other
content, and add `img-src http://127.0.0.1:*` (and the same for `frame-src` if you embed
`url`) to a content security policy. Hide the preview when `state` is `closed`.

**Management operations (headless, then exit; JSON on stdout when it is not a terminal, or with
`--json`; on a terminal `--skill-op` prints a formatted view instead):**
`--skill-op list|show|install|approve|revoke|install-deps|rollback|uninstall|remove-version|reset-data|check`
(with `--skill`, `--skill-path`, `--skill-version`, `--yes`), `--memory-op list|add|update|delete`,
`--mcp-op list|add|remove` and `--connection-op list|add|remove` (secrets on stdin, never
in argv). Details and semantics are in [SKILLS.md](SKILLS.md).

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
