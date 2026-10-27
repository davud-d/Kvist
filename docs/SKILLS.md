# Kvist skills

A skill is an installable package that gives Kvist instructions, tools or both. Skills live
outside the core, are read without being executed, are approved by the person once per
version and workspace, and run in an OS sandbox with only the capabilities that were granted.

Russian version: [SKILLS.ru.md](SKILLS.ru.md).

Contents: [what is a skill](#what-is-a-skill) · [package layout](#package-layout) ·
[manifest](#manifest) · [plugin.py](#pluginpy) · [capabilities](#capabilities) ·
[ctx API](#the-ctx-api) · [sandbox](#what-the-runner-sandbox-guarantees) ·
[network and previews](#network-and-the-preview-screen) · [pause and stop](#pause-stop-all-and-cleanup) ·
[helpers, memory, sessions](#helpers-memory-and-past-conversations) · [managing skills](#managing-skills) ·
[building a skill](#building-a-skill-with-kvist) · [MCP](#mcp-servers) · [connections](#service-connections) ·
[bundled skills](#bundled-skills) · [platforms](#platforms-and-limits)

## What is a skill

| Kind | Contains | Needs the runner |
| --- | --- | --- |
| `instructions` | a Markdown file the model reads while the skill is attached | no |
| `tools` | instructions plus a Python module that implements tools | yes (Linux) |

What the model sees:

* A short catalog of installed skills (id, name, one-line summary). Reading the catalog
  never imports a package.
* After `skills attach <id>` and the person's approval: the skill's instructions (bounded,
  quoted as untrusted package text that cannot override the system rules, the reader role or
  granted permissions) and its tools under wire names `<skill>__<tool>`. A skill cannot claim a
  built-in name or another skill's name; collisions are refused.
* Reference pages: a skill may ship longer Markdown pages in `references/*.md`. The attach
  answer lists them, and the model reads one with `skills read` when the instructions send it
  there (`terminal` or `terminal.md` is enough). They are quoted like the instructions and never
  enter the per-step prompt, so a long skill costs only the pages a task actually reads.
  `check` refuses a page over 64 KiB or not in UTF-8 and a page the instructions name but the
  package lacks, and warns about a page the instructions never name.
* At most 4 skills are attached to one task.

The model uses small built-in tools to work with the platform: `skills`
(`list`, `attach`, `detach`, `read`, `status`, and the builder ops `scaffold`, `check`, `install`),
`memory`, `sessions`, `helper`, `ask_user`, and the timers `wait` (a background pause that wakes the agent, up to
24 hours) and `schedule` (`add`, `list`, `update`, `cancel`, `pause`, `resume`: a one-time or recurring wake-up with
a task, stored in `<workspace>/.kvist/schedules.json`). Timers fire only while Kvist runs in an interface that can
wake the agent (the Ink terminal app and the IDE panels, `--wake`). Skill tools are mapped to the internal action type `skill_call`, so every
guard of the loop (repeat guard, reader/plan barriers, journal, pending action) applies.

## Package layout

```
<id>/
  manifest.json      required, static, validated before anything runs
  SKILL.md           instructions (path set by "instructions")
  references/*.md    optional longer pages, read on request (`skills read`), 64 KiB each
  plugin.py          only for kind "tools"
  tests/             optional, run by `kvist --skill-op check` (see below)
  ...                other files, size and count limits apply
```

Installed packages are stored as `<config_dir>/plugins/skill/<id>/versions/<version>/`
(immutable) with an `active.json` pointer, a per-skill `data/` and a managed dependency
environment `env/<key>/site`. Grants are in `<config_dir>/plugins/grants.json`, keyed by
skill, content digest and workspace. `<config_dir>` is `~/.kvist` or `KVIST_CONFIG_DIR`.

Package limits: manifest 64 KiB, instructions 64 KiB, at most 500 files, 20 MiB, no
symlinks, no files outside the package.

## Manifest

`manifest.json`, `manifest_version: 1`, `plugin_api: 1` (an integer, or
`{"min": 1, "max": 1}`). Unknown top-level keys are refused.

```json
{
  "manifest_version": 1,
  "id": "report_writer",
  "version": "0.1.0",
  "plugin_api": 1,
  "kind": "tools",
  "name": {"en": "Report writer", "ru": "Составитель отчётов"},
  "summary": {"en": "Builds a short report from test output.", "ru": "Собирает короткий отчёт."},
  "instructions": "SKILL.md",
  "entrypoint": "plugin.py",
  "platforms": ["linux"],
  "dependencies": {"python": ["jinja2==3.1.4"], "system": []},
  "capabilities": {
    "required": [{"name": "files.read", "reason": "to read the logs", "scope": {"paths": ["logs/**"]}}],
    "optional": [{"name": "artifacts", "reason": "to save the report as a file"}]
  },
  "limits": {"call_timeout_seconds": 60, "max_result_bytes": 65536},
  "tools": [{
    "name": "build",
    "summary": "Build the report from a log file.",
    "readonly": true,
    "parameters": {"type": "object", "additionalProperties": false,
                   "properties": {"path": {"type": "string", "maxLength": 500}}, "required": ["path"]},
    "result": {"type": "object", "properties": {"report": {"type": "string"}}}
  }]
}
```

Rules:

* `id`: lowercase letters, digits and `_`, 2–32 characters, never `mcp`, `kvist`, `core`,
  `skill`, `skills`, `builtin`, `system`, `memory`, `helper`. Tool names follow the same
  pattern. Neither contains `__` (the wire-name separator).
* `version`: `MAJOR.MINOR.PATCH`. Installing the same version with different content is
  refused: bump the version.
* `platforms`: `linux`, `darwin`, `win32`. A `tools` skill runs only on Linux (see
  [platforms](#platforms-and-limits)).
* `dependencies.python` are exact pins (`name==version`) from PyPI as binary wheels;
  `dependencies.system` are names only, reported to the person and never installed by the core.
* `limits`: `call_timeout_seconds` (default 120, max 1800), `max_result_bytes` (256 KiB,
  max 1 MiB), `memory_mb` (2048), `model_calls_per_task` (20), `model_tokens_per_task`
  (200 000), `max_runtime_seconds` (3600, max 6 h).
* `tools[].parameters` and `result` are JSON Schema subsets checked by the core
  (`kernel/plugins/schema.py`); arguments are validated before the call and a result that
  breaks `result` or `max_result_bytes` is refused.
* `readonly: true` declares a tool that never writes. Reader runs and plan mode may call
  only tools that are `readonly`, and the core enforces this again on every nested service
  call: a `readonly` tool that tries `ctx.files.write` is refused at the call with
  `Denied`, and so is any write while the plan is not accepted or the run is read-only.
* `describe()` in `plugin.py` may narrow the tool list, never widen it or change a tool's
  parameters, and it cannot add capabilities.

## plugin.py

```python
def describe() -> dict:
    return {"tools": ["build"]}

def execute(tool_name: str, arguments: dict, ctx) -> dict:
    if tool_name == "build":
        text = ctx.files.read(arguments["path"])["content"]
        ctx.ui.notify("building the report")
        return {"report": text[:2000]}
    raise ValueError(tool_name)
```

* Python standard library only unless the manifest pins dependencies. The module is
  imported inside the sandbox, never in the core.
* Exceptions become a failed tool result with a redacted message. The SDK exceptions
  are importable inside the runner (`from kvist_skill import SkillError, Denied, Cancelled,
  Paused, Unavailable`) and carry a stable `code`.
* Stdout and stderr are diagnostics only and never reach the model.
* Return JSON-serialisable data. Large content belongs in `ctx.artifacts.save`.

## Capabilities

The catalog is closed (`kernel/plugins/capabilities.py`); a manifest naming anything
else is refused. Required capabilities are approved with the skill. Optional ones are
requested only when the skill asks (`ctx.permissions.request`). Granting is per
skill + content digest + workspace: **a new version, or the same version with changed
content, starts with no grants**.

| Capability | Lets the skill | Writes | Leaves the machine |
| --- | --- | --- | --- |
| `input.read` | read the task, follow-ups, attachment list | | |
| `images.read` | read image details, make cropped/resized copies | | |
| `images.original` | receive original image files | | |
| `images.analyze` | send images to the configured Vision model | | yes |
| `models.call` | call the configured language models (counted in the task) | | yes |
| `files.read` | read workspace files (protected files hidden); scope `paths` | | |
| `files.write` | create/change files (journaled, revertable; no delete or move); scope `paths` | yes | |
| `changes.read` | see this task's changes | | |
| `git.read` | git status and diff | | |
| `processes.run` | run commands in the workspace sandbox | yes | |
| `processes.background` | keep a command (dev server) running for the task | yes | |
| `web.search`, `web.fetch` | search / fetch pages as text | | yes |
| `http.request` | HTTP to listed domains (scope `domains`) | yes | yes |
| `network.internet` | let its own programs reach listed domains | | yes |
| `network.task_services` | let its own programs reach servers this task started | | |
| `connections.use` | use a configured connection by name (scope `connections`) | | yes |
| `ui.notify`, `ui.ask`, `ui.present` | status lines, questions, show images/files | | |
| `sessions.read` | read/search earlier conversations of this workspace | | |
| `memory.read`, `memory.write` | read / add to and change this workspace's memory (as `observed` or `inferred`, never on the person's behalf) | | |
| `storage` | the skill's own key-value data | | |
| `artifacts` | save result files | | |
| `agents.manage` | start a read-only helper | | |
| `display` | run programs on its own virtual screen | | |
| `host.open` | open http(s) addresses in the person's own browser, start installed applications (at most 12 per task, an address with a secret in it is refused). **Core tools only**, see below | yes | |
| `host.windows` | list the person's windows, focus one, close one after the person confirms (Hyprland, Sway, niri, i3, X11 with `wmctrl`, GNOME with the Window Calls extension, KDE with `kdotool`, macOS, Windows). **Core tools only** | yes | |
| `permissions.request` | ask for more access during a task | | |

Capabilities that write are refused in reader runs and plan mode whatever the grant says.
A package with no capabilities and no Python (an instruction-only skill) is
installed and attached without the runner.

## The ctx API

`ctx` is created by the runner per call. Every method is synchronous from the skill's
point of view (it sends a request to the core and waits). Every call is checked against
the grant, the call's budget and the cancel/pause state; a denied call raises `Denied`.

| Group | Methods |
| --- | --- |
| `ctx.input` | `task()`, `attachments()` |
| `ctx.images` | `info(id)`, `analyze(ids, question)`, `crop(id, box)`, `resize(id, w, h)`, `convert(id, format)`, `original(id)`, `model_copy(id)`, `add(path, name)` |
| `ctx.models` | `list()`, `complete(prompt)`, `stream(prompt)`, `json(prompt, schema)`, `count_tokens(text)` |
| `ctx.files` | `read`, `read_many`, `list`, `tree`, `find`, `search`, `write`, `append`, `replace`, `read_bytes`, `mkdir` |
| `ctx.changes` | `list()`, `diff(path)`, `preview(path, content)` |
| `ctx.git` | `status()`, `diff(path, ref, staged)` |
| `ctx.processes` | `run(command, cwd, timeout)`, `start(command, cwd, expose_port)`, `output(job_id, wait)`, `status`, `stop`, `list` |
| `ctx.web` | `search(query)`, `fetch(url, max_chars, mode)` |
| `ctx.http` | `request(method, url, headers, body, json)` |
| `ctx.connections` | `list()`, `request(name, method, path, ...)` |
| `ctx.ui` | `notify`, `message`, `error`, `stream`, `choose`, `ask`, `confirm`, `present` |
| `ctx.sessions` | `list()`, `read(session, before, limit)`, `search(query)` |
| `ctx.memory` | `search(query, kinds, include_inactive)`, `get(id)`, `explain(id)`, `list()`, `remember(content, kind, basis, topic, files)`, `update(id, expected_version, reason, …)`, `link(source_id, target_id, relation, reason)`, `forget(id)` |
| `ctx.storage` | `get`, `set` (optional `expected_version`, `ttl_seconds`), `compare_and_set(key, expected_version, value)`, `delete`, `keys`; scope `call`, `task`, `session` or `workspace` |
| `ctx.runtime` | `status()`, `check()`, `sleep(seconds)`, `progress(text)`, `register_resource`, `release_resource` |
| `ctx.events` | `poll(after, timeout)` — task events, acknowledged by passing `next` as `after` |
| `ctx.agents` | `start`, `message`, `update`, `status`, `wait`, `inspect`, `result`, `stop` |
| `ctx.environment`, `ctx.settings`, `ctx.permissions` | `info()`, `get()`, `current()` / `request(capability, reason, scope)` |
| `ctx.artifacts` | `save(path, name, kind, caption)`, `list()` |
| `ctx.display` | `info()`, `screenshot(name)` |
| `ctx.network` | `proxy()` — the address of the core's outbound proxy inside the sandbox |
| `ctx.log` | `write(text, level)` |

Contract notes:

* **File paths** are workspace-relative. `.env*`, `.kvist/`, keys and the other protected
  paths (`secret_paths.py`) are refused for read and write. Writes go through the same
  journaled path as the model's own writes (`/changes`, `/revert` work), with the
  existing overwrite approval. `ctx.files` has no delete, move or binary write by design.
* **A `paths` scope** is matched against the path's canonical workspace location, after
  `..` and symlinks are resolved, by every file method. `list`, `tree`, `find` and
  `search` need a directory the scope covers whole (`logs/**` covers `logs`), because
  they report everything below it; without a `path` they search the workspace root,
  which only an unscoped grant covers.
* **The exchange directory** is writable by the skill, so the core never follows a
  symlink there: a file it hands over is created anew in a real `in/` directory, and a
  file the skill passes back must be a regular file reached without symlinks.
* **`ctx.processes.run`** uses the shell sandbox, its command policy and approvals; a
  skill gets no way around them. `expose_port` registers a port of a background server so
  the skill's own programs can reach it through the broker.
* **Cancellation and pause.** A stop or pause is delivered at the skill's next request:
  the request raises `Cancelled` or `Paused`, which is the cue to clean up and return
  (a partial result is allowed). `ctx.runtime.sleep` ends early the same way and
  compute-bound code should call `ctx.runtime.check()` between steps. After a short grace
  (3 s for a pause, 5 s for a cancel) a runner that did not return is dealt with by the
  core: a paused one is frozen in place with the cgroup freezer (its code cannot act while
  the interface says "paused") and its call is cancelled on resume; a cancelled or
  timed-out one is ended. Without the freezer (no cgroup v2) a pause that the skill ignores
  ends the runner instead.
* **Budgets.** `models.call` calls and tokens are counted against the task and the
  manifest limits; the call's wall time against `call_timeout_seconds`; the result size
  against `max_result_bytes`.
* **Images.** Originals are never replaced: `resize`/`crop`/`convert` create derived
  attachments with their own ids. Image bytes do not enter history or logs; a model sees
  a bounded JPEG copy only when an image is attached or analysed.
* **Results are untrusted data.** Whatever a skill returns reaches the model inside an
  untrusted-output block. A result cannot approve an install, change policy, or mark the
  task finished; completion and verification guards stay with the runtime.

## What the runner sandbox guarantees

An executable skill is run by `kernel/plugins/host.py` in Bubblewrap (the same trusted
system binary the shell sandbox uses):

* new user, PID, IPC, UTS, cgroup and **network** namespaces; `--clearenv`; no inherited
  file descriptors except the wire socketpair;
* the system runtime, the runner code, a *snapshot* of the approved package and its
  dependency environment are mounted read-only; the snapshot is verified by digest before
  every start, so a package changed between approval and launch is refused;
* private `/tmp` and HOME; **no** workspace, home directory, Kvist config, display
  or environment variables of the person; the config directory is also hidden from shell
  commands when it lies inside a workspace;
* memory and PID limits and a cgroup scope when cgroup v2 is available;
* no direct network: its own loopback only, and outbound traffic only through the core's
  broker (below).

Not guaranteed (honest limits): the kernel is shared with the host (a kernel escape is
out of scope); an approved capability does what it says (a skill granted `http.request`
to a domain can send data there); `files.write` can change workspace files within the
journal rules. Chromium's own internal sandbox is turned off by Playwright inside the
runner (it cannot nest); the outer Bubblewrap boundary applies instead.

## Network and the preview screen

* **Broker.** The core runs an HTTP proxy on a unix socket mounted into the sandbox. It
  allows, per request: ports of servers the task started (`network.task_services`),
  domains from `network.internet` / `http.request` scope, and nothing that resolves to a
  private, loopback or link-local address. Redirects are re-checked at every hop.
  Dependency installs use the same broker, restricted to PyPI.
* **Dev servers.** `run_shell` with `background` and `expose_port` starts a server in
  its own network namespace; only the broker can reach it, so the host loopback and
  other commands never see the port.
* **Own screen.** The `display` capability runs Xvfb and i3 inside the sandbox. The
  core reads the framebuffer and serves it from a loopback viewer: an MJPEG stream,
  view-only, behind a per-session token (cookie after the first request) with
  Host/Origin checks. Frontends receive a `screen` event and show a preview;
  "open in browser" uses the viewer URL.

## Pause, stop all and cleanup

One task, one life cycle: `running`, `pause_requested`, `paused`, `resuming`,
`cancelling`, `cancelled`, `completed`, `failed`, `awaiting_input`, `idle`
(`kernel/agent/task_control.py`; frontends receive `task_state`).

* **Escape (or the Pause button) pauses** the main agent and every helper at their next
  safe point; the interface shows "pause requested" until they reach it. A skill call in
  flight is asked to return (see above) or frozen in place. The task keeps what it owns, so
  the person can correct it: a dev server, the virtual screen (shown as paused), frozen
  runners and paused helpers stay. The checkpoint keeps the task and the next message
  continues it. An action that was interrupted is not executed half-way: it is not run.
* **Stop all** (Ctrl+C, the "Stop all" button) cancels the task and the helpers and
  releases resources: processes, runners, virtual screens, ports and temporary
  directories under the resource manager. The checkpoint is kept, so `/resume` works.
* A crash leaves markers (pid + start time + environment mark); the next start finds and
  removes the leftovers (`ResourceManager.recover`).
* Helpers pause and stop with the task; a helper the person stopped stays stopped.
* In a one-shot frontend where the core process exits at the pause (single-turn Electron
  runs), live resources cannot survive the pause and are released; the checkpoint
  still allows a continuation.

## Helpers, memory and past conversations

* **Helper** (`helper` tool, `agents.manage`): one agent at a time, in its own service
  session (`helper-…`, never the active or resumable session). Its role is the one named or,
  without one, the router's choice for the task. A reader helper only reads; a coder, devops
  or universal helper the model starts may also change files, unless the main agent itself
  may not (reader role, plan mode). A helper a skill starts through `agents.manage` is always
  read-only. Overwriting an existing file asks the person through the main agent's approvals
  in the event frontends and is refused in the classic terminal. Messages travel through an inbox:
  `sent → received → applied`. `wait` costs no model calls. A helper that the person stopped
  stays stopped until the person writes again. Step limit: 40.
* **Memory** (`memory` tool, `ctx.memory`, `/memory`): records that outlive a session —
  `project_fact`, `decision` (what was chosen and why) and `preference` (always the person's).
  Each says how it came to be: `person` (confirmed by the person), `observed` or `inferred`.
  A skill writes `observed` or `inferred` only and sees only its workspace's records, never
  the person's preferences for every project. Records carry sources, links (`supersedes`,
  `contradicts`, `resolved_by`, …), versions and a history; a change needs the version you
  read (`expected_version`) and a reason. Text with a credential or personal data is refused,
  not redacted. `forget` removes the text from the database and its index; an agent or skill
  cannot store the same content again. While the person has memory paused
  (`/memory pause`), writes fail with code `permission`. Relevant records are quoted in
  prompts as reference, never as instructions. The person manages it with `/memory` or
  `--memory-op status|list|search|get|explain|remember|update|forget|link|pause|resume`.
  `/memory process` has the fast model pick durable findings out of the session's finished turns
  (requests, answers, step summaries; never thoughts or raw output, and lines that look like
  secrets are withheld). It asks the person's consent once; such records are always `inferred`.
  After that the interactive interfaces do it by themselves: a session is read when it has been
  quiet for 10 minutes, when the person leaves it, and at launch (at most 40 calls a day per
  project). Each session also gets a short recap (what it was for, what got done, what is left);
  a new session sees the recaps of the three latest others, so "continue" means something.
  Recaps are not visible to skills.
* **Storage** (`ctx.storage`) is a skill's technical state (cursors, checkpoints, caches), never
  put in a prompt. `compare_and_set` refuses a write when the key moved on, a value with
  `ttl_seconds` reads as absent once it expires, and a value holding a credential or personal
  data is refused like a memory record.
* **Sessions** (`sessions` tool, `ctx.sessions`): list, read and search this workspace's
  earlier conversations (the event journal), never other workspaces. A found message is
  untrusted data.
* **Attachments**: stored with the session, deleted with it; expire after 30 days.

## Managing skills

```bash
kvist --skill-op list
kvist --skill-op show --skill browser
kvist --skill-op install --skill-path ./my_skill            # stage, check, install (not approved yet)
kvist --skill-op install --skill-path bundled:browser       # bring back a bundled skill you uninstalled
kvist --skill-op approve --skill browser                    # asks on the terminal (--yes to confirm)
kvist --skill-op install-deps --skill browser               # pip into the skill's own env
kvist --skill-op revoke | rollback | uninstall | remove-version | reset-data --skill browser
kvist --skill-op check --skill-path ./my_skill              # independent static checks and tests
```

On a terminal the result is drawn for a person (cards, ✓/✗ lines). Pipes, scripts and frontends
get one JSON object, as before; `--json` forces it on a terminal too.

* `install` never executes the package. Atomic: staged under a temporary name, verified,
  then pointed to by `active.json`. Duplicate content is detected.
* `approve` shows what the version asks for, with the scopes, in the person's language.
  The same dialog appears when the model asks to attach a skill that has no grant yet.
  The model cannot approve: that is always a dialog for the person.
* `install-deps` runs pip inside the sandbox through the broker (PyPI only, exact pins,
  binary wheels only) into `env/<key>/site`; nothing touches the system Python.
* `rollback` switches `active.json` to the previous version; grants stay tied to digests.

## Building a skill with Kvist

The model can scaffold a skill with the `skills` tool (`scaffold`, `check`, `install`).
Creating a package, checking it and allowing it to run are three separate operations;
the last one always needs the person.

* A draft is an ordinary folder of the workspace, `kvist-skills/<id>/` by default, written
  through the normal journaled file path (so `/changes` and `/revert` apply). The builder has
  no way to write into installed versions, the grants file, the MCP/connection configs or
  Kvist itself: those live outside the workspace, and `.kvist/` is protected.
* `check` runs the core's independent checks, separate from the author's tests:
  * static: manifest, files, Python syntax (compiled, never executed), the capability
    list against what the tools need;
  * in the sandbox with exactly the declared capabilities: `describe()` matches the
    manifest, every tool answers sample arguments (`tests/contract.json`, or minimal ones
    built from the schema) within its time and size limits and its result schema, a cancel
    ends a call, an undeclared request is refused, nothing keeps running after the runner stops;
  * the author's `tests/test_*.py` under `unittest`, in the same kind of sandbox, without network.

  Passing proves none of this is broken in the cases tried; it does not prove the skill harmless.
* `install` installs exactly the content that passed a check in this task, after a dialog that shows the
  person what the skill does, its permissions and the check results; the model cannot answer it. Similar
  installed skills are reported.
* **An installed skill works only after Kvist is restarted.** It is installed, activated and granted for this
  workspace, but `attach` refuses it in the process that installed it and `list` shows it as unavailable until
  then: a conversation must not change its own tools mid-run. The task that built it keeps working as before.
* Quotas per task: at most **10 checks**, **3 drafts**, **2 installs**; they stop endless
  fix loops.
* Page text, documents, images and MCP replies are untrusted: they cannot grant an
  install or change policy.

## MCP servers

Servers are configured by the person only: `kvist --mcp-op list|add|remove` (stored in
`<config_dir>/plugins/mcp.json`). Version 1 supports **local stdio** servers; remote
HTTP/SSE servers are not supported.

```json
{"servers": {"github": {"command": ["github-mcp", "stdio"], "env": {"TOKEN": "…"},
                        "sandbox": "none", "network": true, "workspace": "none",
                        "readonly_tools": ["search_issues"], "description": "…"}}}
```

* Tools appear as `mcp__<server>__<tool>` only after the server is attached to the task
  and the person approved it; the tool list (names and schemas) is pinned by that approval
  and a changed list needs a new approval.
* A tool counts as read-only only if the person listed it in `readonly_tools`; what a
  server says about itself is not trusted. Other tools are refused in reader runs and plan mode.
* Results are untrusted data (bounded to 200 000 characters); `env` secrets stay in the
  core and never reach the model or skills.
* The server process belongs to the task and stops with it. Cancelling a call sends
  `notifications/cancelled`, but what the server already did cannot be undone and is
  reported as unknown.

## Service connections

`kvist --connection-op add|list|remove`: a name, an `https://` base URL and secret
headers (payload on stdin, never in argv). A skill with `connections.use` calls
`ctx.connections.request("crm", "GET", "/v1/items")`: the core adds the headers, sends
only to that host, follows redirects only on it, never returns the headers, and only
for the methods the person allowed (default `GET`).

## Bundled skills

Preinstalled. At every start (and before every `--skill-op`) Kvist installs a bundled skill
that is missing and moves a bundled skill to the newer version a Kvist update ships, through the
same atomic install as any other package (`kernel/plugins/bundled.py`). It leaves alone a skill
the person installed from elsewhere under the same id, a version the person rolled back from,
and a bundled skill the person uninstalled: that one stays removed (recorded in
`plugins/skill/bundled.json`) until `--skill-op install --skill-path bundled:<id>` brings it back.
Preinstalling grants nothing: a skill with tools still asks the person when it is first attached,
and its Python dependencies still need `--skill-op install-deps <id>`. The model's catalog leaves
out skills this OS can never run; `--skill-op list` shows them with the reason.

| Id | What it does |
| --- | --- |
| `browser` | Chromium + Playwright on its own X11 screen: open pages (workspace files via a local server, task services, granted domains), snapshot, click, type, select, press, scroll, wait, screenshot (described by the Vision model when given a `question`), console/network errors, tabs, upload. `emulate` sets the viewport, the colour scheme and reduced motion; `check_page` measures sideways scroll and its cause, clipped text, contrast below WCAG AA, small targets, unnamed controls, images without alt, heading jumps, `lang`, the character set and failed fonts. Preview in the GUI, viewer link. Needs `xorg-server-xvfb`, `i3` and Chromium; Playwright is installed into the skill's own env. |
| `desktop` | **core tools**, no runner: open an http(s) address in the person's own browser, start an installed application by name, list / focus / close windows (closing asks the person). Text over GUI: the agent finds the exact address with web search and opens it. Works on Linux, macOS and Windows; windows on Linux need Hyprland, Sway, niri, i3, X11 with `wmctrl`, GNOME with the Window Calls extension or KDE with `kdotool`. No arbitrary commands, typing or clicks, and no tabs of a running browser. |
| `visualize` | **core tools**, no runner or extra dependencies: validate structured descriptions and show offline pages with large zoomable diagrams, line/bar/donut charts, data tables/CSV and interactive 3D primitives. Saves an HTML artifact and automatically opens the default system browser. Linux, macOS, Windows; browser failure leaves the saved file available. |
| `test_runner` | pytest / `node --test` / `npm test` with structured results and a log artifact. |
| `documents` | read PDF pages, spreadsheet sheets and ranges (xlsx, csv) and Word paragraphs (docx) with exact page, sheet, cell and paragraph references. |
| `code_nav` | **Python only**: go to definition, find usages, file outline, preview a rename as diffs, pyflakes diagnostics. |
| `project_overview` | one read-only call that answers "what is this project": file counts per language, manifests, test/lint/typecheck/build commands read from the project's own configuration, entry points, top of the README. Needs only `files.read`. |
| `static_checks` | ruff, mypy, tsc, eslint, `go vet` and `cargo check` detected from the project's configuration, findings as file, line, column, code and message, full output as a log artifact. A checker that is not installed is reported as `unavailable`, never as clean; nothing is installed. |
| `debugging` | instructions only, no runner and no permissions: reproduce first, narrow, test one guess at a time, fix the cause, prove the fix; ends with `no_change_needed` when the problem cannot be reproduced. Works on every platform. |
| `skill_authoring` | instructions only, no permissions: when a skill is worth building, instructions or tools, then the scaffold → check → install flow and the restart rule. Three reference pages read on request: writing an instructions-only skill (manifest, shape of `SKILL.md`, how to test prose), a tools skill (manifest, the capability table, `plugin.py` and `ctx` rules, unit tests with a fake context, `contract.json`) and how to read `skills check` reports. The tools it describes are the built-in `skills` ops. Works on every platform. |
| `kvist_docs` | instructions only, no permissions: answers questions about Kvist itself. A short core (how to answer, what holds everywhere) and eight reference pages read on request: install and first launch, commands, keys and flags, providers/models/effort/environment, sandbox and approvals, sessions/undo/memory/timers, interfaces and the protocol, skills and MCP, troubleshooting. Defers to `README.md` and `docs/` when the workspace is Kvist's own repository. Works on every platform. |
| `design` | instructions only, no permissions: interfaces people can use — web apps, websites, phone apps, CLI output and full-screen TUIs, desktop and IDE panels, installers and the first run. A short core (process, the quality floor every interface must reach, first-run rules, the generic look to avoid, a checklist) and eleven reference pages read on request: process and audits, visual foundations with a contrast script (WCAG and APCA), web apps, websites, terminal, interaction patterns, accessibility (WCAG 2.2 AA), writing in English and Russian, first run and installation, desktop and IDE, mobile. Works on every platform. |

## Platforms and limits

* **Core tools** (`core_group` in the manifest) are tools the core runs itself, with the same grant, read-only and plan-mode checks but no package code and no runner. Only a skill Kvist ships may use them (the registry refuses the manifest from any other origin, and `host.*` requests from a sandboxed skill are refused), so they work on every platform the core runs on.
* **Executable skills are Linux-only.** Without Bubblewrap and a trusted system Python
  they are reported unavailable; Kvist never falls back to running skill code with the
  person's rights. Instruction-only skills work everywhere.
* macOS and Windows: not verified; no executable skills. The macOS, Windows, GNOME, KDE and niri backends of `desktop` are covered by unit tests with faked commands only, and the Windows package reader is covered by mocked native-handle tests. Package reads use native Windows handles and reject reparse points; real Windows/macOS end-to-end runs remain unverified.
* The browser skill needs Xvfb, i3 and Chromium on the host.
* The ctx file API has no delete, move or binary write. MCP is stdio only.
* ACP (Zed) shows skill, screen, helper and artifact events as text and has no pause.
* The JetBrains plugin is updated but its Kotlin was not compiled in this change.
* Attachments expire after 30 days; one helper at a time; 4 skills per task.
