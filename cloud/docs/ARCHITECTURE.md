# Kvist Cloud — architecture

*(Русская версия: [ARCHITECTURE.ru.md](ARCHITECTURE.ru.md))*

Kvist Cloud is a subscription: every customer gets a cloud machine with Kvist on it. The
customer pastes their own model key, writes a task, and the agent does it on the machine. The
machine sleeps between tasks; the customer pays for the hours the agent actively works.

This MVP runs end to end on one computer. The only piece that is not real yet is a hosting
provider adapter (`providers/sprites.py`); `providers/local.py` stands in for it.

## Picture

```
 browser ──HTTPS + cookie──▶ control server (FastAPI) ◀──WebSocket (outbound from machine)── runner
   │  ▲                       │  users, machines, tasks,                                    │
   │  └──WebSocket: task ─────┤  task_events, ledger_entries,                               │ spawns, per task
   │     events               │  subscriptions, funnel_events                               ▼
   │                          │                                               kvist --frontend stdio-multi
   │                          ├── providers/<name>: create · wake · sleep · delete · status  (Mode B, NDJSON)
   └─Platega payment page      └── billing.py ◀── Platega callbacks (BILLING_ENABLED=false by default)
```

* **Control server** (`backend/kvist_cloud`) — the only stateful part. SQLite by default,
  PostgreSQL through `DATABASE_URL`. Serves the API, the two WebSockets and the built frontend.
* **Runner** (`runner/kvist_runner`) — one process per machine. It dials the server (the
  machine has no inbound ports), sends a heartbeat every 15 s, starts Kvist for each task and
  relays Kvist's NDJSON events unchanged inside a `{"type":"event","task_id",...}` envelope.
* **Kvist** — the unmodified core, launched as `--frontend stdio-multi` (UI_API.md, Mode B).
  The cloud adds no protocol of its own on top of the event catalogue.
* **Frontend** (`frontend/`) — static files, no framework. The pricing page is prerendered HTML
  so it is usable before any script runs.

## Data flows

### Sign-up to first task (the funnel)

1. `pricing_view` — the pricing page, trial preselected. The CTA is a plain link to
   `/auth/github/login`.
2. `github_login` — GitHub OAuth (`read:user` scope only). The callback upserts the user, sets
   the session cookie and **starts creating the machine in the background** before redirecting.
3. `checkout_open` → `card_added` — the free trial starts at once; a paid plan sends the
   person to Platega's payment page (or the dev stub while `BILLING_ENABLED=false`). A plan exists
   once the server has asked Platega and found the payment confirmed.
4. `key_valid` → `first_task_started` → `first_task_done` — onboarding is one screen: provider
   tile, key (checked with one cheap request through the server as soon as it is pasted),
   repository, one of three ready-made tasks, "Run". The machine has been waking up meanwhile.

### A task

```
browser POST /api/tasks ─▶ limits.admit() ─▶ queued | starting
                                   │
                   machine asleep? provider.wake() ─▶ runner connects ─▶ gateway sends start_task
                                                                         {goal, repo, key, limits}
runner: git clone → spawn kvist (key only in its env) → {"type":"goal"} on stdin
kvist stdout NDJSON ─▶ runner scrubs the key ─▶ gateway ─▶ task_events (significant ones)
                                                        └▶ live fan-out to browser sockets
task status is derived from the events: task_state running/awaiting_input/paused, final, error
turn_end ─▶ runner closes stdin (or keeps it for the plan's background window) ─▶ key gone
```

An answer to an `input_request` goes browser → server → runner → Kvist's stdin unchanged.
Pause and cancel are Kvist's own `{"type":"pause"}` / `{"type":"cancel"}` messages.

### A conversation (more messages in the same task)

A task is a session: after the first answer the person keeps writing to it.

