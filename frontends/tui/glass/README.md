# Kvist Classic

Users see this interface as **Classic**; the folder, the crate, `kvist-glass`, the
`glass` frontend value and `KVIST_GLASS` keep the working name Glass.

A terminal interface drawn over a looping forest video: the conversation sits on frosted
glass cards, changed files and helpers on the left, a live preview of the
agent's browser on the right. Written in Rust (ratatui + crossterm); pictures
go through the kitty graphics protocol. It runs the Kvist core as a child
process speaking `--frontend stdio-multi` (`docs/UI_API.md` §8c).

**Status: prototype, connected to the core.** The launcher starts it as one
of the terminal interfaces: `kvist --frontend glass`, or pick it with `/tui`
(the Ink app, Classic Legacy, or Classic itself) and it opens on every launch.
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
  moves the screen is redrawn at the video's 24 fps, and input is drawn at once.
- The bundled `assets/backdrop-night-forest.mp4` loops without sound, with a
  two-second crossfade joining the original tail to its beginning (about 8 s per loop). Playback
  needs ffmpeg: `KVIST_FFMPEG`, then PATH, then the release installer's
  `~/.local/share/kvist/ffmpeg/ffmpeg`. Without it, a still backdrop remains
  and a notice explains why. The clip is embedded in the Rust binary.
- Language follows `KVIST_LANG`, then `LC_ALL` / `LANG` (`ru` or English).

## Keys and mouse

Main actions are on the keyboard; panels, previews and pickers also open with
the mouse.

