# Kvist OpenCode terminal

The terminal interface of [OpenCode](https://github.com/anomalyco/opencode) (TypeScript,
[OpenTUI](https://github.com/anomalyco/opentui) + SolidJS) drawing a Kvist session. Only the
interface is here: OpenCode's server, sessions, providers, tools and agent loop are not. Where
OpenCode has something Kvist lacks, this interface shows what Kvist has instead.

```bash
(cd frontends/tui/opencode && bun install)             # once; needs Bun 1.3.14 or newer
.venv/bin/python -m kernel --frontend opencode          # or /tui, then "OpenCode terminal"
```

There is no build step: Bun runs the TypeScript source. Without Bun, without `bun install`, or
without a terminal, Kvist starts the Ink interface and says why. The release binaries do not
include this interface yet; it runs from a source checkout.

## What it looks like

The home screen has the wordmark, a boxed prompt with a coloured bar and a tip. The wordmark is the one Kvist prints when it exits (the Ink interface's), in its two blues; a tiny window gets OpenCode's small mark. A session is a
transcript: your request in a raised block with a bar, the model's words as markdown, reads and
searches as one muted line each (`→ Read a.py`, `✱ Grep "main"`), writes and edits as raised blocks
with line numbers and a diff, a command with its output (ten lines, click for the rest), a
collapsed `Thought · 3s` line, and `Coder · fast-model · 12s` when a turn ends. Beside the
transcript, on terminals of 120 columns or more, sits a sidebar: the session title, context use,
background jobs and the files the agent changed.

A session the core closed (after abuse) puts the core's notice in the prompt's place, with a "New session" button as in Glass; `Enter` or a click opens a new session. While a turn runs, a spinner line shows what the core says it is doing until words stream in.

Approvals take the prompt's place: `Allow once`, `Allow always` (never offered for a dangerous
command) and `Reject`, with the command or the overwrite diff above them.

## Keys

These are OpenCode's default key bindings (`<leader>` is `Ctrl+X`).

| Key | Does |
|---|---|
| `Enter` / `Shift+Enter` | send / new line |
| `Shift+Tab` | next role (OpenCode's agents); `<leader>` `a` lists them |
| `Ctrl+T` | next reasoning effort (OpenCode's model variants) |
| `Ctrl+P` | the command list |
| `<leader>` then `n` `l` `m` `a` `t` `b` `s` `u` `y` `h` `q` | new session, sessions, models, roles, themes, sidebar, status, undo, copy last answer, help, quit |
| `Ctrl+R` | rename the session |
| `Esc` twice | pause the run and keep its state |
| `Ctrl+C` | clear the input; with an empty input and a run in progress, stop everything; twice at rest, quit |
| `PgUp` / `PgDn` | scroll the transcript |
| `Up` / `Down` | between lines of the text; at its edge, first to the start or end, then through earlier requests. Under an empty first-run box they choose one of three suggested requests (`Tab` or `Enter` puts it in the box) |
| `/` | the slash commands; `Tab` completes, `Enter` runs |
| `@` | a project file; `Tab` or `Enter` writes its path into the request |

Selecting text with the mouse copies it on release.

## What OpenCode has and what stands in for it

| OpenCode | Here |
|---|---|
| agents (build, plan) | Kvist roles: auto, coder, reader, devops, chat, universal |
| model and its variants | the fast and the smart model; reasoning effort |
| connect a provider | `/provider`, `/change-api`, `/settings-api` (the core's own dialogs, drawn as OpenCode dialogs) |
| sessions, fork, undo | `/session`, `/new`, `/revert`, `/changes` |
| `/export` a transcript | `/export` imports sessions from other agents (Claude Code, Codex, OpenCode, Cursor …) |
| todo list, MCP servers | background jobs; the core's skills |
| permission prompt | the core's approvals |
| themes | OpenCode's own theme system and its 33 themes; the core's `theme.json` files work too |

Not present, because Kvist has nothing behind them: share, compaction, workspaces and worktrees,
terminal panes, plugins, the OpenCode account and its model catalogue.

## Themes

OpenCode's theme system is used as it is: its 33 themes, light and dark mode, `/themes` with a
live preview. A theme file is searched in, lowest priority first: the core's `kernel/themes/`,
`~/.config/opencode/themes/`, `~/.kvist/themes/` (or `$KVIST_CONFIG_DIR/themes/`),
`<workspace>/.kvist/themes/`. Themes in the core's 50-slot `theme.json` format are converted on
load; a theme that leaves slots out inherits them from the shipped `kvist` theme. The choice,
the light/dark mode, the sidebar and thinking switches are kept in `~/.kvist/opencode-tui.json`,
a file of this interface; the core's `config.json` is not touched.

## Where the code comes from

Everything under `src/oc/` is **OpenCode's source, copied** (MIT; see `THIRD_PARTY.md`) with the
same relative paths, and cut down to what Kvist has: no server, SDK, plugins, workspaces or
sync. That is what makes it look and behave like OpenCode: theme system, key bindings, dialogs,
toast, spinner, diff view, the logo components and the transcript's message parts. The rest is
Kvist's glue.

```text
src/main.ts        entry the launcher runs (registers Solid's Bun plugin, then cli.tsx)
src/cli.tsx        arguments, renderer, farewell
src/app.tsx        OpenCode's provider stack, global keys, core questions as dialogs
src/kvist.tsx      the one place that talks to the core: process, events, commands
src/commands.tsx   the command list (palette, slash) under OpenCode's command ids
src/tools.ts       how an action is worded and which block it gets   src/files.ts  what "@" offers
src/screens/       home, session, prompt, transcript, sidebar, approval, palette, dialogs
src/oc/            OpenCode's own files (tui/, theme/, plugin/ types), see above
tests/             bun test: pure modules and the whole app against the Ink suite's scripted core
```

The core is reached over `--frontend stdio-multi --wake` like the Ink interface, and the event
reducer, the process adapter and the session operations are the Ink app's own modules
(`frontends/tui/ink/{state,core,session,format,diff,approval,commands}.ts`), imported from there.
A change to them is checked from both sides.

## Development

```bash
cd frontends/tui/opencode
bun install
bun run typecheck
bun test                     # the app tests start frontends/tui/ink/tests/fixtures/fake-core.mjs on Node
SHOW_FRAMES=1 bun test       # prints every frame a test looks at
bun run start -- --workspace /path/to/project
```

OpenTUI needs Bun 1.3.14+ (or Node 26.4+, which this interface does not target). Tests cannot see
colours, only characters; check the look in a real terminal.
