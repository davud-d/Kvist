# Kvist voice screen

A terminal screen for talking to Kvist: an orb in the middle of a photograph shows what it is doing, a frosted-glass card under it holds what was heard and said, and a menu changes the model, provider, session and more. Written in Rust (ratatui and crossterm); pictures go through the kitty graphics protocol, so it runs in kitty and Ghostty only (`kvist-voice-screen --check` says whether a terminal can).

[Русская версия](README.ru.md)

It is a **separate program** and shares no code with the conversation screen (`frontends/tui/glass`): a change in one cannot break the other. Thirteen files here (`anim`, `backdrop`, `core`, `dialog`, `diff`, `highlight`, `kitty`, `layout`, `probe`, `strings`, `term`, `text`, `theme`) began as copies of that screen's files and are maintained in this crate from now on; a fix made in one place has to be made in the other by hand. The screen's own files are `orb.rs`, `voice.rs`, `voice_overlay.rs`, `voice_strings.rs` and `icons.rs`.

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
| `--demo` | no process at all: every orb state in turn (space or the arrows change it by hand) |
| `--check` | only check the terminal: status 0 if the screen can start, 3 with the reason if not |
| `--orb-preview FILE` | write a PNG with the orb in every state |

`KVIST_VOICE_FPS` sets the frames per second (default 60, the orb is drawn at most 30 times a second).

## What it shows

The orb is a small planet of clouds drawn per pixel every frame, after `assets/orb-reference-dusk-valley.png`: deep blue sky, white clouds lit warm from below, a thin bright rim. A state is a set of numbers (the sky's colours, how fast the clouds turn, rings, a running arc, orbiting lights) and a change of state eases from one set to the next. Eleven states: loading, waiting for the wake word, listening, writing the words down, thinking, working, thinking aloud, speaking, waiting for yes or no, error and no connection. The colours shimmer in every state and keep their meaning (amber stays amber, red stays red).

Under the orb a frosted-glass card (the photograph blurred under it, as on the conversation screen) holds the state with its icon, the last words heard or said, a dim line with what the room sounded like when it was not for Kvist (so a misheard wake word can be seen), the result of the last command and a problem if there is one. The footer has the core's provider and model, the session, a context meter and the keys.

## Menu, dialogs, sessions

Typing anywhere on the plain screen opens an input line: Enter sends a request, and a line that starts with `/` runs a core command with its words (`/export`, `/rename name`, `/tui`). `Tab` opens a menu: a new session, sessions, the model, the provider, the API key, web search, thinking effort, role, style, API settings, file changes, revert and the interface (the same picker as `/tui`: Classic, Ink, Claude-style, Voice, Classic Legacy; the launcher starts the one chosen). Each is a command the core already has; the core's own dialogs do the asking, shown as a list with a search field, a text or a secret field (the key is masked and may be pasted) or a yes/no question, and the result appears on the card. The menu shows the current session, model and provider beside its items, and the letter on the left picks an item. `Esc` closes a dialog, the menu or the input line first; on the plain screen it switches the microphone off and on (typing still works). `Ctrl+C` asks once and quits on the second press within three seconds. On exit the screen prints the Kvist mark and the session to resume, like the other terminal interfaces.

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
```

Both helpers are optional. Without ears the screen only shows the core; without a voice, answers are shown and the orb "speaks" for as long as the words would take.

## Tests

```sh
cargo test --release
```

The pure state (`Voice`) is tested without a process or a terminal: every priority of the orb's state, the dialogs, the menu, the commands and the conversation. The tests of the conversation screen's files compiled here run as well. To look at it without a microphone or tokens, run it on a private `Xvfb` with kitty and stand-in scripts for the core, the ears and the voice.