| Key | Mouse | Does |
|---|---|---|
| Enter | | send (Shift+Enter or Alt+Enter: new line) |
| ↑ / ↓ | | move the cursor one row, keeping its column; at the first or last row go to the start or end of the text, and then ↑ walks back through earlier inputs (the workspace's, kept by the core) and ↓ forward to the draft |
| Alt or Ctrl + ← / → | | move the cursor by word (Alt+b / Alt+f do the same) |
| Alt or Ctrl + Backspace / Delete | | delete the word before / after the cursor |
| `/` | | open the command list; typing narrows it to the names that start with the text |
| ↑ / ↓ in the list | hover | choose a command (wraps round) |
| Tab in the list | | complete the chosen name (`/style `) and type its arguments |
| Enter in the list | click a row | run the chosen command, not the typed prefix; `/rename` is completed instead, as it needs a name |
| `/name args` + Enter | | run a core command (`/provider`, `/models`, `/role`, `/effort`, `/new`…) |
| Ctrl+P | click the model in the status row | choose a model of any connected provider |
| | click the provider in the status row | choose the provider (`/provider`) |
| `/tui` + Enter | | choose the terminal interface; the launcher starts it now and on later launches |
| Esc, Esc | | stop the running turn (the first Esc says what the second does) |
| | click the browser preview | open the browser in the middle; Esc closes |
| | `O` in the opened browser | open the preview in your own browser |
| Ctrl+T | click "Helpers" or a helper | open the helpers panel; Esc closes |
| PageUp / PageDown | wheel | scroll the conversation |
| | press and drag over the conversation | pour water on the text: the selection flows after the pointer and is copied when the button is released |
| | double / triple click | pour over a word / a line and copy it |
| Esc | click elsewhere | let the water drain (while a pool is on the glass, Esc drains it first) |
| Ctrl+C | | quit |

Selecting text is pouring water on the glass. The left button lets a drop fall
where it is pressed; dragging pours, and the water runs along the text after
the pointer — it lags behind a fast one and catches up — then settles into a
pool with a rounded rim that catches the light. The pool is a translucent
picture drawn above the cell backgrounds and below the text (kitty's z-index),
so the words stay sharp, and the letters under it brighten. On release the text
goes to the clipboard through the terminal (OSC 52; icons and the quote bar are
left out) and "Copied" shows in the status row. Holding the drag above or below
the conversation scrolls it. Shift+drag still reaches the terminal's own
selection.

The agent's answers are drawn as Markdown (pulldown-cmark): headings, lists
with nesting, quotes, rules, links with their address, task lists, tables, and
fenced code with syntax colours (syntect with the grammars of two-face:
TypeScript, TOML, GLSL and the like). A single newline in the answer is a line
break. The text cursor is the terminal's own blinking bar in the moonlight
colour (OSC 12).

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
pattern) and No, and No is preselected. An approval to overwrite a file shows
the change as a wide dialog with the old file on the left and the new one on
the right: line numbers of both files, syntax colours, removed and added rows
tinted, and the part of a rewritten line that differs marked. The wheel or
Up and Down scroll it; Left and Right choose the answer. A closed session replaces the input
with the core's notice and one button, "New session" (`/new`); Enter does the
same.

Click a file in the "Changed" card to open what the agent did to it in a panel
on the right (windows of at least 124 columns): every edit of this session in
order, as numbered rows with syntax colours, removed and added lines tinted, and
the changed stretch of a rewritten line marked. The wheel over the panel
scrolls; clicking the file again, the cross or Esc closes it. The panel takes
the place of the browser and helpers cards while it is open. Diffs come from the
core with each write, so a file changed in a session saved before the core sent
them shows the counts only.

## How it is drawn

- `video.rs` decodes the looping clip to 960×540 RGB at 24 fps on a separate
  thread, retaining only the newest frame. The decoder stops on exit.
- `backdrop.rs` fits each frame to the window and prepares four blurred,
  darker versions on a worker thread. Textures are capped at 960×540, with
  smaller blur textures; kitty scales them to the window. Pending canvases are bounded to one.
- Every card is its own picture: a crop of the blurred frame under it,
  tinted, with anti-aliased rounded corners and a thin rim of light. Cards
  slide by moving their placement, with pixel offsets, so motion is smoother
  than a cell.
- Only the visible backdrop level and cards are uploaded for each frame
  (as files in `/dev/shm` when supported, otherwise inline and compressed).
  Previous images are freed after their replacements are placed.
- The browser preview is the core's `screen` event: `screen.rs` reads its
  `stream_url` (an MJPEG stream, loopback only; 30 frames a second, `KVIST_SCREEN_FPS`
  in the core changes it). The opened browser shows every frame; the small card
  remakes its picture at most every 120 ms, and the blurred steps of the opening
  are made only when it opens.
- Motion uses "burst" timing (`anim.rs`): a slow start, a short fast middle
  and a long soft landing (for 600 ms: about 200, 50 and 350 ms). Opening
  the browser and the preview's arrival overshoot slightly instead.
- Text is ordinary cells over the pictures. Pictures sit below cells that
  have a background colour, so a highlighted row covers the glass.
- The terminal's own padding is painted with the photograph's edge colour
  (OSC 11) and given back on exit (OSC 111).
- A display formula (`\[ … \]`, `$$ … $$`) is typeset by RaTeX (`math.rs`) and
  drawn as a picture over rows the transcript reserves for it, centred in the
  column and cropped with the text when scrolled. Inline formulas, and ones
  RaTeX cannot read, are shown as text (`√9 ≈ 3`). Math typesetting: RaTeX
  (MIT) with KaTeX fonts (SIL OFL 1.1); see [THIRD_PARTY.md](THIRD_PARTY.md).

## From the core

- Lines added and removed per file come from `action_result`
  (`lines_added`, `lines_removed`): the executor's own line diff. The same
  event's `patch` is the write's unified diff, and an overwrite approval's
  `subject.diff` is another; `diff.rs` reads both.
- The helpers panel shows each helper's calls as they run, with the end of
  their output (`helper_activity`); the left card shows what each helper is
  doing now.
- After `/session` the feed is refilled from the session's journal
  (`--session-op history`, the newest 500 events). Calls and helpers the journal
  left unfinished are shown as stopped, and a toast says when older events are not shown.

## Tests

```bash
cargo test
cargo test video_loops_and_cleans_up_on_drop -- --ignored  # requires ffmpeg, about 11 s
```
