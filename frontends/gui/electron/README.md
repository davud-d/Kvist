# Kvist Desktop

*(Русская версия: [README.ru.md](README.ru.md))*

The desktop app: a thread-based coding workspace on top of Kvist's version-1 NDJSON protocol.
Projects and threads on the left, the conversation in the middle, a review panel and a
commands drawer on demand. It does not import Python or read `.kvist/`; it starts the core as
a child process and speaks only the stdio protocol and the headless `--session-op`,
`--config-op`, `--skill-op`, `--memory-op` and `--journal-op` calls
([`docs/UI_API.md`](../../../docs/UI_API.md)).

It sits beside [`../electron-legacy`](../electron-legacy/README.md): the main-process bridge
(`src/kvist-process.js`, `protocol-state.js`, ...) comes from that app; the renderer is new.

## Run

```bash
cd frontends/gui/electron
npm ci
npm start -- --workspace /path/to/project
```

If `npm ci` leaves Electron without its binary (newer npm blocks install scripts), run
`node node_modules/electron/install.js` once.

In a source checkout the app runs `../../../.venv/bin/python -m kernel`; set
`KVIST_BINARY=/absolute/path/to/kvist` to use a frozen core. `KVIST_CONFIG_DIR=<dir>` tries a
first run against an empty configuration. A provider has to be connected once: *Settings →
Models & providers* takes an API key per provider, signs in with ChatGPT, and adds a custom
OpenAI- or Anthropic-compatible endpoint.

## What is in it, and what stands where Codex's pieces would

| Concept | In Kvist Desktop |
|---|---|
| Threads in projects | Kvist **sessions** in **workspaces**. A thread restores from the session's event journal (`--session-op history`), so a reopened thread looks like the live one. |
| Model and reasoning picker | The chip under the message box: **provider + fast/smart models** for the next task, plus the agent **role** (auto, coder, reader, devops, chat). |
| Permissions: default / full access | **Ask before risky commands** or **Deny risky commands**. There is deliberately no allow-all. A request can offer *Always allow* for its pattern. |
| Approvals | A panel above the box: the command, the overwrite diff, skill and network permissions; *Deny* is focused. A closed window denies. |
| Local / worktree / cloud | **Local** only. A core on a server (`docs/remote.md`) is not wired into this app. |
| Review panel, diff, commit | **Agent changes** (from each write's patch in the thread) and **Working tree** (git, `.kvist/` excluded). *Revert* restores the agent's changes from the change journal. *Commit* writes the message with `--commit-message` and commits only the files you tick. |
| Integrated terminal | A **Commands** drawer (`Ctrl+J`): every command the agent ran or started in the background, with live output. It is not an interactive shell. |
| Plugins, connectors, MCP | **Skills**, **MCP servers** and **service connections** (read-only lists here; manage them with `--skill-op`, `--mcp-op`, `--connection-op`). |
| Memories | **Memory**: what Kvist remembers about you and the project, with *Forget*. |
| Browser / computer use | The agent's own **screen** card (live preview, *Open in browser*), from the `browser` and `app_screen` skills. |
| ChatGPT sign-in | The `chatgpt` provider's sign-in and sign-out. |
| Slash commands, `@` files | `/new /resume /review /commit /commands /skills /memory /folder /settings`; `@` completes a project file; images attach by paste, drop or `+`. |
| Search, shortcuts | `Ctrl+K` searches threads and commands; `Ctrl+N`, `Ctrl+B`, `Ctrl+J`, `Ctrl+Shift+D`, `Ctrl+,`; `Esc` pauses the agent. |

Not covered: several tasks running at once (one task per app; other threads open when it has
ended), automations, queued follow-ups while a task runs (`Enter` never stops a task;
use *Pause* or *Stop*), inline comments on a diff.

The interface is English and Russian (*Settings → General*), light and dark.

## Layout

```
src/        main process: window, IPC, core operations (core-ops.js), git.js, workspace-files.js
renderer/   index.html, styles.css, js/
  thread.js   events (live or from the journal) -> the items a thread shows; pure, no DOM
  timeline.js draws them        composer.js  the message box     diff.js  unified diffs
  panels.js   review, commands drawer, commit dialog              pages.js skills, memory, settings
  sidebar.js  projects/threads  app.js       wiring
test/       node --test
```

## Tests and packaging

```bash
npm test
npm run dist     # builds the core with PyInstaller into resources/bin, then packages
```

`package.json` sets `"workspaces": []` so electron-builder treats this folder as its own root.
`resources/bin/` and `.build/` are build output.
