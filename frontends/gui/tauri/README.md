# Kvist Pixel (Tauri)

A desktop interface for Kvist in pixel-game style. It is a frontend over the same core as every
other one (`docs/UI_API.md`), written in Rust (Tauri 2) with a plain-JavaScript page.

*(Русская версия: [README.ru.md](README.ru.md))*

## What it does

- **A project tree on the left.** Each folder opens into its sessions; picking one shows it (its agent starts when you use it), and a dot shows which are running.
- **Agents side by side.** Each agent is its own core process in its own session; the rail on the left
  lists them, the grid ("zoom out", `Ctrl+\`) shows all of them live and zooms back into the one you pick.
- **See what the helpers do and watch the agent's browser** in the right-hand panel (helpers, screen,
  background jobs, context and changed files). It opens by itself when a helper or a screen appears.
- **A closed conversation** shows a bar instead of the input box: press Enter for a new session.
- **Pictures**: paste, drop or `+`; they show as thumbnails above your message. **Formulas** render with KaTeX.
- **Settings** (gear, bottom left) do what the slash commands do (role, effort, style, models, sessions,
  changes, revert, ...), plus the look: five themes, size, animations on or off, window transparency.
- Plan offers, command approvals and overwrite diffs appear as dialogs, as in the other interfaces.
- **best-of and council** (`/best-of N <task>`, `/council <question>`), the same screens as in Classic, from the same core events
  (`docs/UI_API.md` §7). Both commands are whole turns: your line stands in the feed, Stop works, and the input is free again
  when the result comes (also when the command fails before its turn begins).
  - **The arena** takes the conversation's place while a run goes on: a card per executor with its style, state, step, tests,
    size of changes (`+added −removed · files`), money or tokens, its branch and latest call. An executor that is out is
    muted, with its reason. The cross (or Esc inside the screen) gives the conversation back.
  - **The executor's log**: a click on a card reads that executor's journal (`--session-op history`, the newest 500 events)
    and draws it with the conversation's own item views, read only; "Back" returns to the arena. It is replayed into a state
    of its own, never into the conversation.
  - **The decision screen** is the core's question with `purpose: "bestof_decision"` for the run on screen: the verdict table,
    the winner and why, and the buttons the core offered: Accept (`accept`), See the diff (`diff:<id>` of the chosen row),
    Pick another (`pick:<id>`; on the winner's row `other`), Discard all (`discard`). Closing it answers `null`. After a diff
    the core asks again and the patch is shown. With no verdict, or another run's, it is the ordinary list.
  - **The council**: four cards (simplicity, security, development speed, maintenance cost) filled as members answer, their
    risks under each card, and after the verdict the chosen card is lit with the reason and the dissent.
  - **The layer's bar** (only while the arena or council is up; an ordinary conversation, header included, shows nothing new): how calls travel (`tool_mode`: native or text tools) and the money: the sum of `cost_usd` of the main
    stream and of the executors' latest snapshots, "≈" when a price is approximate or some call has none; with no price at
    all, "total N tok", plus "· M uncached" only when the calls said what the cache served ("≈M" when not all did).
  - The logic is in `ui/js/bestof.js` (state, money, decision answers) and `bestof-log.js`; the views are
    `ui/js/views/{arena,execlog,decision,council,bestof}.js`, the words (EN/RU) `bestof-words.js`, tests `tests/bestof.test.mjs`.
    The stand-in core (`mock-core.js`) plays both commands, so the screens can be tried in a plain browser.

## Window transparency

Whether the window is see-through is decided when it is created. macOS and Windows start transparent. **Linux starts opaque**:
WebKitGTK paints nothing in a transparent window on some graphics stacks (measured: transparent with the DMABUF renderer off
is blank, and NVIDIA needs it off), so transparency is opt in there. Turn it on in the settings ("window transparency", the app
restarts) or start with `--transparent`; `--opaque` turns it off again. If the window is ever empty, start with `--safe`
(`KVIST_SAFE_RENDER=1`: opaque and the software web view). The menu entry has a "Safe mode" action for that. The choice is
saved in `~/.kvist/desktop.json` (or `$KVIST_CONFIG_DIR`). Wayland and X11 need nothing else; a transparent window needs a
compositor, and blur behind it is the compositor's, not the app's.

## Run from a checkout

```bash
cd frontends/gui/tauri/src-tauri
cargo run                      # needs the repository's .venv (the core), or KVIST_CORE_CMD
cargo build --release --locked # target/release/kvist-tauri
```

Linux needs `libwebkit2gtk-4.1-dev`, `libgtk-3-dev`, `librsvg2-dev`. Tests: `node --test "tests/*.test.mjs"` and
`cargo test`. To look at the page without Tauri, serve `ui/` with any static server: a scripted stand-in core answers
(try the words `approve`, `plan` and `insult` in a message).

## Install

The release installers (`curl ... | bash`, `install.ps1`) offer **Kvist Pixel desktop app**, ticked by default: they
download the binary and add it to the application menu (Linux launcher entry, `~/Applications/Kvist Pixel.app`,
Start menu and Desktop shortcut). From a checkout, `python scripts/installer/install.py` (or `install_app.py`) *compiles* it with cargo
after Rust is installed and adds it to the same menu; it never downloads.

## Layout

`src-tauri/` the shell (`core.rs` core processes, `ops.rs` session operations, `main.rs` commands) ·
`ui/js/` reducer, store, actions, motion, views, panels · `ui/css/` theme and layout · `tests/` page logic.
Fonts and KaTeX are bundled; see `THIRD_PARTY.md`.
