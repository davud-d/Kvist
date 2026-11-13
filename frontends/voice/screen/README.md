# Kvist voice screen

A terminal screen for talking to Kvist: an orb in the middle of a photograph shows what it is doing, a frosted-glass card under it holds what was heard and said, and a menu changes the model, provider, session and more. Written in Rust (ratatui and crossterm); pictures go through the kitty graphics protocol, so it runs in kitty and Ghostty only (`kvist-voice-screen --check` says whether a terminal can).

[Русская версия](README.ru.md)

It is a **separate program** and shares no code with the conversation screen (`frontends/tui/glass`): a change in one cannot break the other. Thirteen files here (`anim`, `backdrop`, `core`, `dialog`, `diff`, `highlight`, `kitty`, `layout`, `probe`, `strings`, `term`, `text`, `theme`) began as copies of that screen's files and are maintained in this crate from now on; a fix made in one place has to be made in the other by hand. The screen's own files are `orb.rs`, `modules.rs`, `voice.rs`, `voice_overlay.rs`, `voice_strings.rs` and `icons.rs`.

## Run

```sh
cd frontends/voice/screen && cargo build --release      # once
frontends/voice/kvist-voice --workspace /path/to/project
```

`frontends/voice/kvist-voice` starts the screen with the ears and the voice; the setup of those two and every environment variable are in [`../README.md`](../README.md). Run directly, `kvist-voice-screen` starts the core only (`KVIST_CORE_CMD`, else this checkout's `.venv/bin/python kernel/__main__.py`) with `AGENT_VOICE_MODE=1`, and the ears and the voice when `KVIST_VOICE_STT_CMD` and `KVIST_VOICE_TTS_CMD` name them (JSON arrays with a command that speaks JSON lines).

| Option | Does |
|---|---|
| `--workspace DIR` | the folder the agent works in (default: here) |
| `--continue` | stay in the last session; a launch otherwise opens a new one |
| `--demo` | no process at all: every orb state in turn (space or the arrows change it by hand); the listening state plays a voice and a pause, the working state plays three modules |
| `--check` | only check the terminal: status 0 if the screen can start, 3 with the reason if not |
| `--orb-preview FILE` | write a PNG with the orb in every state, over the photograph |

`KVIST_VOICE_FPS` sets the frames per second (default 60, the orb is drawn at most 30 times a second).

## What it shows

The orb is a ball of glass hanging in the photograph, with a light inside. The glass bends the photograph behind it the way a real glass ball does (upside down, squeezed towards the rim), so the orb belongs to the scene and is as sharp as the photograph. It is drawn per pixel at exactly the size it is shown at (the picture has the pixels of its cells, so the terminal scales nothing and the ball is a true circle); only a window taller than about 1600 pixels has it scaled up, and by little. What Kvist is doing is the light: its colour, how it moves, and what leaves the ball. A state is a set of numbers and a change of state eases from one set to the next. Eleven states: loading (a pale spark, a running arc), waiting for the wake word (a warm spark breathing slowly), listening (cyan), writing the words down (teal, a quick arc), thinking (violet ribbons turning inside), working (amber), thinking aloud (lilac), speaking (warm white, rings leaving), waiting for yes or no (gold, a slow pulse), error (red, a shiver) and no connection (grey glass, no light).

**The person can see that they are heard.** While somebody speaks, a ring of light inside the ball moves with their voice (also while the orb only waits for the wake word, so a dead microphone shows at once). While Kvist listens, a thin ring outside the ball is whole as long as the person speaks and empties clockwise while they are silent: when nothing is left of it, the phrase is over. So they know how long they may stop to think, and a long request is not cut by surprise. Between the phrases of one request the orb keeps listening (see the ears' notes in [`../README.md`](../README.md)); words the recognition lost are admitted on the card ("say it again").

**Modules.** Each tool Kvist uses comes out from behind the ball as a small glass card and stays beside it while it works: a browser, a web search, a terminal, files, an edit, git, a helper, memory, a skill by its own name. A card has the tool's icon and name, what the call does (the core's `action_description`), a spinner while it runs and a check or a cross with the core's one-line summary when it ends. A twig grows from the ball to the card, with lights running along it while the tool works; it turns green or red with the outcome. A finished card stays for a few seconds and folds back in. A card does not just slide: it comes out of the ball as a thin thread of light in its tool's colour, widens as it travels, and when it is in its place opens up and down into glass like a leaf unfolding; the words appear last. Closing is the same in reverse (the words go, the card folds into a line, the line draws into the ball). Both ways go by one curve, slow start, fast middle, slow end (`Ease::Fold` in `anim.rs`; `FOLD_ENDS` there sets how long each end creeps and how much of the way it covers, `OPEN_MS` and `CLOSE_MS` in `modules.rs` set the times). Calls of one model response that do the same kind of thing share a card, which counts them (`2/12`). There are up to six places round the ball (right and left of it, then above and below those); with all of them taken a new module waits for one, and a window with no room beside the ball shows none. **Writes and commands have tall cards too.** A file being written gets a card to the left of the ball (the thoughts and the terminal take the right): the file's path, `+N −M`, and the diff as the conversation screen draws it, with line numbers, syntax colours and tinted added and removed rows; it is the diff of the call's arguments at first (all of `content` added, or the old fragment struck out) and the core's own `action_result.patch` once the write has happened, and a long one scrolls through within about two seconds. A command gets a terminal: `$ command` pinned at the top, and under it what it printed, line by line as a terminal prints it, the newest at the bottom, with `exit code N` for a failure. Several writes or commands of one response share a card and it shows the newest. A card grows with its words (up to seven lines), and a window with no room beside the ball gives these calls the ordinary two-line card. **Thoughts.** When the provider lets the model's thinking be seen (the core's `reasoning_*` events carry words), a tall card of its own opens beside the ball (on the right, level with its middle; the places it covers are kept free of tool cards) and shows the thought as a window of six lines, like the thinking block of a chat app: the newest words at the bottom, the oldest fading out at the top, a thin rule down the left. The name shimmers while the model thinks. When it has finished thinking the card shows a check and closes after a moment, and the next thought opens a new one. A provider that sends only the time it thought for (and no words) gets no card. A menu or a dialog over a card hides it. `modules.rs` holds this without a terminal.

Cost: the glass, its reflections, the shadow and the shape of the glow are the same in every frame, so they are worked out once for a size of window and a frame draws only the light (about 10 ms of processor time for a 690-pixel picture, 13 ms with the voice's rings); a waiting orb is drawn ten times a second instead of thirty. A whole `--demo` cycle takes about as much processor time as the earlier 360-pixel orb did, with 3.6 times the pixels.

Under the orb a frosted-glass card (the photograph blurred under it, as on the conversation screen) holds the state with its icon, the last words heard or said, a dim line with what the room sounded like when it was not for Kvist (so a misheard wake word can be seen), the result of the last command and a problem if there is one. The footer has the core's provider and model, the session and the keys; the context meter sits right-aligned on the row above the keys.

## Menu, dialogs, sessions

Typing anywhere on the plain screen opens an input line: Enter sends a request, and a line that starts with `/` runs a core command with its words (`/export`, `/rename name`, `/tui`). A bare `/` lists the commands over the input line, as the conversation screen does: typing narrows it, the arrows walk it, `Tab` completes the name and Enter runs the chosen command; the glass frosts in as the list opens. The window title is `Kvist` until the core has generated the session's title, then `Kvist | <title>`. `Tab` opens a menu: a new session, sessions, the model, the provider, the API key, web search, thinking effort, role, style, API settings, file changes, revert and the interface (the same picker as `/tui`: Classic, Ink, Claude-style, Voice, Classic Legacy; the launcher starts the one chosen). Each is a command the core already has; the core's own dialogs do the asking, shown as a list with a search field, a text or a secret field (the key is masked and may be pasted) or a yes/no question, and the result appears on the card. The menu shows the current session, model and provider beside its items, and the letter on the left picks an item. `Esc` closes a dialog, the menu or the input line first; on the plain screen it switches the microphone off and on (typing still works). `Ctrl+C` asks once and quits on the second press within three seconds. On exit the screen prints the Kvist mark and the session to resume, like the other terminal interfaces.

A launch opens its own session, as the other interfaces do (the core would otherwise continue the last one and answer from its context); `--continue` keeps the last one, and the menu reopens any.

A question the agent asks is read aloud and answered by saying yes or no, or with Y and N. Speech is never taken for a task while a dialog or the menu is open. While the voice talks the ears are muted, and the person's own voice cuts the voice off. When the assistant has finished speaking, the screen tells the ears the person may talk on without the wake word for a while (see the ears' notes in [`../README.md`](../README.md)). Only the assistant's replies are spoken: a runtime note or a skill's message is neither spoken nor shown.

## Protocol with the helpers

```
core (docs/UI_API.md)      --frontend stdio-multi --wake, AGENT_VOICE_MODE=1
voice, to it:              {"type":"speak","id":N,"kind":"answer|narration","text":…}  {"type":"stop"}
voice, from it:            {"event":"speaking"} {"event":"idle"} {"event":"level","value":0..1}
ears, to them:             {"type":"mute","value":bool}  {"type":"converse","seconds":n,"min_words":n}
ears, from them:           {"event":"state","state":"loading|idle|listening|transcribing"}
                           {"event":"text","text":…}  {"event":"heard","text":…}
                           {"event":"phrase","on":bool}  {"event":"level","value":0..1,"pause":0..1}
                           {"event":"missed"}
```

Both helpers are optional. Without ears the screen only shows the core; without a voice, answers are shown and the orb "speaks" for as long as the words would take.

## Tests

```sh
cargo test --release
```

The pure state (`Voice`) is tested without a process or a terminal: every priority of the orb's state, the dialogs, the menu, the commands and the conversation. The tests of the conversation screen's files compiled here run as well. To look at it without a microphone or tokens, run it on a private `Xvfb` with kitty and stand-in scripts for the core, the ears and the voice.
