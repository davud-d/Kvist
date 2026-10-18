# Kvist Glass

A terminal interface drawn over a photograph: the conversation sits on frosted
glass cards, changed files and helpers on the left, a live preview of the
agent's browser on the right. Written in Rust (ratatui + crossterm); pictures
go through the kitty graphics protocol. It runs the Kvist core as a child
process speaking `--frontend stdio-multi` (`docs/UI_API.md` §8c).

**Status: prototype, connected to the core.** The launcher starts it as one
of the terminal interfaces: `kvist --frontend glass`, or pick it with `/tui`
(the Ink app, Classic Legacy, or Glass itself) and it opens on every launch.
On a terminal it cannot draw on, or without a build, the launcher says why and
opens the Ink UI instead.

## Which terminals

The interface is pictures first, so it needs the **kitty graphics protocol**
and the cell size in pixels:

| Terminal | Works |
|---|---|
| kitty 0.28 or newer | yes (checked on 0.49) |
| Ghostty | expected (kitty protocol), not checked yet |
| WezTerm, Konsole | not checked: their kitty-protocol support is partial |
| Alacritty, GNOME Terminal, Windows Terminal, xterm | no |
| anything inside tmux, GNU screen or Zellij | no: the multiplexer does not pass pictures through |

On any other terminal the program **does not start**: it checks the terminal
before taking over the screen, prints the reason and the alternatives, and
exits with status 3. `kvist-glass --check` runs only that check (status 0 means
it can start), so a launcher can decide before it runs anything.

## Run

The installer builds it into the `kvist` binary: tick "Rust toolchain" and "CLI"
in `python scripts/installer/install.py` (Linux and macOS), and release builds
carry it too. By hand:

```bash
cd frontends/tui/glass && cargo build --release   # the launcher finds target/release/kvist-glass
kvist --frontend glass                              # or /tui glass, saved for the next launches
```

The launcher looks for `KVIST_GLASS`, then `kvist-glass` on PATH, then this
checkout's build, and passes its own core in `KVIST_CORE_CMD`. Run directly,
`kvist-glass --workspace /path/to/project` starts the checkout's core.

- The core is `KVIST_CORE_CMD` (a JSON array of strings, as the launcher passes
  it to the Ink app), else this checkout's `.venv/bin/python kernel/__main__.py`.
  Its stderr goes to `kvist-glass-core-<pid>.log` in the temporary folder; the
  log is removed when the core exits cleanly and kept otherwise.
- The photograph stays alone on screen until the core says `hello` (about
  10–15 s, mostly the core fetching model lists). If the core cannot start,
  the reason and the log's path are shown there.
- Provider and model changes are saved by the core as everywhere else. To try
  them without touching your settings, point `KVIST_CONFIG_DIR` at a copy of
  `~/.kvist`.
- `KVIST_GLASS_FPS` sets the frame rate while something moves (default 60,
  from 20 to 240; anything else stops the start with a message). Set it to
  your monitor's refresh rate: kitty shows at most that (`sync_to_monitor`),
  and at most 100 with its default `repaint_delay 10`. Animations are timed in
  milliseconds, so the rate changes smoothness, never speed. While nothing
  moves the screen is redrawn every 90 ms, and input is drawn at once.
- Language follows `KVIST_LANG`, then `LC_ALL` / `LANG` (`ru` or English).

## Keys and mouse

Main actions are on the keyboard; panels, previews and pickers also open with
the mouse.

| Key | Mouse | Does |
|---|---|---|
| Enter | | send (Shift+Enter or Alt+Enter: new line) |
| `/name args` + Enter | | run a core command (`/provider`, `/models`, `/role`, `/effort`, `/new`…) |
| Ctrl+P | click the model in the status row | choose a model of any connected provider |
| | click the provider in the status row | choose the provider (`/provider`) |
| `/tui` + Enter | | choose the terminal interface; the launcher starts it now and on later launches |
| Esc, Esc | | stop the running turn (the first Esc says what the second does) |
| | click the browser preview | open the browser in the middle; Esc closes |
| Ctrl+T | click "Helpers" or a helper | open the helpers panel; Esc closes |
| PageUp / PageDown | wheel | scroll the conversation |
| Ctrl+C | | quit |

Ctrl+P lists every connected provider's models (the core's `catalog`: a
saved key or sign-in, its live model list) under the provider's name, the one
in use marked. Tab switches between choosing the smart and the fast model;
type to filter. A model of another provider switches the provider too, and the
other role keeps the model that provider already has (`models PROVIDER FAST
SMART`, one rebuild).

Every question the core asks (a choice, a text, a key, a confirmation) opens
the same glass dialog: type to filter a list, arrows and Enter to answer, Esc
to cancel. A command approval shows the command and the core's risk note;
its answers are Yes, Always (only when the core offers to remember the
pattern) and No, and No is preselected. A closed session replaces the input
with the core's notice and one button, "New session" (`/new`); Enter does the
same.

## How it is drawn

- `backdrop.rs` fits the photograph to the window at full resolution and
  prepares four blurred, darker versions of it on a worker thread.
- Every card is its own picture: a crop of the blurred photograph under it,
  tinted, with anti-aliased rounded corners and a thin rim of light. Cards
  slide by moving their placement, with pixel offsets, so motion is smoother
  than a cell.
- A picture is uploaded once (as a file in `/dev/shm` when the terminal
  accepts that, otherwise inline and compressed) and then only placed, moved
  and removed. Opening a dialog switches the backdrop between the
  pre-uploaded blur levels.
- The browser preview is the core's `screen` event: `screen.rs` fetches its
  `frame_url` (loopback only) about once a second.
- Motion uses "burst" timing (`anim.rs`): a slow start, a short fast middle
  and a long soft landing (for 600 ms: about 200, 50 and 350 ms). Opening
  the browser and the preview's arrival overshoot slightly instead.
- Text is ordinary cells over the pictures. Pictures sit below cells that
  have a background colour, so a highlighted row covers the glass.
- The terminal's own padding is painted with the photograph's edge colour
  (OSC 11) and given back on exit (OSC 111).

## From the core

- Lines added and removed per file come from `action_result`
  (`lines_added`, `lines_removed`): the executor's own line diff.
- The helpers panel shows each helper's calls as they run, with the end of
  their output (`helper_activity`); the left card shows what each helper is
  doing now.
- After `/session` the feed is refilled from the session's journal
  (`--session-op history`, the newest 500 events). Calls and helpers the journal
  left unfinished are shown as stopped, and a toast says when older events are not shown.

## Tests

```bash
cargo test
```
