# Kvist GUI

Electron frontend for Kvist's version 1 NDJSON protocol. The GUI does not import
Python modules or call Kvist internals. It starts one Kvist child process per
task and communicates only through stdin and stdout.

Run Kvist once in an interactive terminal to configure the provider, models,
and credentials before using the GUI. The protocol contract is documented in
`../UI_API.md`.

## Development

```bash
cd gui
npm ci
npm start -- --workspace /path/to/project
```

In development the app uses `../.venv/bin/python ../main.py`. Set
`KVIST_BINARY=/absolute/path/to/kvist` to test a specific frozen binary.

Click the role, model, or approval chips below the prompt to configure the next
run. Provider and model overrides travel as explicit stdio CLI arguments. Kvist
has separate fast and smart models, so either set both or keep both empty to use
the saved configuration. Approval mode supports `ask` and the
fail-closed `deny`; unsafe automatic approval is intentionally unavailable.

The sidebar contains only operations backed by Kvist's session lifecycle API.
Projects are workspaces previously opened by the GUI. Expanding a project shows
its saved sessions; selecting a session activates it through `--session-op
switch` and restores its persisted conversation. Recents combines sessions from
all remembered projects and sorts them by the session's `updated_at` value.

## Tests

```bash
npm test
```

## Package

Install the Python development dependencies first, then run:

```bash
npm run dist
```

The build runs PyInstaller and embeds the resulting Kvist binary into the
Electron application. Build separately on Linux and Windows for native
packages.

`package.json` sets `"workspaces": []` so electron-builder treats this
directory as its own workspace root. Without it, electron-builder walks up the
directory tree looking for a workspace root and lands on an unrelated monorepo
above this repository, then fails its Node module collection.

## Keyboard

- `Enter`: submit
- `Shift+Enter` or `Ctrl+Enter`: newline
- `Escape` twice: interrupt the active process
- `Ctrl+P`: command palette
- `Ctrl+M`: models, role, and permissions
- `Ctrl+N`: create a new named session in the active project

Approvals default to `ask`. Closing the window, selecting `deny`, or losing the
renderer denies dangerous-command confirmation before the child is stopped.