```
browser POST /api/tasks/{id}/messages {text}
   status running / waiting_for_user ─▶ {"type":"message"} to the runner ─▶ Kvist queues it behind its turn
   status done / paused / cancelled / failed ─▶ pending_text = text, admit() ─▶ start_task {goal: text, session, follow_up}
                                                                   │
        runner: the same process if it is still up (finishing its turn, or kept by background work);
                otherwise a new Kvist: `command session switch <session>` ─▶ then the goal
```

* The text is also stored as a `user` event in `task_events`, so a reloaded page shows the whole conversation
  (the core's own journal never reaches the server). Tasks from before this have no first `user` event; the page
  puts the goal in front of the history.
* `session` is the name Kvist gave the conversation (the latest `session` event, else the first `hello`).
  `Task.pending_text` holds a message until the machine's Kvist says it has it (`hello` or `task_state: running`),
  so a machine that is asleep, or a plan with no free slot, loses nothing.
* The runner keeps Kvist up past `turn_end` only while a message is queued behind that turn (`_goals_waiting`);
  otherwise it still closes stdin at the end of the turn, so the model key is in no process while the agent is idle.
* A late `task_exit` of the previous run does not end the new one (`pending_text` set while `starting`/`queued`),
  and a message that reaches a run that has just ended comes back as `message_undelivered` and starts the next run.
* Every message needs hours, a key and a subscription, like a first goal (`can_run`); one that arrives while the task
  is `queued`/`starting` is refused with `not_ready` (409).

### Heartbeat

Every 15 s, and immediately whenever the set changes, the runner sends
`{"type":"heartbeat","running":[task ids]}`. The server bills the interval between two
consecutive heartbeats (see below). No other signal ever creates a charge.

### Archive export

`POST /api/machines/{id}/export` (whole machine) or `POST /api/tasks/{id}/export` (one task's
folder) creates an `exports` row, wakes the machine if needed and asks the runner over the
socket. The runner packs the folder into a `.tar.gz` and uploads it with an HTTP `PUT` carrying
the machine token. The browser polls the export and downloads it. Export goes through the
runner, not the provider, so it works the same on any hosting.

### Sign in with ChatGPT (`chatgpt.py`)

The customer's ChatGPT plan can pay for their model instead of an API key. The sign-in runs on the server, so
nothing listens on the customer's computer:

1. `POST /api/chatgpt/start` opens an attempt (3 minutes, one use, bound to the signed-in customer) and returns
   OpenAI's address. Only the hash of its `state` is stored; the PKCE verifier is encrypted.
2. OpenAI sends the browser back to `/auth/chatgpt/callback`. The attempt is looked up by that state, deleted at
   once, and its code is exchanged for tokens.
3. The tokens are stored as one encrypted blob on the user row (they must be sent back to OpenAI, so a hash would
   be useless). The identity token is read without its signature: it came straight from OpenAI's token endpoint
   over TLS (OpenID Connect Core 3.1.3.7).
4. A refresh token is replaced whenever it is used, so `fresh_access_token` is the only place that renews it, under
   a per-customer lock and outside any database transaction. A machine only ever gets the short-lived access token.
5. Once a day (`CHATGPT_CHECK_SECONDS`) every saved sign-in is renewed. One that OpenAI refuses becomes
   `needs_login` and the customer signs in again; OpenAI being unreachable changes nothing.
6. A task of a ChatGPT customer starts without a key: `start_task` carries the access token (`chatgpt`) and Kvist's
   provider is `chatgpt`. The runner writes the token into Kvist's config (`home/.kvist/config.json`) as a "lent"
   account, the form a core serving someone else's frontend holds, and hides it from events like a key. Every
   four minutes the server looks at each connected machine of such a customer and sends `chatgpt_access` when
   the token changed (it is renewed ten minutes before it ends); Kvist re-reads its config on every request, so
   a long task keeps working. A sign-in that is gone pauses the task (`invalid_key`) instead of failing it.

### Session addresses and who may open them

Every task has a **public id** (`tasks.public_id`: 72 random bits, url-safe; not the row number, never reused) and
the workspace shows each session at its own address, **`/pixel/s/<id>`** (a draft in a project at `/pixel/p/<project>`).
Typing the address, following a link or reloading opens that session without any button; the address bar follows
what is in front, so back and forward move between sessions.

The page at that address is the same for everyone (`pixel/index.html`); what it shows it asks the API for. The API
resolves a session only for its owner: `GET /api/sessions/<id>` (and `/api/tasks/{n}`, `/events`, `/stream`, the
screen routes) answer another account exactly as they answer an id that does not exist (404), and a signed-out visitor
is sent to sign in and back to the same address. The page says "no such session, or it belongs to another account"
without telling which. Knowing a session's id opens nothing for anyone else.

The workspace also warns before things bite: no trial, no hours or few left (under 30 minutes or 10% of the plan),
no model key, no project yet, the server in error, a task that stopped (and why) or failed
(`cloud.warnings`, `views/notices.js`).

### Projects

A project is a named folder on the person's machine: **empty** (just a name) or a **clone of one of their public
GitHub repositories** (`GET /api/github/repos`). Tasks (sessions) belong to a project and work in its folder, so
what one leaves the next finds; older tasks (no `project_id`) keep a folder of their own (`task-N`).

* **Names never repeat.** The name is the folder (spaces become hyphens) and is compared without regard to case,
  so "My app", "my app" and "my-app" are one project: `409 name_taken`, enforced by the database
  (`UNIQUE(user_id, folder_key)`) and checked first for the message. Allowed: letters, digits, spaces, dots,
  hyphens, underscores, 60 characters at most; no `/`, no leading dot or hyphen, and not `task-<digits>` (those are
  the older tasks' folders). Two people may use the same name: their machines are separate.
* **The folder is made on the machine** (`prepare_project`: created, or cloned) as soon as the project is added if the
  machine is awake, and for every project each time its runner connects, so one added while the machine slept
  appears when it wakes. A folder that exists is left alone. The runner refuses a folder name that is not a plain
  name (`project_dir`), whoever sent it.
* **One session per task inside the shared folder.** Kvist keeps its sessions per folder, so a task would otherwise
  continue the project's last conversation. A task's session is named `task-<id>`: its first run opens it
  (`command new`), later runs switch back into it (`command session switch`); a first run that finds the name taken
  (it died after opening it) switches in too.
* Exports of a task pack its project's folder (`folder` in the `export` message).

### The agent's screen (private)

The browser and desktop skills draw on a virtual screen that Kvist serves on a loopback address of
the machine, behind a secret token. What the agent does there is as private as its files, so:

1. **The address and token never leave the machine.** The runner rewrites every `screen` event
   (`url`, `stream_url`, `frame_url` emptied) and keeps the address to itself. The server whitelists
   the event again (`screens.public_view`) and puts its own address in `frame_url`.
2. **Pictures are pulled only while the owner looks.** The owner's page polls
   `GET /api/tasks/{id}/screens/{screen}/frame.jpg`; each poll renews a 10 s lease on the machine
   (`screen_watch`), and the runner fetches a 960 px JPEG from its loopback viewer about twice a
   second until the lease runs out. Pictures are sent over the machine's one authenticated socket
   as `screen_frame`; a frame is dropped, never queued, when the link is down or backed up.
3. **Memory only.** Frames live in one process dictionary (`ScreenStore`, at most 64, each at most
   1 MB and checked for a JPEG header), go stale after 10 s and are forgotten when the screen
   closes or the task ends. They are not in the database, `task_events`, logs or exports, and a
   `screen` event is not persisted either.
4. **Owner only.** The route resolves the task with the same `owned_task` as every other task route:
   another account, an admin and a signed-out visitor get the answer for a task that does not
   exist (404; 401 when signed out), not even a hint that a screen exists. The live stream
   refuses non-owners the same way. The route also needs the `X-Kvist-Client` header, which an
   `<img>` on another site cannot send, and answers `private, no-store`.
5. **A machine can only speak for its own tasks.** A `screen_frame` for a task the machine does
   not own is dropped and logged.

Tests: `backend/tests/test_screens.py` (isolation between accounts, admin, signed-out, stream,
storage, limits) and `runner/tests/test_screens.py` (address stripped, loopback-only fetch, lease).

## Hours ledger rules

`ledger_entries` is append-only. Every row has a signed `seconds` value and a `kind`:
`grant` (+, plan hours for a period), `purchase` (+, a bought pack), `refund` (+, a correction
with a `reason`), `usage` (−, agent time). The balance of a period is the sum of its rows.

1. **What is billed.** Time while a task's status is `running`: the agent thinks, waits for the
   model or runs a command. `waiting_for_user`, `paused`, `done`, `cancelled`, `failed` and
   `queued` are free.
2. **Only heartbeats count.** On heartbeat *k* (server clock, never the runner's) the server
   bills `floor(t_k) − floor(t_{k−1})` seconds if heartbeat *k−1* named at least one task, the
   gap is at most `HEARTBEAT_MAX_GAP_SECONDS` (default 45 s, three missed beats) and those tasks
   are `running` on the server too. A longer gap bills nothing: the meter stopped at the last
   heartbeat and restarts from the next one.
3. **Parallel tasks count once.** One interval of one machine is one charge, whatever the number
   of tasks in it: the union per machine, not the sum per task. The seconds are split between
   the tasks only for display (`s // n` each, the remainder one second at a time in id order),
   so the per-task feed adds up to exactly what was charged.
4. **Per second, shown in minutes.** Rows store whole seconds; floors of absolute timestamps
   telescope, so no fraction is lost or invented across heartbeats. The UI shows
   `floor(seconds / 60)` minutes.
5. **No UPDATE, no DELETE.** The ORM refuses both on `LedgerEntry` (mapper and bulk-statement
   guards) and the migration installs database triggers that abort them. A correction is a new
   `refund` row with a reason.
6. **Hours run out mid-task.** The server stops billing at zero (it never charges past the
   balance) and sends the runner `pause` with a 5-minute grace: Kvist pauses at its next safe
   point, normally after the current command; if it has not paused after 5 minutes the runner
   sends `cancel`, which keeps the checkpoint. The task becomes `paused` with reason
   `hours_exhausted`. Nothing is deleted; buying hours lets the person resume it.
7. **Model provider failure.** A wrong key (401/403) pauses the task at once. 429 and 5xx count
   only if they last 5 minutes without a successful model call; then the task is paused with
   reason `provider_unavailable`. Paused means the meter is stopped. Waiting for the model
   during those 5 minutes is normal "running" time.

## Limits (`limits.py`)

* **Parallel tasks.** A task beyond the plan's limit is `queued` and starts when a slot frees.
  `starting`, `running` and `waiting_for_user` occupy a slot; `paused` does not.
* **Command time.** Sent to the runner with every task. The core caps a foreground command at
  300 s today, below every plan's limit, so in practice the limit applies to background jobs:
  a job older than the limit makes the runner stop the task (`cancel`, checkpoint kept, reason
  `command_time_limit`).
* **Background.** `background_minutes = 0` (trial) launches Kvist without `--wake`, so jobs die
  with the turn. Otherwise the runner keeps Kvist alive after `turn_end` while jobs run, up to
  the plan's window, then closes it. Background time is not billed; a wake-up turn is.
* **Machine size.** On the local provider the runner wraps Kvist in
  `systemd-run --user --scope -p MemoryMax=… -p CPUQuota=…` when systemd is available, so every
  command the agent starts inherits the limits. Without systemd it logs a warning and runs
  without them.

## Secrets

* Model keys are encrypted with Fernet (`KVIST_CLOUD_MASTER_KEY`, from the environment only).
  The API returns only the last four characters.
* The key is decrypted only to build a `start_task` message. The runner puts it into Kvist's
  environment and nowhere else, scrubs it from every line Kvist prints before relaying it, and
  drops it when the process ends. The server scrubs relayed events a second time.
* Logs never receive request bodies. Unhandled errors return a plain message and a support code;
  the traceback goes to the log with secrets scrubbed.
* The local provider starts the runner with a clean environment, so the server's master key and
  session secret never reach a machine.
* `tests/test_secret_leaks.py` checks logs, API responses, events and exceptions for the key.

## Honesty rules the UI keeps

The trial's length and the price after it are on the pricing page next to the button, before
any card. Cancelling is one button in Settings plus one confirmation. Two days before the trial
ends the dashboard shows a banner and an email is queued: "In 2 days we will charge $15". No
countdowns, no false urgency, no preselected paid options.

## Performance

The pricing page is prerendered per locale with its CSS inlined; its button is a link, so it is
interactive as soon as the HTML is parsed. The app bundle is one ES module with both locales.
Budgets are enforced by `frontend/build.mjs`: first screen ≤ 100 KB transferred.

Lighthouse results are recorded in the "Lighthouse" section below.

## Lighthouse

Measured on 2026-11-05 with Lighthouse 12.8.2 (`frontend/tools/lighthouse.sh`) against
`scripts/dev.sh` on this computer: mobile form factor, slow 3G (400 ms round trip, 400 kbit/s, CPU
slowed 4×). The first screen a person sees is the pricing page; the other two are the funnel's
screens behind sign-in.

Chrome throttling the network for real (`METHOD=devtools`, the default):

| Screen | Perf | A11y | Best pr. | FCP | LCP | TTI | CLS | Bytes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Pricing, en | 100 | 100 | 100 | 0.56 s | 0.60 s | **0.57 s** | 0 | 59 KB |
| Pricing, ru | 100 | 100 | 100 | 0.64 s | 0.64 s | **0.64 s** | 0 | 72 KB |
| Card step (`/start`, goes on to onboarding) | 90 | 100 | 100 | 0.54 s | 3.07 s | 2.23 s | 0.126 | 96 KB |
| Onboarding, ru | 96 | 100 | 100 | 0.57 s | 2.81 s | 2.38 s | 0.030 | 95 KB |

Lighthouse's own model (`METHOD=simulate`) on the same pages: pricing en 3.2 s, ru 4.0 s,
onboarding 5.3 s TTI. The model counts the webfont as blocking the first paint whatever
`font-display` says; with `optional` the browser paints in the system monospace at once, which is
what the devtools run shows. Both are kept in `.lighthouse/` by the script.

The first screen is within the brief: under 100 KB, interactive in 0.6 s. The screens behind
sign-in are at 2.2–2.4 s: the HTML, one 22 KB bundle and one round trip to the API at 400 ms
each. What would bring them under 1.5 s, not done in the MVP: inline the bundle into the shell
(one round trip less; needs a CSP hash) and build one bundle per locale (−6 KB).

What moved the numbers: gzip on every response (the pricing HTML went from 20 KB to 5.5 KB),
`font-display: optional` instead of the landing's `swap` (no late reflow, no font in the first
paint), no font preload, and the data the first screen needs put into the page by the server.

## Decisions

Where the brief left a choice open, the simple option was taken and written down here.

1. **Branch.** The brief said "work in main"; the repository's CLAUDE.md forbids committing to
   `main` directly. Work is committed on `experimental` and reaches `main` by a fast-forward
   after review.
2. **Landing source.** The brief names `web/`; the landing lives in `site/`. Its tokens and
   fonts were copied into `frontend/` (nothing outside `cloud/` is changed).
3. **No frontend framework.** Same stack as the landing (vanilla JS, esbuild). A framework's
   runtime alone would eat most of the first-screen budget.
4. **"Other" provider = any OpenAI-compatible endpoint** (base URL, key, model). Kvist runs it
   through its `omniroute` provider, configured from the environment. The local dev model
   gateway is reached the same way, so there is no dev-only provider.
5. **One key per account.** Stored on the user row (`model_provider`, `model_key_enc`,
   `model_key_last4`); there is no keys table.
6. **One workspace folder per task** (`work/task-<id>` on the machine). Parallel tasks never
   edit the same checkout; the task archive is that folder, the machine archive is all of them.
7. **Public repositories only.** Login asks for `read:user` and nothing else; asking for `repo`
   at the first screen scares people away. Private repositories need a separate grant later.
8. **Hours do not roll over.** Plan hours and bought packs belong to the current billing period.
   The trial is its own period of 7 days.
9. **The 5-minute grace after hours run out is free.** Billing stops at zero rather than
   running the balance negative.
10. **Approvals.** Kvist runs with `--role universal`, `--approval-mode ask`, `AGENT_AUTO_APPROVAL=1` (the auto-mode classifier answers approval questions and the person is asked only when it cannot decide) and `AGENT_WRITE_APPROVAL=0`: a
    dangerous command still asks the person (status `waiting_for_user`, meter stopped), file
    writes inside the task folder do not. Network inside the sandbox is allowed
    (`AGENT_SANDBOX_NET=allow`): a cloud machine without network cannot install dependencies.
11. **Provider errors are recognised from Kvist's error text** (401/403, 429, 5xx patterns).
    A structured event would be better; see "Core changes wanted".
12. **Dev sign-in.** With `AUTH_DEV_LOGIN=true` and no GitHub app configured, the GitHub button
    signs in a local dev user. It refuses to start when `PUBLIC_URL` is not a loopback address.
13. **Single server process.** Live event fan-out is an in-process pub/sub. Several server
    instances would need PostgreSQL `LISTEN/NOTIFY` or similar; not needed for the MVP.
14. **Paused tasks release the machine.** On pause the Kvist process ends (the key with it);
    resuming starts a new process with Kvist's `resume` command and the key sent again.
15. **Emails are queued, not sent.** The trial reminder is written to the `outbox` table; wiring
    an email provider is outside the MVP.
16. **`font-display: optional`**, unlike the landing's `swap`. On slow 3G a late swap moved the
    largest paint by seconds and reflowed the page; now the first visit may render in the system
    monospace and the next one in Kvist Mono from the cache.
17. **The app shell carries the first screen's data** (`<script id="boot" type="application/json">`):
    the person, subscription, billing settings, key summary, providers. It saves one or two round
    trips; a later screen inside the app asks the API as usual.
18. **A chat reply ends a task as done.** Kvist ends a chat turn with `message` + `turn_end` and no
    `final` (UI_API.md); a turn with neither a reply nor a final is a failure.
19. **The local model is a fake OpenAI-compatible server** (`scripts/fake_model.py`) that drives the
    real core through a short script, reached as the "Other" provider. Kvist's tool-calling probe
    (`ping`) and role router are answered so the core runs as it would against a real model.
20. **Lighthouse is reported with real throttling** (`devtools`) next to Lighthouse's model
    (`simulate`), because the model ignores `font-display` (see "Lighthouse").
21. **Hard-coded runner environment.** Kvist runs with `AGENT_WRITE_APPROVAL=0` and
    `AGENT_SANDBOX_NET=allow` (decision 10) and its own `HOME` and `KVIST_CONFIG_DIR` inside the
    machine folder, so the developer's own Kvist config is never used by a local machine.

## Core changes wanted

Not made (the brief forbids changes outside `cloud/`); listed here and in the final report.

1. A structured `provider_error` event (`status_code`, `retrying`, `retry_in`) instead of text
   matching on `error`/`status` messages.
2. An inbound `{"type":"job_stop","job_id"}` so the runner can stop one background job past its
   time limit instead of the whole task.
3. A configurable ceiling for foreground command time (`MAX_SHELL_TIMEOUT_SECONDS` is a fixed
   300 s) so the plans' 30 min / 2 h / 6 h command limits can mean what they say.
