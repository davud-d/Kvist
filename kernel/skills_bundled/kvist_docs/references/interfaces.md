# Interfaces and the protocol

All of them drive the same Python core. `/tui` (or `--frontend NAME`) chooses a terminal
look; the choice is saved. If what a look needs is missing (Node.js, a build, a TTY, Bun,
kitty graphics), Kvist falls back to another and says why.

| Interface | How | Notes |
| --- | --- | --- |
| Ink | default; `--frontend tui` | Node.js 22+ app, the release installer brings it |
| Claude-style | `--frontend claude` | full-screen skin of the same app; in-app scrolling and mouse selection |
| Classic | `--frontend glass` | Rust interface drawn over a photograph; needs kitty graphics (kitty, Ghostty), not tmux |
| Classic Legacy | `--frontend terminal` | the prompt_toolkit terminal; the fallback |
| OpenCode style | `--frontend opencode` | OpenCode's terminal interface on Kvist; needs Bun |
| Voice | `--frontend voice` | an orb you talk to; wake word "Kvist"; kitty or Ghostty |
| Electron app | `scripts/installer/install_gui.py` | desktop app |
| Browser | `kvist-web` | opens `http://127.0.0.1:9671`, guides provider setup |
| VS Code, JetBrains | plugins from the release | chat panel for the open project, commit messages in source control |
| Zed | `scripts/installer/install_zed.py` | registered as an external ACP agent (`kvist --acp`); no inline completion, no pause |

The status bar (Waybar, Polybar) gets a tray module showing whether a task runs; disable
with `tray_enabled: false` in the config.

## Your own client

`kvist --workspace DIR --frontend stdio` (or `stdio-multi`) speaks newline-delimited JSON:
first `hello`, then status, streamed text, `input_request` (approvals and dialogs), errors
and the final answer; replies are JSON on stdin. The full protocol is `docs/UI_API.md`.
Headless helpers: `--config-info`, `--session-op`, `--journal-op changes|revert`,
`--commit-message`, `--doctor`.
