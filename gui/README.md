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
has separate fast, smart, and planner models, so either set all three or keep all
three empty to use the saved configuration. Approval mode supports `ask` and the
fail-closed `deny`; unsafe automatic approval is intentionally unavailable.

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

## Keyboard

- `Enter`: submit
- `Shift+Enter` or `Ctrl+Enter`: newline
- `Escape` twice: interrupt the active process
- `Ctrl+P`: command palette
- `Ctrl+M`: models, role, and permissions
- `Ctrl+N`: clear the current task

Approvals default to `ask`. Closing the window, selecting `deny`, or losing the
renderer denies dangerous-command confirmation before the child is stopped.
