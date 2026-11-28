// The voice screen (`kvist-voice-screen`): the orb in the middle of a
// photograph, a line saying what it is doing, what was heard or said, and the
// model and context below. Three child processes feed it:
//
//   the core    `--frontend stdio-multi --wake` with AGENT_VOICE_MODE=1 (docs/UI_API.md)
//   the ears    KVIST_VOICE_STT_CMD: frontends/voice, JSON lines on stdout
//   the voice   KVIST_VOICE_TTS_CMD: frontends/voice/speak.py, JSON lines both ways
//
// A fresh session starts with the screen (`--continue` keeps the last one). `Tab`
// opens a menu of what can be changed (a new session, another session, the
// model, the provider, the API key, web search, effort, role, style): each is a
// command the core already has, and the core's own dialogs do the asking, shown
// here as a list, a text or a secret field, or a yes/no question.
//
// Both sidecars are optional. Without ears the screen only shows the core;
// without a voice, answers are shown and the orb "speaks" for as long as the
// words would take. `--demo` runs no process and walks through every state.
//
// Messages to the voice (stdin):  {"type":"speak","id":N,"kind":"answer|narration","text":…}
//                                 {"type":"stop"}
// Messages from the voice:        {"event":"speaking"} {"event":"idle"} {"event":"level","value":0..1}
// Messages to the ears (stdin):   {"type":"mute","value":bool}  (its own voice must not wake it)
//                                 {"type":"converse","seconds":n,"min_words":n}  (talk on without the wake word)
// Messages from the ears:         {"event":"state","state":"loading|idle|listening|transcribing"}
//                                 {"event":"text","text":…}  {"event":"heard","text":…}
//                                 {"event":"phrase","on":bool}  {"event":"level","value":0..1,"pause":0..1}
//                                 {"event":"missed"}
//
// The orb (orb.rs) is a ball of glass with a light inside; the tools Kvist uses come out of
// it as glass cards (modules.rs) and go back in when they are done.

use std::cell::RefCell;
use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use std::path::Path;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;
use std::time::{Duration, Instant};

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers};
use image::RgbImage;
use image::imageops::FilterType;
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::Rect;
use ratatui::style::{Modifier, Style};
use serde_json::{Value, json};

use crate::anim::{self, Ease, Tween};
use crate::backdrop::{self, Glass};
use crate::core::{self, Core, Message};
use crate::dialog::{Ask, Body, Choice};
use crate::diff::{self, Patch};
use crate::kitty::{Gpu, Placement, Z_BACKDROP, Z_CARD, Z_DIALOG};
use crate::math;
use crate::layout::Grid;
use crate::modules::{self, Kind as ModuleKind, Module, Modules, Preview, Shell, Status};
use crate::orb::{ALL_STATES, Orb, OrbState, SCENERY_REACH, Scenery, Twig};
use crate::probe::Caps;
use crate::slash;
use crate::voice_strings::{Strings, strings};
use crate::text::{self, Painter};
use crate::icons;
use crate::theme;
use crate::voice_overlay::{self, LIST_ROWS, MENU};

const BACKDROP: &[u8] = include_bytes!("../assets/backdrop-voice-dusk-valley.png");
const BACKDROP_SLOT: u32 = 1;
const ORB_SLOT: u32 = 2;
const CARD_SLOT: u32 = 3;
const OVERLAY_SLOT: u32 = 4;
const MENU_SLOT: u32 = 5;
/// The modules' glass: one slot for each place round the ball.
/// How much of the end of a thought is kept for its card.
const THOUGHT_KEPT_CHARS: usize = 600;
const MODULE_SLOT: u32 = 10;
/// Under the orb's picture, so a module comes out from behind the ball.
const Z_MODULE: i32 = Z_CARD - 1000;
const Z_ORB: i32 = Z_CARD + 1000;
/// Cells between a module's edge and its text.
const MODULE_PAD_X: u16 = 2;
/// The most pixels along a side of the photograph's patch that the ball's glass bends: enough
/// for the middle of a large ball, where the glass shows the photograph about life-size.
const SCENERY_SIDE: u32 = 768;
/// The most commands the slash list shows at once; the rest scroll.
const SLASH_ROWS: usize = 8;
/// After an answer is spoken, the person may talk on for this long without the wake word,
/// with at least this many words: a stray word is not a reply.
const CONVERSE_SECONDS: f32 = 12.0;
const CONVERSE_MIN_WORDS: u32 = 2;
/// After a question the agent asked, a single word ("да") answers it.
const QUESTION_SECONDS: f32 = 20.0;
/// How long the result of a command stays on the card.
const TOAST_SECONDS: u64 = 5;
/// Request id of the interface picker, which this screen asks itself and never sends to the core.
const FRONTEND_REQUEST: &str = "local:frontend";
/// Exit status that asks the launcher to start the interface named in `KVIST_SWITCH_FILE`.
const SWITCH_FRONTEND: u8 = 64;
/// A second Ctrl+C within this long quits.
const QUIT_CONFIRM: Duration = Duration::from_secs(3);
/// The orb is drawn at most this often: each frame is a new picture for the terminal.
const ORB_FPS: u32 = 30;
/// How often an orb that only waits is drawn anew.
const CALM_ORB_PERIOD: Duration = Duration::from_millis(100);
/// The tallest the orb's picture is drawn. Below this it has exactly the pixels of its
/// cells, so the terminal scales nothing; only a window taller than about 1600 pixels
/// has it scaled up, and by little.
const ORB_TEXTURE_MAX: u32 = 1024;
/// The photograph is sent at most this wide; the terminal scales it to the window.
const BACKDROP_MAX_WIDTH: u32 = 1600;
/// How long a phrase that was not for Kvist stays on the card.
const HEARD_SECONDS: u64 = 6;
/// How long the orb stays in the error state.
const ERROR_SECONDS: u64 = 3;
/// How long a state is shown in `--demo` before it moves on by itself.
const DEMO_SECONDS: u64 = 4;
/// The working state plays a longer story: the thinking, then the tools.
const DEMO_WORK_SECONDS: u64 = 10;

/// How long `--demo` stays in a state before it goes on to the next.
fn demo_seconds(state: OrbState) -> u64 {
    if state == OrbState::Working { DEMO_WORK_SECONDS } else { DEMO_SECONDS }
}
/// Steps that end a turn rather than work (their words arrive with `final`).
const ENDINGS: [&str; 4] = ["final_answer", "report_to_user", "refuse", "end_session"];

// ───────────────────────────── state, without any process ─────────────────────────────

#[derive(Clone, Copy, Debug, PartialEq)]
enum Ears {
    /// No recognition process was started.
    Absent,
    Loading,
    Idle,
    Listening,
    Transcribing,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Turn {
    Idle,
    Thinking,
    Working,
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Kind {
    Answer,
    Narration,
}

impl Kind {
    fn wire(self) -> &'static str {
        match self {
            Kind::Answer => "answer",
            Kind::Narration => "narration",
        }
    }
}

/// A conversation the ears are to open once the assistant has finished speaking.
#[derive(Clone, Copy, Debug, PartialEq)]
struct Converse {
    seconds: f32,
    min_words: u32,
    /// The voice has begun (or there is none): only now can its end be told.
    voice_started: bool,
    /// It is the answer to a question the agent asked, which may come in the middle of a turn.
    mid_turn: bool,
}

/// A command sent to the core that has not answered yet; its dialogs belong to it.
#[derive(Clone, Debug, PartialEq)]
struct Pending {
    /// A start-up command nobody asked for: its result is not shown.
    silent: bool,
}

/// What the screen asks its processes to do; the run loop delivers them.
#[derive(Clone, Debug, PartialEq)]
enum Out {
    Core(Value),
    Voice(Value),
    Ears(Value),
}

struct Demo {
    index: usize,
    since: Instant,
    /// How many steps of the state's little play have been shown.
    played: usize,
}

/// What `--demo` plays while it shows the working state, so the modules can be seen without
/// a core: (milliseconds into the state, the event the core would send).
fn demo_work() -> Vec<(u64, Value)> {
    let skill = |call: &str, label: &str| json!({"type": "step", "batch_id": call, "call_id": call, "action_type": "skill_call", "action_args": {"skill": "browser"}, "action_description": label});
    let done = |call: &str, summary: &str| json!({"type": "action_result", "call_id": call, "ok": true, "summary": summary});
    // The model's thinking arrives in pieces to append, as the core sends it.
    let piece = |text: &str| json!({"type": "reasoning_chunk", "stream_id": "demo", "text": text, "append": true});
    vec![
        (0, json!({"type": "reasoning_start", "stream_id": "demo"})),
        (100, piece("Нужно открыть YouTube и найти там документальный фильм про ледники.")),
        (700, piece(" Сначала откроем сайт, потом поищем по названию,")),
        (1100, piece(" и поставим на паузу то, что сейчас играет в браузере.\n")),
        (1500, piece("Лучше выбрать то, которое подлиннее: короткие ролики обычно не про устройство ледников, а про красивые кадры.\n")),
        (2000, piece("- проверить длительность перед запуском\n- не пересказывать описание под видео")),
        (2500, piece("\nИ последнее: сказать пользователю, как оно называется и сколько идёт.")),
        (3300, json!({"type": "reasoning_commit", "stream_id": "demo", "duration_seconds": 3.2})),
        (3500, json!({"type": "step", "batch_id": "e", "call_id": "d1", "action_type": "write_file", "action_description": "games/rules.py",
            "action_args": {"path": "games/rules.py", "content": "from dataclasses import dataclass\n\n\n@dataclass\nclass Move:\n    row: int\n    col: int\n\n\ndef legal(board, move):\n    if board[move.row][move.col] != 0:\n        return False\n    return True\n"}})),
        (4000, json!({"type": "step", "batch_id": "s", "call_id": "d2", "action_type": "run_shell", "action_description": "прогнать тесты правил игры",
            "action_args": {"command": ["python", "-m", "pytest", "-q", "tests/test_rules.py"]}})),
        (4400, skill("d3", "youtube.com")),
        (5000, json!({"type": "action_result", "call_id": "d1", "ok": true, "summary": "Wrote 13 lines", "path": "games/rules.py", "lines_added": 13, "lines_removed": 0,
            "patch": "--- /dev/null\n+++ b/games/rules.py\n@@ -0,0 +1,13 @@\n+from dataclasses import dataclass\n+\n+\n+@dataclass\n+class Move:\n+    row: int\n+    col: int\n+\n+\n+def legal(board, move):\n+    if board[move.row][move.col] != 0:\n+        return False\n+    return True\n"})),
        (5800, json!({"type": "action_result", "call_id": "d2", "ok": true, "summary": "Command exited with code 0", "exit_code": 0,
            "output": "============ test session starts ============\ncollected 9 items\n\ntests/test_rules.py .........        [100%]\n\n============ 9 passed in 0.31s ============"})),
        (6400, done("d3", "browser.open: done in 2.9 s")),
    ]
}

/// The lines of a tall card, made for a content (its generation) and a width: how many of them are
/// the command at the head (the terminal's), and all of them.
struct TallLines {
    generation: u64,
    width: usize,
    head: usize,
    lines: Vec<text::Line>,
}

/// A tall card's room in the window, and its height in rows, which eases towards what its words need.
struct Tall {
    /// Where it stands; `None` without room.
    rect: Option<Rect>,
    /// The places it covers (a tool does not take them while it is up).
    covered: Option<Vec<usize>>,
    rows: f32,
    open: bool,
}

impl Default for Tall {
    fn default() -> Self {
        Tall { rect: None, covered: Some(Vec::new()), rows: 4.0, open: false }
    }
}

pub struct Voice {
    started: Instant,
    orb: Orb,
    demo: Option<Demo>,
    ears: Ears,
    has_voice: bool,
    core_ready: bool,
    core_alive: bool,
    turn: Turn,
    /// A dialog of the core on screen: a question of the agent's, or a step of a command.
    ask: Option<Ask>,
    /// The menu is open, on this item.
    menu: Option<usize>,
    /// The line being typed: a request, or a `/command` with its words.
    input: Option<String>,
    /// The first Ctrl+C of a quit, waiting for its second.
    quit_armed: Option<Instant>,
    /// The person switched the microphone off with Esc.
    mic_off: bool,
    /// What the ears were last told about muting.
    mute_sent: bool,
    pending: Option<Pending>,
    converse: Option<Converse>,
    /// The result of the last command, and whether it worked.
    toast: Option<(String, bool, Instant)>,
    session: String,
    /// The core's name of the open session, to tell whose title an event brings.
    session_id: String,
    /// The open session's generated title; empty until the core has one.
    title: String,
    /// The slash list's chosen row.
    pick: usize,
    /// 0 while the slash list is closed, 1 once it has opened.
    menu_in: Tween,
    /// Start a new session when the core says hello (not with `--continue`).
    fresh_session: bool,
    error_until: Option<Instant>,
    /// What the voice is saying now: by its own report, or, without a voice, a timer.
    voice_busy: bool,
    voice_kind: Kind,
    pretend_until: Option<Instant>,
    level: f32,
    caption: String,
    caption_from_person: bool,
    stream_text: String,
    last_spoken: String,
    speak_id: u64,
    provider: String,
    model: String,
    context: Option<f32>,
    notice: String,
    /// A core on a server that another frontend drives: what is said instead of listening. Enter takes over.
    observer: Option<String>,
    /// The last phrase the ears heard that was not for Kvist, and when.
    overheard: Option<(String, Instant)>,
    /// What Kvist is doing: the cards that come out of the ball.
    modules: Modules,
    /// How many times the model has begun to think: its cards are told apart by it.
    thoughts: u32,
    /// The thought as it has come so far (the core sends it in pieces).
    thought_text: String,
    /// The tall cards: the thoughts, the writes, the terminal.
    tall: [Tall; modules::TALL_COUNT],
    /// The lines of a write or a command on its card, made once for a content and a width.
    tall_lines: [RefCell<Option<TallLines>>; modules::TALL_COUNT],
    /// Counts the diffs and commands the cards have been given, so that the lines above are known to be stale.
    edit_generation: u64,
    /// How many places for modules the window has (set by the run loop from its size).
    places: usize,
    /// Somebody in the room is speaking now; how loud; and how much of the pause that ends their phrase has passed.
    hearing: bool,
    mic_level: f32,
    pause: f32,
    outbox: Vec<Out>,
    pub quit: bool,
    /// The status the program exits with.
    exit_code: u8,
}

impl Voice {
    fn new(demo: bool, now: Instant) -> Self {
        Voice {
            started: now,
            orb: Orb::new(if demo { ALL_STATES[0] } else { OrbState::Loading }),
            demo: demo.then_some(Demo { index: 0, since: now, played: 0 }),
            ears: Ears::Absent,
            has_voice: false,
            core_ready: demo,
            core_alive: true,
            turn: Turn::Idle,
            ask: None,
            menu: None,
            input: None,
            quit_armed: None,
            mic_off: false,
            mute_sent: false,
            pending: None,
            converse: None,
            toast: None,
            session: String::new(),
            session_id: String::new(),
            title: String::new(),
            pick: 0,
            menu_in: Tween::settled(0.0),
            fresh_session: true,
            error_until: None,
            voice_busy: false,
            voice_kind: Kind::Answer,
            pretend_until: None,
            level: 0.0,
            caption: String::new(),
            caption_from_person: false,
            stream_text: String::new(),
            last_spoken: String::new(),
            speak_id: 0,
            provider: if demo { "provider".into() } else { String::new() },
            model: if demo { "model".into() } else { String::new() },
            context: demo.then_some(0.42),
            notice: String::new(),
            observer: None,
            overheard: None,
            modules: Modules::default(),
            thoughts: 0,
            thought_text: String::new(),
            tall: std::array::from_fn(|_| Tall::default()),
            tall_lines: std::array::from_fn(|_| RefCell::new(None)),
            edit_generation: 0,
            places: 6,
            hearing: false,
            mic_level: 0.0,
            pause: 0.0,
            outbox: Vec::new(),
            quit: false,
            exit_code: 0,
        }
    }

    /// The state the orb should show now. A failure is shown first; the person's
    /// own voice comes next, then a question waiting for an answer; the
    /// assistant's speech and work follow, and waiting comes last.
    fn resolve(&self, now: Instant) -> OrbState {
        if let Some(demo) = &self.demo {
            return ALL_STATES[demo.index];
        }
        if !self.core_alive {
            return OrbState::Offline;
        }
        if self.error_until.is_some_and(|until| now < until) {
            return OrbState::Error;
        }
        match self.ears {
            Ears::Listening => return OrbState::Listening,
            // Between the phrases of one request the ears write down the last one while the person says the next.
            Ears::Transcribing if self.hearing => return OrbState::Listening,
            Ears::Transcribing => return OrbState::Transcribing,
            _ => {}
        }
        if self.asks_the_person() {
            return OrbState::Approval;
        }
        if self.speaking(now) {
            return if self.voice_kind == Kind::Narration { OrbState::Narrating } else { OrbState::Speaking };
        }
        match self.turn {
            Turn::Thinking => return OrbState::Thinking,
            Turn::Working => return OrbState::Working,
            Turn::Idle => {}
        }
        if !self.core_ready || self.ears == Ears::Loading {
            return OrbState::Loading;
        }
        OrbState::Waiting
    }

    /// The agent waits for a yes or a no (a dialog that belongs to a command is not that).
    fn asks_the_person(&self) -> bool {
        self.pending.is_none() && self.ask.as_ref().is_some_and(|ask| ask.request_id != FRONTEND_REQUEST)
    }

    fn speaking(&self, now: Instant) -> bool {
        self.voice_busy || self.pretend_until.is_some_and(|until| now < until)
    }

    /// Per frame: choose the state, move the orb, give it the voice's loudness.
    fn tick(&mut self, now: Instant, dt: f32, clock: f32) {
        if let Some(demo) = &mut self.demo
            && now.duration_since(demo.since) >= Duration::from_secs(demo_seconds(ALL_STATES[demo.index]))
        {
            demo.index = (demo.index + 1) % ALL_STATES.len();
            demo.since = now;
            demo.played = 0;
        }
        self.play_demo(now);
        for tall in 0..modules::TALL_COUNT {
            self.modules.set_tall_room(tall, self.tall[tall].covered.clone());
        }
        self.grow_tall(now, dt);
        self.modules.tick(self.places, now);
        if self.overheard.as_ref().is_some_and(|(_, at)| now.duration_since(*at) >= Duration::from_secs(HEARD_SECONDS)) {
            self.overheard = None;
        }
        if self.toast.as_ref().is_some_and(|(_, _, at)| now.duration_since(*at) >= Duration::from_secs(TOAST_SECONDS)) {
            self.toast = None;
        }
        if let Some(ask) = &mut self.ask {
            ask.follow(LIST_ROWS);
        }
        self.sync_menu(now);
        self.open_conversation(now);
        let state = self.resolve(now);
        self.orb.set_state(state);
        self.level *= (-dt * 6.0).exp();
        // Between two reports of the ears (ten a second) the loudness falls, so silence shows at once.
        self.mic_level *= (-dt * 5.0).exp();
        let talking = matches!(state, OrbState::Speaking | OrbState::Narrating);
        let target = if talking && (self.demo.is_some() || !self.has_voice) {
            // Without a voice reporting its loudness the swell follows a speech-like rhythm.
            (0.5 + 0.5 * (clock * 7.0).sin() * (clock * 2.3).cos()).abs()
        } else if talking {
            self.level
        } else if self.hearing {
            // The person's own voice: the orb moves with it, also while it only waits for
            // the wake word, so they can see that the microphone hears them.
            self.mic_level
        } else {
            0.0
        };
        // The light follows the loudness quickly but without flicker.
        self.orb.level += (target - self.orb.level) * (1.0 - (-dt * 14.0).exp());
        self.orb.pause = if self.hearing { self.pause } else { 0.0 };
        self.orb.step(dt);
    }

    /// `--demo` shows the modules in the working state and a voice with a pause in the listening one.
    fn play_demo(&mut self, now: Instant) {
        let Some(demo) = &mut self.demo else { return };
        let ms = now.duration_since(demo.since).as_millis() as u64;
        match ALL_STATES[demo.index] {
            OrbState::Working => {
                let script = demo_work();
                let due: Vec<Value> = script.iter().skip(demo.played).take_while(|(at, _)| *at <= ms).map(|(_, event)| event.clone()).collect();
                demo.played += due.len();
                for event in due {
                    self.on_work(&event, now);
                }
            }
            OrbState::Listening => {
                // Two seconds of speech, then the pause that ends the phrase.
                let seconds = ms as f32 / 1000.0;
                self.hearing = true;
                self.pause = ((seconds - 2.0) / 1.8).clamp(0.0, 1.0);
                self.mic_level = if seconds < 2.0 { (0.45 + 0.4 * (seconds * 9.0).sin() * (seconds * 3.1).cos()).abs() } else { 0.0 };
            }
            _ => self.hearing = false,
        }
    }

    /// The tools Kvist uses become modules: a call starting, a call ending.
    fn on_work(&mut self, event: &Value, now: Instant) {
        let text = |key: &str| event.get(key).and_then(Value::as_str).unwrap_or("");
        let call = text("call_id");
        match text("type") {
            "action_result" => {
                self.modules.end(call, event.get("ok").and_then(Value::as_bool).unwrap_or(false), text("summary"), now);
                // The write's real diff, with the file's own line numbers, takes the place of the one from its arguments.
                // Only the card of a command takes it; for any other call this finds nothing to finish.
                if self.modules.is_shell_call(call) {
                    self.edit_generation += 1;
                    let output = text("output");
                    self.modules.finish_shell(call, if output.is_empty() { text("summary") } else { output }, event.get("exit_code").and_then(Value::as_i64), now);
                }
                if let Some(patch) = event.get("patch").and_then(Value::as_str).filter(|patch| !patch.is_empty()) {
                    let count = |key: &str| event.get(key).and_then(Value::as_u64).unwrap_or(0) as u32;
                    self.edit_generation += 1;
                    let (added, removed) = (count("lines_added"), count("lines_removed"));
                    self.modules.set_preview(call, Preview { path: text("path").to_string(), patch: patch.to_string(), added, removed });
                }
            }
            kind @ ("step" | "call_begin") => {
                let action = if kind == "step" { text("action_type") } else { text("tool") };
                let Some(module) = modules::kind(action) else { return };
                let skill = event.pointer("/action_args/skill").and_then(Value::as_str).unwrap_or("");
                let label = if kind == "step" { text("action_description") } else { text("description") };
                // A step without a call id (an older core) is told apart by its number.
                let call = if call.is_empty() { format!("step-{}", event.get("step").and_then(Value::as_i64).unwrap_or(0)) } else { call.to_string() };
                self.modules.begin(text("batch_id"), &call, module, skill, label);
                // A write shows what it puts in the file as soon as it is known.
                if let Some(preview) = event.get("action_args").and_then(|args| preview_from_args(action, args)) {
                    self.edit_generation += 1;
                    self.modules.set_preview(&call, preview);
                }
                // A command shows as a terminal does.
                if let Some(command) = event.get("action_args").and_then(|args| shell_command(action, args)) {
                    self.edit_generation += 1;
                    self.modules.set_shell(&call, &command);
                }
            }
            "reasoning_start" => {
                self.thoughts += 1;
                self.thought_text.clear();
            }
            "reasoning_chunk" => self.on_thought(text("text"), event.get("append").and_then(Value::as_bool)),
            "reasoning_commit" => self.end_thought(now),
            _ => {}
        }
    }

    /// A tall card is as tall as its words need (up to its window) and grows with them, not by a
    /// jump of a row: its height eases towards the target.
    fn grow_tall(&mut self, now: Instant, dt: f32) {
        for index in 0..modules::TALL_COUNT {
            let wanted = {
                let card = self.modules.shown().iter().find(|module| module.slot == modules::TALL_SLOT + index);
                match (card, self.tall[index].rect) {
                    (Some(card), Some(rect)) => Some(3.0 + self.tall_line_count(index, card, rect, now) as f32),
                    _ => None,
                }
            };
            let tall = &mut self.tall[index];
            match wanted {
                None => tall.open = false,
                Some(wanted) if tall.open => tall.rows += (wanted - tall.rows) * (1.0 - (-dt * 12.0).exp()),
                Some(wanted) => {
                    tall.rows = wanted;
                    tall.open = true;
                }
            }
        }
    }

    /// How many lines a tall card's words fill, at least one and at most its window.
    fn tall_line_count(&self, index: usize, card: &Module, rect: Rect, now: Instant) -> usize {
        let count = if index == modules::TALL_THOUGHTS {
            thought_lines(&card.detail, thought_width(rect)).len()
        } else {
            let since = card.shell.as_ref().and_then(|shell| shell.got).map(|got| now.saturating_duration_since(got).as_secs_f32());
            self.with_tall_lines(index, card, rect, |head, lines| shown_rows(head, lines.len(), since, card.shell.is_some()))
        };
        count.clamp(1, modules::TALL_LINES as usize)
    }

    /// The lines of the write or the command on the card of `card`, made for the card's width and
    /// kept until the content or the width changes; `with` gets how many of them are the command
    /// at the head, and the lines.
    fn with_tall_lines<T>(&self, index: usize, card: &Module, rect: Rect, with: impl FnOnce(usize, &[text::Line]) -> T) -> T {
        let width = rect.width.saturating_sub(2 * MODULE_PAD_X) as usize;
        let mut cached = self.tall_lines[index].borrow_mut();
        if !cached.as_ref().is_some_and(|made| made.generation == self.edit_generation && made.width == width) {
            let (head, lines) = match (&card.preview, &card.shell) {
                (Some(preview), _) if index == modules::TALL_EDITS => {
                    (0, diff::inline(&Patch::parse(&preview.patch, diff::language(&preview.path)), width, "…"))
                }
                (_, Some(shell)) => shell_lines(shell, width),
                _ => (0, Vec::new()),
            };
            *cached = Some(TallLines { generation: self.edit_generation, width, head, lines });
        }
        let made = cached.as_ref().expect("just made");
        with(made.head, &made.lines)
    }

    /// The card of the model's current thinking.
    fn thought_call(&self) -> String {
        format!("thoughts:{}", self.thoughts)
    }

    /// The model's thinking as it streams: its card opens with the first words. `append` says that
    /// `words` are the next bytes of the thought (what the core sends now); without it they are the
    /// whole thought so far (what older cores sent). A provider that does not let the thinking be
    /// seen sends none, and then there is no card.
    fn on_thought(&mut self, words: &str, append: Option<bool>) {
        if append == Some(true) {
            self.thought_text.push_str(words);
        } else {
            self.thought_text = words.to_string();
        }
        // Only the end of it is shown, and the whole of a long thought is not kept.
        let from = self.thought_text.char_indices().rev().nth(THOUGHT_KEPT_CHARS * 3).map_or(0, |(at, _)| at);
        self.thought_text.drain(..from);
        // Lines stay lines (a thought can hold code or a list); the spaces inside them are one each.
        let shown: Vec<String> =
            self.thought_text.lines().map(|line| line.split_whitespace().collect::<Vec<_>>().join(" ")).filter(|line| !line.is_empty()).collect();
        let shown = shown.join("\n");
        if shown.is_empty() {
            return;
        }
        let from = shown.char_indices().rev().nth(THOUGHT_KEPT_CHARS).map_or(0, |(at, _)| at);
        let call = self.thought_call();
        if self.modules.has(&call) {
            self.modules.set_detail(&call, &shown[from..]);
        } else {
            self.modules.begin("", &call, ModuleKind::Thoughts, "", &shown[from..]);
        }
    }

    /// The model has finished thinking: its card shows that, and closes.
    fn end_thought(&mut self, now: Instant) {
        let call = self.thought_call();
        if self.modules.has(&call) {
            self.modules.end(&call, true, "", now);
        }
    }

    /// Once the assistant has finished speaking, tell the ears the person may answer without the wake word.
    fn open_conversation(&mut self, now: Instant) {
        let Some(converse) = self.converse else { return };
        let quiet = !self.speaking(now) && (converse.mid_turn || self.turn == Turn::Idle);
        if !converse.voice_started || !quiet || self.ears == Ears::Absent {
            return;
        }
        self.converse = None;
        self.outbox.push(Out::Ears(json!({"type": "converse", "seconds": converse.seconds, "min_words": converse.min_words})));
    }

    fn arm_conversation(&mut self, seconds: f32, min_words: u32, mid_turn: bool) {
        self.converse = Some(Converse { seconds, min_words, voice_started: !self.has_voice, mid_turn });
    }

    fn say(&mut self, text: &str, kind: Kind, now: Instant) {
        let words = speakable(text);
        if words.is_empty() {
            return;
        }
        if kind == Kind::Answer && words == self.last_spoken {
            return;
        }
        // A line the person is asked to answer is not interrupted by a narrator.
        if kind == Kind::Narration && self.asks_the_person() {
            return;
        }
        self.caption = words.clone();
        self.caption_from_person = false;
        if kind == Kind::Answer {
            self.last_spoken = words.clone();
            self.arm_conversation(CONVERSE_SECONDS, CONVERSE_MIN_WORDS, false);
        }
        if self.has_voice {
            // The voice drops a narration line while it is busy, so a narration
            // never changes what the orb says about an answer being spoken.
            if kind == Kind::Answer || !self.voice_busy {
                self.voice_kind = kind;
            }
            self.speak_id += 1;
            self.outbox.push(Out::Voice(json!({"type": "speak", "id": self.speak_id, "kind": kind.wire(), "text": words})));
        } else if kind == Kind::Answer || self.pretend_until.is_none_or(|until| now >= until) {
            self.voice_kind = kind;
            self.pretend_until = Some(now + speech_estimate(&words));
        }
    }

    fn on_core(&mut self, event: &Value, now: Instant) {
        let text = |key: &str| event.get(key).and_then(Value::as_str).unwrap_or("");
        match text("type") {
            "hello" => {
                self.core_ready = true;
                self.provider = text("provider").to_string();
                if let Some(model) = event.pointer("/models/fast").and_then(Value::as_str) {
                    self.model = model.to_string();
                }
                // A launch opens its own session, as the other interfaces do: the last one's
                // context would otherwise be answered from. The person can come back to it from the menu.
                if self.fresh_session {
                    self.fresh_session = false;
                    self.start_command("new", true);
                }
            }
            "control" => {
                self.observer = (text("role") == "observer").then(|| text("notice").to_string());
                if let Some(notice) = &self.observer {
                    self.notice = notice.clone();
                }
            }
            "remote_state" if text("state") != "connected" => {
                let reason = text("reason");
                self.notice = if reason.is_empty() { text("host").to_string() } else { reason.to_string() };
            }
            "session" | "session_title" => {
                let title = text("title");
                let name = if title.is_empty() { text("session") } else { title };
                // A title can arrive for a session the person has already left.
                let current = self.session_id.is_empty() || text("session") == self.session_id;
                if text("type") == "session" {
                    self.session_id = text("session").to_string();
                    self.title = title.to_string();
                } else if current {
                    self.title = title.to_string();
                }
                if !name.is_empty() && current {
                    self.session = name.to_string();
                }
            }
            "command_result" => self.on_command_result(event, now),
            "usage" => {
                if !text("model").is_empty() {
                    self.model = text("model").to_string();
                }
                let (used, limit) = (event.get("context_tokens").and_then(Value::as_f64), event.get("context_limit").and_then(Value::as_f64));
                if let (Some(used), Some(limit)) = (used, limit)
                    && limit > 0.0
                {
                    self.context = Some((used / limit).clamp(0.0, 1.0) as f32);
                }
            }
            "notice" => self.toast(text("text"), text("level") == "info", now),
            "wake" => self.turn = Turn::Thinking,
            "reasoning_start" | "reasoning_chunk" => {
                self.turn = Turn::Thinking;
                self.on_work(event, now);
            }
            "reasoning_commit" => self.on_work(event, now),
            "step" if !ENDINGS.contains(&text("action_type")) => {
                self.turn = Turn::Working;
                self.on_work(event, now);
            }
            "call_begin" | "action_result" => self.on_work(event, now),
            "stream_start" => self.stream_text.clear(),
            "stream_chunk" => {
                if event.get("append").and_then(Value::as_bool) == Some(false) {
                    self.stream_text.clear();
                }
                self.stream_text.push_str(text("text"));
                self.caption = self.stream_text.clone();
                self.caption_from_person = false;
            }
            // A message is spoken only when it is the assistant talking to the person: a runtime
            // note (a warning, a clean-up line) or a skill's message is not for the listener.
            "message" if !matches!(text("source"), "" | "reply") => {}
            // `shown`: the stream already said it.
            "final" if event.get("shown").and_then(Value::as_bool) == Some(true) => {}
            "stream_commit" | "final" | "message" => {
                let words = if text("type") == "final" { text("message") } else if text("type") == "message" { text("markdown") } else { text("text") };
                self.say(words, Kind::Answer, now);
            }
            "narration" => self.say(text("text"), Kind::Narration, now),
            "input_request" => self.on_ask(event),
            "turn_end" => {
                self.turn = Turn::Idle;
                self.stream_text.clear();
                self.modules.finish_all(now);
            }
            "error" => {
                self.error_until = Some(now + Duration::from_secs(ERROR_SECONDS));
                self.caption = text("message").to_string();
                self.caption_from_person = false;
            }
            _ => {}
        }
    }

    fn start_command(&mut self, command: &str, silent: bool) {
        self.start_command_with(command, &[], silent);
    }

    fn start_command_with(&mut self, command: &str, args: &[&str], silent: bool) {
        if self.refuse_while_watching() {
            return;
        }
        self.pending = Some(Pending { silent });
        self.outbox.push(Out::Core(json!({"type": "command", "name": command, "args": args})));
    }

    /// A request typed instead of spoken: the same path as heard words.
    fn send_goal(&mut self, text: &str) {
        if self.refuse_while_watching() {
            return;
        }
        self.caption = text.to_string();
        self.caption_from_person = true;
        self.converse = None;
        self.stop_voice();
        self.outbox.push(Out::Core(json!({"type": "goal", "text": text})));
        self.turn = Turn::Thinking;
        self.stream_text.clear();
    }

    /// Another frontend writes to this session; the core would drop anything sent, so say so instead.
    fn refuse_while_watching(&mut self) -> bool {
        let Some(notice) = &self.observer else { return false };
        self.notice = notice.clone();
        true
    }

    fn toast(&mut self, message: &str, ok: bool, now: Instant) {
        self.toast = Some((message.to_string(), ok, now));
    }

    /// Enter in the input line: `/tui` opens the interface picker (the launcher's, not the core's),
    /// any other `/command` runs in the core with its words, anything else is a request.
    fn submit_input(&mut self, now: Instant) {
        let Some(line) = self.input.take() else { return };
        let line = line.trim().to_string();
        if line.is_empty() {
            return;
        }
        let Some(command) = line.strip_prefix('/') else {
            return self.send_goal(&line);
        };
        let mut words = command.split_whitespace();
        let name = words.next().unwrap_or("");
        if name == "tui" {
            return self.ask_frontend();
        }
        if self.pending.is_some() {
            self.input = Some(line);
            return self.toast(strings().voice_busy, false, now);
        }
        let args: Vec<&str> = words.collect();
        self.start_command_with(name, &args, false);
    }

    /// The terminal interfaces the launcher can start, this one first highlighted.
    /// The list of the Ink app's `/tui` (frontends/tui/ink/commands.ts).
    fn ask_frontend(&mut self) {
        let s = strings();
        let others = crate::strings::strings();
        let choices = [
            ("glass", "Classic", s.voice_frontend_classic),
            ("tui", "Ink", others.frontend_ink),
            ("claude", "Claude-style", others.frontend_claude),
            ("opencode", "OpenCode", others.frontend_opencode),
            ("voice", "Voice", s.voice_frontend_here),
            ("terminal", "Classic Legacy", others.frontend_classic),
        ]
        .iter()
        .map(|&(value, label, detail)| Choice { value: value.into(), label: label.into(), detail: detail.into() })
        .collect();
        self.menu = None;
        self.ask = Some(Ask::local_choice(FRONTEND_REQUEST, others.frontend_title, choices, "voice"));
    }

    /// Leaves for another interface: the launcher reads its name from `KVIST_SWITCH_FILE`
    /// when this program exits with status 64.
    fn switch_frontend(&mut self, chosen: Option<&str>, now: Instant) {
        let Some(chosen) = chosen.filter(|value| *value != "voice") else { return };
        self.write_switch(format!("{chosen}\n"), now);
    }

    /// The core asked for this screen to end so the launcher starts it again somewhere else (`effects.relaunch`):
    /// leave it a note of where (this interface, an empty line, the choice).
    fn start_again(&mut self, choice: &str, now: Instant) {
        self.write_switch(format!("voice\n\n{choice}"), now);
    }

    fn write_switch(&mut self, contents: String, now: Instant) {
        let Some(path) = std::env::var_os("KVIST_SWITCH_FILE") else {
            return self.toast(strings().voice_needs_launcher, false, now);
        };
        match std::fs::write(&path, contents) {
            Ok(()) => {
                self.exit_code = SWITCH_FRONTEND;
                self.quit = true;
            }
            Err(error) => self.toast(&error.to_string(), false, now),
        }
    }

    /// A dialog of the core. One this screen cannot show is cancelled, and the person is told.
    fn on_ask(&mut self, event: &Value) {
        // The driver's to answer; whoever takes over is sent the ones still open.
        if self.observer.is_some() {
            return;
        }
        let Some(ask) = Ask::from_request(event) else {
            let id = event.get("request_id").and_then(Value::as_str).unwrap_or("");
            self.outbox.push(Out::Core(json!({"request_id": id, "value": Value::Null})));
            self.notice = strings().voice_text_needed.to_string();
            return;
        };
        // The agent's own question is read aloud, so that someone who is not looking can answer it.
        if self.pending.is_none() && self.has_voice && !matches!(ask.body, Body::Text { .. }) {
            let spoken = if ask.text.is_empty() { ask.title.clone() } else { format!("{}. {}", ask.title, ask.text) };
            self.speak_id += 1;
            self.outbox.push(Out::Voice(json!({"type": "speak", "id": self.speak_id, "kind": "answer", "text": speakable(&spoken)})));
        }
        if self.pending.is_none() && !matches!(ask.body, Body::Text { .. }) {
            self.arm_conversation(QUESTION_SECONDS, 1, true);
        }
        self.menu = None;
        self.ask = Some(ask);
    }

    /// The end of a command: show how it went, and take over what it changed.
    fn on_command_result(&mut self, event: &Value, now: Instant) {
        let pending = self.pending.take();
        let name = event.get("name").and_then(Value::as_str).unwrap_or("");
        let ok = event.get("ok").and_then(Value::as_bool).unwrap_or(false);
        let message = core::localized(event.get("message").and_then(Value::as_str).unwrap_or(""), crate::strings::russian());
        let effects = event.get("effects");
        let effect = |key: &str| effects.and_then(|e| e.get(key));
        if let Some(choice) = effect("relaunch").and_then(|r| r.get("switch")).and_then(Value::as_str)
            && !choice.is_empty()
        {
            return self.start_again(choice, now);
        }
        if let Some(provider) = effect("provider").and_then(Value::as_str) {
            self.provider = provider.to_string();
        }
        if let Some(model) = effect("models").and_then(|m| m.get("fast")).and_then(Value::as_str) {
            self.model = model.to_string();
        }
        let moved = effect("changed").and_then(Value::as_bool) == Some(true) || (ok && name == "new");
        if moved && matches!(name, "new" | "session") {
            let title = effect("title").and_then(Value::as_str);
            let id = effect("session").and_then(Value::as_str);
            self.session = title.or(id).unwrap_or("").to_string();
            self.session_id = id.unwrap_or("").to_string();
            self.title = title.unwrap_or("").to_string();
            self.reset_conversation();
        }
        // A dialog the person closed is not news.
        let cancelled = effect("cancelled").and_then(Value::as_bool) == Some(true);
        if !pending.is_some_and(|p| p.silent) && !message.is_empty() && !cancelled {
            self.toast = Some((message.to_string(), ok, now));
        }
    }

    /// A different session is open: what was said in the last one is no longer on screen.
    fn reset_conversation(&mut self) {
        self.caption.clear();
        self.caption_from_person = false;
        self.stream_text.clear();
        self.last_spoken.clear();
        self.overheard = None;
        self.turn = Turn::Idle;
        self.context = None;
        self.converse = None;
        self.modules.clear();
        self.stop_voice();
    }

    fn on_ears(&mut self, event: &Value, now: Instant) {
        match event.get("event").and_then(Value::as_str).unwrap_or("") {
            "state" => {
                let next = match event.get("state").and_then(Value::as_str).unwrap_or("") {
                    "loading" => Ears::Loading,
                    "listening" => Ears::Listening,
                    "transcribing" => Ears::Transcribing,
                    _ => Ears::Idle,
                };
                // The person speaking cuts the assistant off.
                if matches!(next, Ears::Listening | Ears::Transcribing) && self.ears != next {
                    self.stop_voice();
                }
                self.ears = next;
            }
            "text" => {
                let heard = event.get("text").and_then(Value::as_str).unwrap_or("").trim().to_string();
                if !heard.is_empty() {
                    self.on_heard(&heard, now);
                }
            }
            "heard" => {
                let phrase = event.get("text").and_then(Value::as_str).unwrap_or("").trim();
                if !phrase.is_empty() {
                    self.overheard = Some((phrase.to_string(), now));
                }
            }
            "phrase" => {
                self.hearing = event.get("on").and_then(Value::as_bool) == Some(true);
                self.pause = 0.0;
            }
            "level" => {
                let number = |key: &str| event.get(key).and_then(Value::as_f64).unwrap_or(0.0).clamp(0.0, 1.0) as f32;
                self.mic_level = number("value");
                self.pause = number("pause");
            }
            // Words meant for Kvist that the recognition lost: say so, or the person waits for nothing.
            "missed" => self.toast(strings().voice_missed, false, now),
            "gone" => {
                self.ears = Ears::Absent;
                self.hearing = false;
                self.notice = strings().voice_stt_gone.to_string();
            }
            _ => {}
        }
    }

    fn on_heard(&mut self, heard: &str, _now: Instant) {
        self.overheard = None;
        // While a dialog is open or a command runs, speech is never a task: at most it answers a question.
        if let Some(ask) = &self.ask {
            // The interface picker is typed, never answered by a stray "no".
            let reply = if ask.request_id == FRONTEND_REQUEST { None } else { voice_overlay::voice_reply(heard, ask) };
            if let Some(reply) = reply {
                self.converse = None;
                self.caption = heard.to_string();
                self.caption_from_person = true;
                self.outbox.push(Out::Core(reply));
                self.ask = None;
            }
            return;
        }
        if self.menu.is_some() || self.pending.is_some() || self.input.is_some() {
            return;
        }
        self.send_goal(heard);
    }

    fn on_voice(&mut self, event: &Value) {
        match event.get("event").and_then(Value::as_str).unwrap_or("") {
            "speaking" => {
                self.set_voice_busy(true);
                if let Some(converse) = &mut self.converse {
                    converse.voice_started = true;
                }
            }
            "idle" => {
                self.set_voice_busy(false);
                // A line the voice dropped (it was busy) never began: do not wait for it.
                if let Some(converse) = &mut self.converse {
                    converse.voice_started = true;
                }
            }
            "level" => {
                self.level = event.get("value").and_then(Value::as_f64).unwrap_or(0.0).clamp(0.0, 1.0) as f32;
            }
            "gone" => {
                self.has_voice = false;
                self.set_voice_busy(false);
                if let Some(converse) = &mut self.converse {
                    converse.voice_started = true;
                }
                self.notice = strings().voice_tts_gone.to_string();
            }
            _ => {}
        }
    }

    /// The ears are muted while the voice talks: its own words must not be heard as the wake word.
    fn set_voice_busy(&mut self, busy: bool) {
        self.voice_busy = busy;
        self.sync_mute();
    }

    /// The ears are muted while the voice talks and while the person has the microphone off.
    fn sync_mute(&mut self) {
        let muted = self.voice_busy || self.mic_off;
        if self.mute_sent != muted {
            self.mute_sent = muted;
            self.outbox.push(Out::Ears(json!({"type": "mute", "value": muted})));
        }
    }

    fn stop_voice(&mut self) {
        self.pretend_until = None;
        if self.has_voice {
            self.outbox.push(Out::Voice(json!({"type": "stop"})));
        }
    }

    fn key(&mut self, key: KeyEvent, now: Instant) {
        if key.kind == KeyEventKind::Release {
            return;
        }
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && key.code == KeyCode::Char('c') {
            self.quit_or_arm(now);
            return;
        }
        if self.ask.is_some() {
            self.ask_key(key.code, ctrl, now);
            return;
        }
        if let Some(selected) = self.menu {
            self.menu_key(selected, key.code);
            return;
        }
        if self.input.is_some() {
            self.input_key(key.code, ctrl, now);
            return;
        }
        match key.code {
            KeyCode::Esc if self.demo.is_some() => self.quit = true,
            KeyCode::Esc => self.toggle_microphone(now),
            KeyCode::Tab => self.menu = Some(0),
            KeyCode::Enter if self.observer.is_some() => self.outbox.push(Out::Core(json!({"type": "takeover"}))),
            KeyCode::Char('q') if self.demo.is_some() => self.quit = true,
            KeyCode::Char(' ') | KeyCode::Right if self.demo.is_some() => self.step_demo(1, now),
            KeyCode::Left if self.demo.is_some() => self.step_demo(ALL_STATES.len() - 1, now),
            KeyCode::Char(c) if !ctrl && self.demo.is_none() => {
                self.input = Some(c.to_string());
                self.pick = 0;
                self.sync_menu(now);
            }
            _ => {}
        }
    }

    /// Ctrl+C once arms the quit and says so; Ctrl+C again within a few seconds leaves.
    fn quit_or_arm(&mut self, now: Instant) {
        if self.demo.is_some() || self.quit_armed.is_some_and(|at| now.saturating_duration_since(at) < QUIT_CONFIRM) {
            self.quit = true;
            return;
        }
        self.quit_armed = Some(now);
        self.toast(strings().voice_quit_hint, true, now);
    }

    /// Esc switches the microphone off and on; typing still works while it is off.
    fn toggle_microphone(&mut self, now: Instant) {
        self.mic_off = !self.mic_off;
        self.sync_mute();
        let message = if self.mic_off { strings().voice_mic_off } else { strings().voice_mic_on };
        self.toast(message, true, now);
    }

    /// The commands the typed line asks for; none unless it starts with `/`.
    fn suggestions(&self) -> Vec<&'static slash::Command> {
        if self.ask.is_some() || self.menu.is_some() {
            return Vec::new();
        }
        self.input.as_deref().map(slash::suggest).unwrap_or_default()
    }

    /// The slash list's chosen row.
    fn chosen(&self) -> usize {
        self.pick.min(self.suggestions().len().saturating_sub(1))
    }

    /// Opens the slash list with a short animation when it appears; it goes at once when the
    /// list is gone, since its rows are gone with it.
    fn sync_menu(&mut self, now: Instant) {
        let open = !self.suggestions().is_empty();
        if open && self.menu_in.target() == 0.0 {
            self.menu_in.go(1.0, now, 170, Ease::OutCubic);
        } else if !open && self.menu_in.target() != 0.0 {
            self.menu_in = Tween::settled(0.0);
        }
    }

    /// Keys in the input line: typing, Backspace, Ctrl+U to clear, Enter sends, Esc drops the line.
    /// While the slash list is open the arrows walk it, Tab completes the name and Enter runs the chosen command.
    fn input_key(&mut self, code: KeyCode, ctrl: bool, now: Instant) {
        let shown = self.suggestions().len();
        match code {
            KeyCode::Up | KeyCode::Down if shown > 0 => {
                self.pick = slash::step(shown, self.chosen(), if code == KeyCode::Up { -1 } else { 1 });
                return;
            }
            KeyCode::Tab if shown > 0 => {
                self.input = Some(format!("/{} ", self.suggestions()[self.chosen()].name));
                self.pick = 0;
                return self.sync_menu(now);
            }
            KeyCode::Enter if shown > 0 => match slash::resolve_enter(self.input.as_deref().unwrap_or(""), self.chosen()) {
                slash::Enter::Fill(line) => self.input = Some(line),
                slash::Enter::Submit(line) => {
                    self.input = Some(line);
                    self.submit_input(now);
                }
            },
            KeyCode::Esc => self.input = None,
            KeyCode::Enter => self.submit_input(now),
            KeyCode::Backspace => {
                if let Some(line) = self.input.as_mut() {
                    line.pop();
                }
            }
            KeyCode::Char('u') if ctrl => self.input = Some(String::new()),
            KeyCode::Char(c) if !ctrl => {
                if let Some(line) = self.input.as_mut() {
                    line.push(c);
                }
            }
            _ => {}
        }
        self.pick = 0;
        self.sync_menu(now);
    }

    fn step_demo(&mut self, by: usize, now: Instant) {
        if let Some(demo) = &mut self.demo {
            demo.index = (demo.index + by) % ALL_STATES.len();
            demo.since = now;
        }
    }

    /// Keys in a dialog: arrows choose, typing searches or fills the field, Enter answers, Esc cancels.
    fn ask_key(&mut self, code: KeyCode, ctrl: bool, now: Instant) {
        let mut reply = None;
        if let Some(ask) = self.ask.as_mut() {
            match code {
                KeyCode::Esc => reply = Some(ask.cancel()),
                KeyCode::Enter => reply = ask.accept(),
                KeyCode::Up | KeyCode::Left => ask.step(-1),
                KeyCode::Down | KeyCode::Right | KeyCode::Tab => ask.step(1),
                KeyCode::PageUp => ask.step(-(LIST_ROWS as i32)),
                KeyCode::PageDown => ask.step(LIST_ROWS as i32),
                KeyCode::Backspace => ask.backspace(),
                KeyCode::Char('u') if ctrl => ask.clear(),
                KeyCode::Char(c) if !ctrl => ask.type_char(c),
                _ => {}
            }
        }
        let Some(reply) = reply else { return };
        self.ask = None;
        if reply.get("request_id").and_then(Value::as_str) == Some(FRONTEND_REQUEST) {
            return self.switch_frontend(reply.get("value").and_then(Value::as_str), now);
        }
        self.outbox.push(Out::Core(reply));
    }

    /// Keys in the menu: arrows and Enter, or the letter of an item; Esc closes it.
    fn menu_key(&mut self, selected: usize, code: KeyCode) {
        match code {
            KeyCode::Esc | KeyCode::Tab => self.menu = None,
            KeyCode::Up => self.menu = Some(selected.saturating_sub(1)),
            KeyCode::Down => self.menu = Some((selected + 1).min(MENU.len() - 1)),
            KeyCode::Enter => self.run_menu(selected),
            KeyCode::Char(c) => {
                if let Some(index) = MENU.iter().position(|item| item.key == c.to_ascii_lowercase()) {
                    self.run_menu(index);
                }
            }
            _ => {}
        }
    }

    fn run_menu(&mut self, index: usize) {
        self.menu = None;
        if self.demo.is_some() {
            return;
        }
        if MENU[index].command == "tui" {
            return self.ask_frontend();
        }
        self.start_command(MENU[index].command, false);
    }

    /// Text pasted into a field of a dialog.
    fn paste(&mut self, text: &str) {
        if let Some(ask) = &mut self.ask {
            ask.paste(text);
        } else if self.menu.is_none() {
            let flat = text.split_whitespace().collect::<Vec<_>>().join(" ");
            self.input.get_or_insert_with(String::new).push_str(&flat);
        }
    }

    fn take_outbox(&mut self) -> Vec<Out> {
        std::mem::take(&mut self.outbox)
    }
}

// ───────────────────────────── pure helpers ─────────────────────────────

/// The words of a reply as they should be said: markdown marks and line breaks dropped.
fn speakable(text: &str) -> String {
    let plain: String = math::spoken(text).chars().filter(|c| !matches!(c, '`' | '*' | '#')).collect();
    plain.split_whitespace().collect::<Vec<_>>().join(" ")
}

/// How long a line takes to say, for a screen without a voice: a third of a second per word.
fn speech_estimate(words: &str) -> Duration {
    let count = words.split_whitespace().count() as f32;
    Duration::from_secs_f32((0.4 + count * 0.32).clamp(1.0, 12.0))
}

/// Where the orb sits: its size in cells, its corner, and the size of its picture in pixels.
#[derive(Clone, Copy, Debug, PartialEq)]
struct OrbBox {
    cols: u16,
    rows: u16,
    col: u16,
    row: u16,
    width: u32,
    height: u32,
}

impl OrbBox {
    fn cells(&self) -> Rect {
        Rect::new(self.col, self.row, self.cols, self.rows)
    }
}

/// Rows kept below the orb for its caption, what is said, and the status line.
const TEXT_ROWS: u16 = 6;

fn orb_box(grid: Grid) -> OrbBox {
    let room = grid.rows.saturating_sub(TEXT_ROWS + 2).max(4);
    let rows = ((grid.rows as f32 * 0.64) as u16).clamp(4, room);
    let side_px = rows as u32 * grid.ch;
    let cols = (side_px.div_ceil(grid.cw) as u16).min(grid.cols.saturating_sub(2)).max(2);
    let col = (grid.cols - cols) / 2;
    let row = (grid.rows.saturating_sub(TEXT_ROWS + rows) / 2).max(1);
    // The picture has the pixels of the cells it covers (a whole number of cells is a little
    // wider than it is tall), so the ball is a true circle drawn at the size it is shown at.
    // A very tall window gets a smaller picture of the same shape, scaled up by the terminal.
    let (width, height) = (cols as u32 * grid.cw, side_px);
    let shrink = (ORB_TEXTURE_MAX as f32 / height as f32).min(1.0);
    let scaled = |px: u32| ((px as f32 * shrink).round() as u32).max(8);
    OrbBox { cols, rows, col, row, width: scaled(width), height: scaled(height) }
}

/// A module's card on its way out of the ball or back: the glass as it is now in pixels
/// (a line, a wider line, a card unfolding), the twig that reaches it, and how visible its text is.
#[derive(Clone, Copy, Debug, PartialEq)]
struct ModuleSpot {
    slot: usize,
    /// Its place: where the words go once it is open.
    cells: Rect,
    x: f32,
    y: f32,
    width: f32,
    height: f32,
    /// How open it is, 0 a line and 1 a card.
    open: f32,
    /// How many lines of words the glass is tall enough for (the thoughts card grows with its text).
    lines: usize,
    /// The colour of its kind: the line it comes out as shines with it.
    glow: [u8; 3],
    /// Seconds since it came out of the ball.
    age: f32,
    /// Seconds since a command's output came, for the terminal card.
    since_result: Option<f32>,
    twig: Twig,
    text: f32,
}

/// Where and how big a module's glass picture was made (x, y, width, height in pixels).
type PlateKey = (i64, i64, u32, u32);

/// The thickness of the line a card comes out of the ball as.
const FOLD_LINE_PX: f32 = 3.0;

fn status_colour(module: &Module) -> ratatui::style::Color {
    match module.status() {
        Status::Running => module_face(module).2,
        Status::Done => theme::MOSS,
        Status::Failed => theme::ROSEHIP,
    }
}

/// The terminal's lines: the prompt and the command (a few rows at most), then what it printed,
/// then how it ended if it failed; and how many of them are the command.
fn shell_lines(shell: &Shell, width: usize) -> (usize, Vec<text::Line>) {
    let prompt = Style::default().fg(theme::MOSS).add_modifier(Modifier::BOLD);
    let command: text::Line = vec![(shell.command.clone(), Style::default())];
    let mut lines: Vec<text::Line> = text::wrap(&command, width.saturating_sub(2).max(4))
        .into_iter()
        .take(3)
        .enumerate()
        .map(|(index, row)| {
            let mut line: text::Line = vec![((if index == 0 { "$ " } else { "  " }).to_string(), prompt)];
            line.extend(row.into_iter().map(|(word, _)| (word, Style::default().fg(theme::FOG))));
            line
        })
        .collect();
    let head = lines.len();
    if let Some(output) = &shell.output {
        for source in output.trim_end().lines() {
            let spans: text::Line = vec![(source.replace('\t', "    "), Style::default())];
            lines.extend(
                text::wrap(&spans, width.max(4)).into_iter().map(|row| row.into_iter().map(|(word, _)| (word, Style::default().fg(theme::MIST))).collect::<text::Line>()),
            );
        }
        if let Some(code) = shell.exit.filter(|code| *code != 0) {
            lines.push(vec![(format!("exit code {code}"), Style::default().fg(theme::ROSEHIP))]);
        }
    }
    (head, lines)
}

/// How many of a terminal's lines are on screen: the command at once, the output a line at a
/// time as a terminal prints it (all of it within a second or so), and a command that has not
/// answered has only itself. `terminal` is false for a card that is not one.
fn shown_rows(head: usize, total: usize, since_result: Option<f32>, terminal: bool) -> usize {
    if !terminal {
        return total;
    }
    let Some(since) = since_result else { return head };
    let output = total.saturating_sub(head);
    let speed = (output as f32 / 1.2).max(30.0);
    head + ((since * speed) as usize).min(output)
}

/// The command line of a `run_shell` call as a person would type it: the words of the argv list,
/// those with spaces in quotes. `None` for any other action.
fn shell_command(action: &str, args: &Value) -> Option<String> {
    if action != "run_shell" {
        return None;
    }
    match args.get("command")? {
        Value::String(line) => Some(line.clone()),
        Value::Array(words) => Some(
            words
                .iter()
                .filter_map(Value::as_str)
                .map(|word| if word.is_empty() || word.contains(char::is_whitespace) { format!("'{word}'") } else { word.to_string() })
                .collect::<Vec<_>>()
                .join(" "),
        ),
        _ => None,
    }
}

/// How many lines of a write the card gets at most to parse: the rest is not shown.
const PREVIEW_LINES: usize = 150;

/// The diff of a write as its arguments say it will go: all of `content` is added (to a file that
/// may exist: the real diff replaces this one when the result comes), and an exact fragment is
/// swapped for another. `None` for any other action.
fn preview_from_args(action: &str, args: &Value) -> Option<Preview> {
    let word = |key: &str| args.get(key).and_then(Value::as_str);
    let path = word("path")?.to_string();
    let (old, new) = match action {
        "write_file" | "append_file" => ("", word("content")?),
        "replace_in_file" => (word("old_text")?, word("new_text")?),
        _ => return None,
    };
    let (removed, added) = (old.lines().count(), new.lines().count());
    let mut patch = format!("@@ -{},{removed} +1,{added} @@\n", if removed == 0 { 0 } else { 1 });
    patch.extend(old.lines().take(PREVIEW_LINES).map(|line| format!("-{line}\n")));
    patch.extend(new.lines().take(PREVIEW_LINES).map(|line| format!("+{line}\n")));
    Some(Preview { path, patch, added: added as u32, removed: removed as u32 })
}

/// How many columns the words of the thoughts card wrap to.
fn thought_width(card: Rect) -> usize {
    // The card's padding, and the rule with its space.
    card.width.saturating_sub(2 * MODULE_PAD_X + 2).max(4) as usize
}

/// The thought as lines of the card: each line of it wrapped to the width.
fn thought_lines(thought: &str, width: usize) -> Vec<String> {
    thought
        .lines()
        .flat_map(|line| {
            let spans: text::Line = vec![(line.to_string(), Style::default())];
            text::wrap(&spans, width).into_iter().map(|row| row.iter().map(|(word, _)| word.as_str()).collect::<String>())
        })
        .collect()
}

/// The model's thought as a window of a few lines that scrolls as the thought grows: the newest
/// words at the bottom, the oldest fading out at the top, a thin rule down the left side. `rows`
/// is how many of them the glass is tall enough for just now.
fn draw_thought(p: &mut Painter, card: Rect, rows: usize, thought: &str) {
    let left = (card.x + MODULE_PAD_X) as i32;
    let body = left + 2;
    let lines = thought_lines(thought, thought_width(card));
    let window = modules::TALL_LINES as usize;
    let overflow = lines.len() > window;
    let shown = &lines[lines.len().saturating_sub(window)..];
    let glass = ratatui::style::Color::Rgb(theme::GLASS_TINT[0], theme::GLASS_TINT[1], theme::GLASS_TINT[2]);
    for (index, line) in shown.iter().enumerate().take(rows) {
        let y = card.y as i32 + 2 + index as i32;
        // Only a thought that has outgrown the window fades at its top: the lines that are going.
        let gone = if overflow { [0.78, 0.5, 0.22].get(index).copied().unwrap_or(0.0) } else { 0.0 };
        p.text(left, y, "│", Style::default().fg(theme::mix(theme::DUSK, glass, gone)));
        p.text(body, y, line, Style::default().fg(theme::mix(theme::MIST, glass, gone)));
    }
}

/// A module's icon, name and colour.
fn module_face(module: &Module) -> (&'static str, String, ratatui::style::Color) {
    let s = strings();
    let (icon, name, colour) = match module.kind {
        ModuleKind::Files => (theme::I_EYE, s.module_files, theme::MIST),
        ModuleKind::Search => (theme::I_SEARCH, s.module_search, theme::SYN_OPERATOR),
        ModuleKind::WebSearch => (theme::I_SEARCH, s.module_web_search, theme::LINK),
        ModuleKind::Page => (theme::I_GLOBE, s.module_page, theme::LINK),
        ModuleKind::Edit => (theme::I_EDIT, s.module_edit, theme::MOSS),
        ModuleKind::Shell => (theme::I_TERMINAL, s.module_shell, theme::SYN_TYPE),
        ModuleKind::Git => (theme::I_BRANCH, s.module_git, theme::SYN_NUMBER),
        ModuleKind::Helper => (theme::I_HELPER, s.module_helper, theme::SYN_KEYWORD),
        ModuleKind::Skill if module.title == "browser" => (theme::I_GLOBE, s.module_browser, theme::LINK),
        ModuleKind::Skill => (icons::I_TOOLS, s.module_skill, theme::SYN_FUNCTION),
        ModuleKind::Memory => (icons::I_DATABASE, s.module_memory, theme::SYN_KEYWORD),
        ModuleKind::Picture => (icons::I_PICTURE, s.module_picture, theme::SYN_NUMBER),
        ModuleKind::Plan => (icons::I_CHECKLIST, s.module_plan, theme::SYN_FUNCTION),
        ModuleKind::Timer => (icons::I_CLOCK, s.module_timer, theme::MIST),
        ModuleKind::Thoughts => (icons::I_SPARKLE, s.module_thoughts, theme::SYN_KEYWORD),
        ModuleKind::Other => (icons::I_TOOLS, s.module_other, theme::MIST),
    };
    // A skill is known by its own name, except the browser, which has a word of its own.
    let own_name = module.kind == ModuleKind::Skill && !module.title.is_empty() && module.title != "browser";
    (icon, if own_name { module.title.clone() } else { name.to_string() }, colour)
}

// ───────────────────────────── processes ─────────────────────────────

/// A child process speaking JSON lines: `events` has what it printed, and a
/// final `{"event":"gone"}` when its output ends.
struct Sidecar {
    child: Child,
    stdin: Option<ChildStdin>,
    events: Receiver<Value>,
}

impl Sidecar {
    fn spawn(name: &str, argv: &[String]) -> io::Result<Sidecar> {
        let log = std::env::temp_dir().join(format!("kvist-voice-{name}-{}.log", std::process::id()));
        let mut child = Command::new(&argv[0])
            .args(&argv[1..])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(File::create(log)?)
            .spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| io::Error::other("the process has no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| io::Error::other("the process has no stdout"))?;
        let (sender, events) = mpsc::channel();
        thread::spawn(move || {
            // Anything on stdout that is not a JSON object is not an event and is skipped.
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if let Ok(event) = serde_json::from_str::<Value>(&line)
                    && sender.send(event).is_err()
                {
                    return;
                }
            }
            // The receiver may already be gone when the screen quits first.
            let _ = sender.send(json!({"event": "gone"}));
        });
        Ok(Sidecar { child, stdin: Some(stdin), events })
    }

    fn send(&mut self, message: &Value) -> io::Result<()> {
        let stdin = self.stdin.as_mut().ok_or_else(|| io::Error::other("the process input is closed"))?;
        let mut line = serde_json::to_vec(message)?;
        line.push(b'\n');
        stdin.write_all(&line)?;
        stdin.flush()
    }
}

impl Drop for Sidecar {
    fn drop(&mut self) {
        drop(self.stdin.take());
        if let Err(error) = self.child.kill() {
            // Already exited is the usual case; anything else is worth a line.
            if error.kind() != io::ErrorKind::InvalidInput {
                eprintln!("kvist-voice: could not stop a voice process: {error}");
            }
        }
        // Reaps the child so it does not stay a zombie until the screen exits.
        let _ = self.child.wait();
    }
}

/// A command from the environment: a JSON array of strings.
fn env_command(name: &str) -> Result<Option<Vec<String>>, String> {
    let Ok(raw) = std::env::var(name) else { return Ok(None) };
    match serde_json::from_str::<Vec<String>>(&raw) {
        Ok(argv) if !argv.is_empty() && argv.iter().all(|a| !a.is_empty()) => Ok(Some(argv)),
        _ => Err(format!("{name} must be a non-empty JSON array of strings, got {raw}")),
    }
}

// ───────────────────────────── drawing ─────────────────────────────

fn state_label(state: OrbState, s: &Strings) -> &'static str {
    match state {
        OrbState::Loading => s.state_loading,
        OrbState::Waiting => s.state_waiting,
        OrbState::Listening => s.state_listening,
        OrbState::Transcribing => s.state_transcribing,
        OrbState::Thinking => s.state_thinking,
        OrbState::Working => s.state_working,
        OrbState::Narrating => s.state_narrating,
        OrbState::Speaking => s.state_speaking,
        OrbState::Approval => s.state_approval,
        OrbState::Error => s.state_error,
        OrbState::Offline => s.state_offline,
    }
}

/// The icon of a state; waiting and writing use the interface's own spinner.
fn state_icon(state: OrbState, ms: u128) -> &'static str {
    match state {
        OrbState::Loading | OrbState::Transcribing => theme::spinner(ms),
        OrbState::Waiting => icons::I_MIC,
        OrbState::Listening => icons::I_BROADCAST,
        OrbState::Thinking => icons::I_SPARKLE,
        OrbState::Working => icons::I_TOOLS,
        OrbState::Narrating => icons::I_COMMENT,
        OrbState::Speaking => icons::I_UNMUTE,
        OrbState::Approval => icons::I_QUESTION,
        OrbState::Error => theme::I_ERROR,
        OrbState::Offline => icons::I_DISCONNECT,
    }
}

fn state_colour(state: OrbState) -> ratatui::style::Color {
    match state {
        OrbState::Error | OrbState::Offline => theme::ROSEHIP,
        OrbState::Listening | OrbState::Transcribing => theme::LINK,
        OrbState::Approval | OrbState::Working => theme::SYN_TYPE,
        OrbState::Thinking => theme::SYN_KEYWORD,
        OrbState::Speaking | OrbState::Narrating => theme::MOON,
        OrbState::Loading => theme::MIST,
        OrbState::Waiting => theme::FOG,
    }
}

/// States in which something is going on: their label is lit by a travelling wave.
fn is_active(state: OrbState) -> bool {
    !matches!(state, OrbState::Waiting | OrbState::Offline | OrbState::Error)
}

/// Lines of what was heard or said that the card shows at most (the newest).
const CAPTION_LINES: usize = 3;
/// Cells between the card's edge and its text, at each side and above and below.
const CARD_PAD_X: u16 = 4;
const CARD_PAD_Y: u16 = 1;
const CARD_MIN_WIDTH: u16 = 34;
const CARD_MAX_TEXT: usize = 64;

/// What the glass card under the orb holds and where it sits.
#[derive(Debug, PartialEq)]
struct CardPlan {
    rect: Rect,
    caption: Vec<text::Line>,
    /// What the room sounded like, dim, under the caption.
    overheard: Option<text::Line>,
    /// How the last command went.
    toast: Option<text::Line>,
    notice: Option<String>,
}

/// The menu or a dialog of the core over the orb: its cells and its rows.
struct OverlayPlan {
    rect: Rect,
    rows: Vec<voice_overlay::Row>,
}

impl Voice {
    fn caption_lines(&self, width: usize) -> Vec<text::Line> {
        if self.caption.is_empty() {
            return Vec::new();
        }
        let words = Style::default().fg(if self.caption_from_person { theme::MIST } else { theme::FOG });
        let mut line: text::Line = Vec::new();
        if self.caption_from_person {
            line.push((format!("{}  ", icons::I_MIC), Style::default().fg(theme::DUSK)));
        }
        line.push((self.caption.clone(), words));
        let mut lines = text::wrap(&line, width);
        if lines.len() > CAPTION_LINES {
            // The newest words matter: keep the end and mark the cut.
            lines.drain(..lines.len() - CAPTION_LINES);
            lines[0].insert(0, ("… ".to_string(), Style::default().fg(theme::DUSK)));
        }
        lines
    }

    /// The card's contents and its rectangle below the orb.
    fn card_plan(&self, grid: Grid) -> CardPlan {
        let s = strings();
        let notice = if !self.notice.is_empty() {
            Some(self.notice.clone())
        } else if self.ears == Ears::Absent && self.demo.is_none() {
            Some(s.voice_no_stt.to_string())
        } else {
            None
        };
        let room = (grid.cols as usize).saturating_sub(2 * CARD_PAD_X as usize + 2);
        let text_width = room.min(CARD_MAX_TEXT);
        let caption = self.caption_lines(text_width);
        let overheard = self.overheard.as_ref().map(|(phrase, _)| -> text::Line {
            vec![(format!("› {}", text::fit(phrase, text_width.saturating_sub(2))), Style::default().fg(theme::DUSK))]
        });
        let toast = self.toast.as_ref().map(|(message, ok, _)| -> text::Line {
            let (icon, colour) = if *ok { (theme::I_CHECK, theme::MOSS) } else { (theme::I_ERROR, theme::ROSEHIP) };
            let message = text::fit(&message.replace('\n', " "), text_width.saturating_sub(3));
            vec![(format!("{icon}  "), Style::default().fg(colour)), (message, Style::default().fg(theme::FOG))]
        });
        let label = state_label(self.orb.state(), s);
        let widest = caption
            .iter()
            .chain(overheard.iter())
            .chain(toast.iter())
            .map(text::line_width)
            .chain([text::width(label) + 3])
            .chain(notice.iter().map(|n| text::width(n).min(text_width) + 3))
            .max()
            .unwrap_or(0) as u16;
        let width = (widest + 2 * CARD_PAD_X).clamp(CARD_MIN_WIDTH.min(grid.cols), grid.cols.saturating_sub(2).max(1));
        let content = 1 + caption.len() as u16 + overheard.is_some() as u16 + toast.is_some() as u16 + notice.is_some() as u16;
        let height = content + 2 * CARD_PAD_Y;
        let orb = orb_box(grid);
        let x = (grid.cols - width) / 2;
        let wanted = orb.row + orb.rows + 1;
        let y = wanted.min(grid.rows.saturating_sub(height + 2));
        CardPlan { rect: Rect::new(x, y, width, height), caption, overheard, toast, notice }
    }

    /// Where each module's glass is now, with the twig that reaches it. A module whose
    /// place a menu or a dialog covers is left out until that closes.
    fn module_spots(&self, grid: Grid, now: Instant) -> Vec<ModuleSpot> {
        let orb = orb_box(grid);
        let places = modules::places(grid, orb.cells());
        let covered = [self.overlay_plan(grid).map(|plan| plan.rect), self.menu_plan(grid)];
        let (cw, ch) = (grid.cw as f32, grid.ch as f32);
        // The ball's centre in the window's pixels, and half the orb's height: the unit its picture is drawn in.
        let centre = ((orb.col as f32 + orb.cols as f32 / 2.0) * cw, (orb.row as f32 + orb.rows as f32 / 2.0) * ch);
        let unit = orb.rows as f32 * ch / 2.0;
        let mut spots = Vec::new();
        for module in self.modules.shown() {
            let cells = if module.slot >= modules::TALL_SLOT { modules::tall_place(grid, orb.cells(), module.slot - modules::TALL_SLOT) } else { places.get(module.slot).copied() };
            let Some(cells) = cells else { continue };
            if covered.iter().flatten().any(|rect| rect.intersects(cells)) {
                continue;
            }
            let shape = modules::fold(module.out.value(now));
            // The thoughts card is only as tall as its words, from the top of its place down.
            let rows = if module.slot >= modules::TALL_SLOT { self.tall[module.slot - modules::TALL_SLOT].rows } else { cells.height as f32 };
            let (full_w, full_h) = (cells.width as f32 * cw, rows * ch);
            let (width, height) = (full_w * shape.width, FOLD_LINE_PX + (full_h - FOLD_LINE_PX) * shape.height);
            // Still inside the ball: nothing to see yet.
            if width < 2.0 {
                continue;
            }
            // The middle of the card goes from the ball's centre to the middle of its place.
            let place = (cells.x as f32 * cw + full_w / 2.0, cells.y as f32 * ch + full_h / 2.0);
            let middle = (centre.0 + (place.0 - centre.0) * shape.travel, centre.1 + (place.1 - centre.1) * shape.travel);
            let (x, y) = (middle.0 - width / 2.0, middle.1 - height / 2.0);
            let near = if modules::on_the_right(cells, orb.cells()) { x } else { x + width };
            let (r, g, b) = theme::rgb(status_colour(module));
            let twig = Twig {
                to: ((near - centre.0) / unit, (middle.1 - centre.1) / unit),
                colour: [r, g, b],
                grown: 1.0,
                flow: if module.status() == Status::Running { 1.0 } else { 0.0 },
            };
            let (gr, gg, gb) = theme::rgb(module_face(module).2);
            spots.push(ModuleSpot { slot: module.slot, cells, x, y, width, height, open: shape.height, lines: ((height / ch) as i32 - 3).max(0) as usize, glow: [gr, gg, gb], age: now.saturating_duration_since(module.since).as_secs_f32(), since_result: module.shell.as_ref().and_then(|shell| shell.got).map(|got| now.saturating_duration_since(got).as_secs_f32()), twig, text: shape.text });
        }
        spots
    }

    /// The words on the modules: what each one is, how far it has got, and what it is doing.
    /// They appear when the glass has all but arrived, and go before it leaves.
    fn draw_modules(&self, p: &mut Painter, spots: &[ModuleSpot], ms: u128) {
        for spot in spots.iter().filter(|spot| spot.text > 0.0) {
            let Some(module) = self.modules.shown().iter().find(|module| module.slot == spot.slot) else { continue };
            let (icon, name, colour) = module_face(module);
            let left = (spot.cells.x + MODULE_PAD_X) as i32;
            let right = (spot.cells.right() - MODULE_PAD_X) as i32;
            let y = spot.cells.y as i32 + 1;
            let (done, total) = module.progress();
            let count = if total > 1 { format!("{done}/{total} ") } else { String::new() };
            let (mark, mark_colour) = match module.status() {
                Status::Running => (theme::spinner(ms), colour),
                Status::Done => (theme::I_CHECK, theme::MOSS),
                Status::Failed => (theme::I_CLOSE, theme::ROSEHIP),
            };
            let state: text::Line = vec![(count, Style::default().fg(theme::DUSK)), (mark.to_string(), Style::default().fg(mark_colour))];
            let room = (right - left) as usize;
            let name_room = room.saturating_sub(text::line_width(&state) + 4);
            let thinking = module.kind == ModuleKind::Thoughts;
            // A write that has the tall card shows what it puts in the file, as the conversation screen does.
            let tall = module.slot >= modules::TALL_SLOT;
            if let (true, ModuleKind::Edit, Some(preview)) = (tall, module.kind, &module.preview) {
                self.draw_edit(p, spot, module, preview, ms);
                text::fade(p.buf, spot.cells, 1.0 - spot.text, theme::NIGHT);
                continue;
            }
            if let (true, ModuleKind::Shell, Some(shell)) = (tall, module.kind, &module.shell) {
                self.draw_shell(p, spot, module, shell, ms);
                text::fade(p.buf, spot.cells, 1.0 - spot.text, theme::NIGHT);
                continue;
            }
            let name = text::fit(&name, name_room);
            let mut head: text::Line = vec![(format!("{icon}  "), Style::default().fg(colour))];
            // The words "Thoughts" shimmer while the model thinks, as the word on the card under the orb does.
            if thinking && module.status() == Status::Running {
                head.extend(text::shimmer(&name, ms, theme::MIST, theme::FOG));
            } else {
                head.push((name, Style::default().fg(theme::FOG).add_modifier(Modifier::BOLD)));
            }
            p.line(left, y, &head);
            p.line_right(right, y, &state);
            let detail = module.detail.replace('\n', " ");
            if thinking {
                draw_thought(p, spot.cells, spot.lines, &module.detail);
            } else {
                p.text(left, y + 1, &text::fit(&detail, room), Style::default().fg(theme::MIST));
            }
            text::fade(p.buf, spot.cells, 1.0 - spot.text, theme::NIGHT);
        }
    }

    /// The card of a write: the file, how many lines it adds and removes, and the diff, which
    /// scrolls through if it is longer than the window (all of it within about two seconds).
    fn draw_edit(&self, p: &mut Painter, spot: &ModuleSpot, module: &Module, preview: &Preview, ms: u128) {
        let card = spot.cells;
        let (icon, name, colour) = module_face(module);
        let left = (card.x + MODULE_PAD_X) as i32;
        let right = (card.right() - MODULE_PAD_X) as i32;
        let y = card.y as i32 + 1;
        let (done, total) = module.progress();
        let count = if total > 1 { format!("{done}/{total} ") } else { String::new() };
        let (mark, mark_colour) = match module.status() {
            Status::Running => (theme::spinner(ms), colour),
            Status::Done => (theme::I_CHECK, theme::MOSS),
            Status::Failed => (theme::I_CLOSE, theme::ROSEHIP),
        };
        let state: text::Line = vec![
            (count, Style::default().fg(theme::DUSK)),
            (format!("+{} ", preview.added), Style::default().fg(theme::MOSS)),
            (format!("−{} ", preview.removed), Style::default().fg(theme::ROSEHIP)),
            (mark.to_string(), Style::default().fg(mark_colour)),
        ];
        // The file's name, or its end when the path is long: the name is what matters.
        let room = (right - left) as usize;
        let file = if preview.path.is_empty() { name } else { preview.path.clone() };
        let file_room = room.saturating_sub(text::line_width(&state) + 5);
        p.line(left, y, &vec![(format!("{icon}  "), Style::default().fg(colour)), (text::fit_end(&file, file_room), Style::default().fg(theme::FOG).add_modifier(Modifier::BOLD))]);
        p.line_right(right, y, &state);
        let window = modules::TALL_LINES as usize;
        self.with_tall_lines(modules::TALL_EDITS, module, card, |_, lines| {
            let extra = lines.len().saturating_sub(window);
            let speed = (extra as f32 / 2.0).max(4.0);
            let from = (((spot.age - 0.5).max(0.0) * speed) as usize).min(extra);
            for (index, line) in lines.iter().skip(from).take(window.min(spot.lines)).enumerate() {
                p.line(left, card.y as i32 + 2 + index as i32, line);
            }
        });
    }

    /// The card of a command, as a terminal would show it: the prompt and the command, then what it
    /// printed, line by line as it comes; the newest lines at the bottom.
    fn draw_shell(&self, p: &mut Painter, spot: &ModuleSpot, module: &Module, shell: &Shell, ms: u128) {
        let card = spot.cells;
        let (icon, name, colour) = module_face(module);
        let left = (card.x + MODULE_PAD_X) as i32;
        let right = (card.right() - MODULE_PAD_X) as i32;
        let y = card.y as i32 + 1;
        let state: text::Line = match (module.status(), shell.exit) {
            (Status::Running, _) => vec![(theme::spinner(ms).to_string(), Style::default().fg(colour))],
            (_, Some(code)) if code != 0 => vec![(format!("exit {code} "), Style::default().fg(theme::ROSEHIP)), (theme::I_CLOSE.to_string(), Style::default().fg(theme::ROSEHIP))],
            (Status::Failed, _) => vec![(theme::I_CLOSE.to_string(), Style::default().fg(theme::ROSEHIP))],
            _ => vec![(theme::I_CHECK.to_string(), Style::default().fg(theme::MOSS))],
        };
        // What the command is for, beside the name, when there is room.
        let room = (right - left) as usize;
        let purpose_room = room.saturating_sub(text::width(&name) + text::line_width(&state) + 8);
        let mut head: text::Line = vec![(format!("{icon}  "), Style::default().fg(colour)), (name, Style::default().fg(theme::FOG).add_modifier(Modifier::BOLD))];
        if purpose_room >= 8 && !module.detail.is_empty() && shell.output.is_none() {
            head.push((format!("  {}", text::fit(&module.detail.replace('\n', " "), purpose_room)), Style::default().fg(theme::DUSK)));
        }
        p.line(left, y, &head);
        p.line_right(right, y, &state);
        let window = modules::TALL_LINES as usize;
        self.with_tall_lines(modules::TALL_SHELL, module, card, |command_rows, lines| {
            let revealed = shown_rows(command_rows, lines.len(), spot.since_result, true);
            // The command stays at the top; the output scrolls under it, its newest lines at the bottom.
            let room = window.saturating_sub(command_rows).max(1);
            let from = revealed.saturating_sub(room).max(command_rows);
            let rows: Vec<&text::Line> = lines[..command_rows.min(revealed)].iter().chain(lines[from..revealed].iter()).collect();
            for (index, line) in rows.iter().enumerate().take(spot.lines) {
                p.line(left, card.y as i32 + 2 + index as i32, line);
            }
            // A command that has not answered yet shows the cursor of a terminal waiting.
            if shell.output.is_none() && (ms / 500) % 2 == 0 && rows.len() < spot.lines.max(1) {
                p.text(left, card.y as i32 + 2 + rows.len() as i32, "▍", Style::default().fg(theme::MIST));
            }
        });
    }

    /// What the menu shows beside each item: where things stand now.
    fn menu_values(&self) -> Vec<String> {
        MENU.iter()
            .map(|item| match item.command {
                "session" => self.session.clone(),
                "models" => self.model.clone(),
                "provider" => self.provider.clone(),
                _ => String::new(),
            })
            .collect()
    }

    /// The menu or a dialog, at the bottom above the footer, or `None` when neither is open.
    fn overlay_plan(&self, grid: Grid) -> Option<OverlayPlan> {
        let s = strings();
        let width = (grid.cols as usize).saturating_sub(6).clamp(44, 84).min(grid.cols as usize);
        let inner = width.saturating_sub(2 * CARD_PAD_X as usize);
        let rows = match (&self.ask, self.menu, &self.input) {
            (Some(ask), _, _) => voice_overlay::ask_rows(ask, s, inner),
            (None, Some(selected), _) => voice_overlay::menu_rows(selected, &self.menu_values(), s, inner),
            (None, None, Some(line)) => voice_overlay::input_rows(line, s, inner),
            (None, None, None) => return None,
        };
        let height = (rows.len() as u16 + 2 * CARD_PAD_Y).min(grid.rows.saturating_sub(3));
        let x = (grid.cols - width as u16) / 2;
        let y = grid.rows.saturating_sub(height + 2).max(1);
        Some(OverlayPlan { rect: Rect::new(x, y, width as u16, height), rows })
    }

    /// The slash list's card above the input line, or `None` when no list is open or there is no room for a row.
    fn menu_plan(&self, grid: Grid) -> Option<Rect> {
        let count = self.suggestions().len();
        let input = self.overlay_plan(grid)?.rect;
        let rows = count.min(SLASH_ROWS).min(input.y.saturating_sub(4) as usize);
        if rows == 0 {
            return None;
        }
        let height = rows as u16 + 2;
        Some(Rect::new(input.x, input.y - 1 - height, input.width, height))
    }

    /// The slash list over the input: the commands the typed prefix leaves, the chosen one lit.
    /// Its text fades in as the glass behind it frosts.
    fn draw_menu(&self, p: &mut Painter, card: Rect, now: Instant) {
        let shown = self.suggestions();
        let rows = card.height as usize - 2;
        let chosen = self.chosen();
        let first = slash::window_start(shown.len(), chosen, rows);
        let russian = crate::strings::russian();
        let x = card.x as i32 + 3;
        let right = card.right() as i32 - 3;
        let hint_x = x + slash::name_width() as i32 + 4;
        for (index, command) in shown.iter().enumerate().skip(first).take(rows) {
            let y = card.y as i32 + 1 + (index - first) as i32;
            let picked = index == chosen;
            if picked {
                p.fill(card.x as i32 + 1, y, card.width as i32 - 2, theme::MOON);
            }
            let name = Style::default()
                .fg(if picked { theme::INK } else { theme::FOG })
                .add_modifier(if picked { Modifier::BOLD } else { Modifier::empty() });
            p.text(x, y, &format!("/{}", command.name), name);
            let room = (right - hint_x).max(0) as usize;
            if room >= 8 {
                let hint = Style::default().fg(if picked { theme::INK_SOFT } else { theme::DUSK });
                p.text(hint_x, y, &text::fit(command.hint(russian), room), hint);
            }
        }
        if shown.len() > rows {
            let counter = format!("{}/{}", chosen + 1, shown.len());
            p.text(right - counter.len() as i32, card.bottom() as i32 - 1, &counter, Style::default().fg(theme::DUSK));
        }
        let open = self.menu_in.value(now).clamp(0.0, 1.0);
        text::fade(p.buf, card, 1.0 - open, theme::NIGHT);
    }

    /// The text of the window title: `Kvist | <title>` once the session has one.
    fn window_title(&self) -> String {
        let clean: String = self.title.chars().filter(|c| !c.is_control()).collect();
        let clean = clean.trim();
        if clean.is_empty() { "Kvist".to_string() } else { format!("Kvist | {}", clean.chars().take(80).collect::<String>()) }
    }

    fn draw(&self, frame: &mut ratatui::Frame, grid: Grid, now: Instant) {
        let s = strings();
        let area = frame.area();
        let ms = now.duration_since(self.started).as_millis();
        let mut p = Painter { buf: frame.buffer_mut(), clip: area };

        self.draw_modules(&mut p, &self.module_spots(grid, now), ms);
        match self.overlay_plan(grid) {
            Some(plan) => Self::draw_overlay(&mut p, &plan),
            None => self.draw_card(&mut p, grid, ms),
        }
        if let Some(card) = self.menu_plan(grid) {
            self.draw_menu(&mut p, card, now);
        }

        // The footer, laid out like the conversation screen's: where it runs, how full it is, how to leave.
        let bottom = area.height.saturating_sub(1) as i32;
        let dim = Style::default().fg(theme::DUSK);
        let mut left: text::Line = vec![(format!("{} ", theme::I_CHIP), Style::default().fg(theme::MIST))];
        let place = if self.provider.is_empty() { self.model.clone() } else { format!("{} · {}", self.provider, self.model) };
        left.push((place, Style::default().fg(theme::MIST)));
        if !self.session.is_empty() {
            left.push((format!("   {} ", icons::I_COMMENT), dim));
            left.push((text::fit(&self.session, 28), dim));
        }
        if self.mic_off {
            left.push((format!("   {} {}", icons::I_MIC, s.voice_mic_off_short), Style::default().fg(theme::ROSEHIP)));
        }
        p.line(3, bottom, &left);
        if let Some(fraction) = self.context {
            const CELLS: usize = 12;
            let filled = (fraction * CELLS as f32).round() as usize;
            let bar: text::Line = vec![
                (format!("{} {}  ", icons::I_DATABASE, s.voice_context), dim),
                ("━".repeat(filled), Style::default().fg(theme::MOON)),
                ("━".repeat(CELLS - filled), Style::default().fg(theme::INK_SOFT)),
                (format!("  {:.0}%", fraction * 100.0), dim),
            ];
            // Right-aligned on the row above the key hints, where nothing else is drawn.
            p.line_right(area.width as i32 - 3, bottom - 1, &bar);
        }
        let hint = if self.demo.is_some() { format!("{} · {}", s.voice_demo, s.voice_hint_demo) } else { s.voice_hint.to_string() };
        p.line_right(area.width as i32 - 3, bottom, &vec![(hint, dim)]);
    }

    /// The card under the orb: what it is doing, the words, what was overheard, a result, a problem.
    fn draw_card(&self, p: &mut Painter, grid: Grid, ms: u128) {
        let s = strings();
        let state = self.orb.state();
        let colour = state_colour(state);
        let plan = self.card_plan(grid);
        let centre = plan.rect.x as i32 + plan.rect.width as i32 / 2;
        let mut y = plan.rect.y as i32 + CARD_PAD_Y as i32;
        let label = state_label(state, s);
        // The icon breathes; the label is lit by a travelling wave while something is going on.
        let breath = 0.5 + 0.5 * (ms as f32 * 0.004).sin();
        let icon = Style::default().fg(theme::mix(colour, theme::NIGHT, if is_active(state) { 0.40 * (1.0 - breath) } else { 0.0 }));
        let mut head: text::Line = vec![(format!("{}  ", state_icon(state, ms)), icon)];
        if is_active(state) {
            head.extend(text::shimmer(label, ms, colour, theme::MOON));
        } else {
            head.push((label.to_string(), Style::default().fg(colour)));
        }
        for part in &mut head {
            part.1 = part.1.add_modifier(Modifier::BOLD);
        }
        p.line(centre - text::line_width(&head) as i32 / 2, y, &head);
        y += 1;
        for line in plan.caption.iter().chain(plan.overheard.iter()).chain(plan.toast.iter()) {
            p.line(centre - text::line_width(line) as i32 / 2, y, line);
            y += 1;
        }
        if let Some(notice) = &plan.notice {
            let body = text::fit(notice, plan.rect.width.saturating_sub(2 * CARD_PAD_X + 3) as usize);
            let line: text::Line = vec![
                (format!("{}  ", theme::I_ERROR), Style::default().fg(theme::ROSEHIP)),
                (body, Style::default().fg(theme::MIST)),
            ];
            p.line(centre - text::line_width(&line) as i32 / 2, y, &line);
        }
    }

    fn draw_overlay(p: &mut Painter, plan: &OverlayPlan) {
        let left = plan.rect.x as i32 + CARD_PAD_X as i32;
        let top = plan.rect.y as i32 + CARD_PAD_Y as i32;
        for (y, row) in (top..).zip(&plan.rows) {
            if let Some(background) = row.fill {
                // A highlighted row covers the glass from edge to edge of the text area.
                p.fill(plan.rect.x as i32 + 2, y, plan.rect.width as i32 - 4, background);
            }
            p.line(left, y, &row.line);
        }
    }
}

/// The photograph scaled to cover the window, slightly darkened, as RGB.
fn backdrop_rgb(width: u32, height: u32) -> Result<RgbImage, image::ImageError> {
    let photo = image::load_from_memory(BACKDROP)?;
    let w = width.min(BACKDROP_MAX_WIDTH);
    let h = (height as f32 * w as f32 / width as f32) as u32;
    let scale = (w as f32 / photo.width() as f32).max(h as f32 / photo.height() as f32);
    let scaled = photo.resize_exact(
        (photo.width() as f32 * scale).ceil() as u32,
        (photo.height() as f32 * scale).ceil() as u32,
        FilterType::Triangle,
    );
    let (x, y) = ((scaled.width() - w) / 2, (scaled.height() - h) / 2);
    let mut image = scaled.crop_imm(x, y, w, h).to_rgb8();
    for byte in image.iter_mut() {
        *byte = (*byte as f32 * 0.82) as u8;
    }
    Ok(image)
}

/// The patch of the photograph round a point, for the ball's glass to bend: `reach` pixels
/// each way from `centre`. Past the photograph's edge its last pixels repeat.
fn scenery_patch(image: &RgbImage, centre: (f32, f32), reach: f32) -> Scenery {
    let side = SCENERY_SIDE.min((reach * 2.0).ceil() as u32).max(2);
    let (last_x, last_y) = (image.width() as f32 - 1.0, image.height() as f32 - 1.0);
    let mut bytes = Vec::with_capacity((side * side * 3) as usize);
    for row in 0..side {
        for col in 0..side {
            let across = |index: u32| ((index as f32 + 0.5) / side as f32 * 2.0 - 1.0) * reach;
            let x = (centre.0 + across(col)).clamp(0.0, last_x);
            let y = (centre.1 + across(row)).clamp(0.0, last_y);
            let (x0, y0) = (x as u32, y as u32);
            let (x1, y1) = ((x0 + 1).min(last_x as u32), (y0 + 1).min(last_y as u32));
            let (fx, fy) = (x - x0 as f32, y - y0 as f32);
            for c in 0..3 {
                let at = |px: u32, py: u32| image.get_pixel(px, py).0[c] as f32;
                let top = at(x0, y0) + (at(x1, y0) - at(x0, y0)) * fx;
                let bottom = at(x0, y1) + (at(x1, y1) - at(x0, y1)) * fx;
                bytes.push((top + (bottom - top) * fy).round() as u8);
            }
        }
    }
    Scenery::new(side as usize, side as usize, &bytes)
}

/// The scenery of the orb in a window of `grid`, cut from the photograph as the window shows it.
fn scenery_for(image: &RgbImage, grid: Grid) -> Scenery {
    let orb = orb_box(grid);
    let scale = image.width() as f32 / (grid.cols as f32 * grid.cw as f32);
    let centre = ((orb.col as f32 + orb.cols as f32 / 2.0) * grid.cw as f32, (orb.row as f32 + orb.rows as f32 / 2.0) * grid.ch as f32);
    let unit = orb.rows as f32 * grid.ch as f32 / 2.0;
    scenery_patch(image, (centre.0 * scale, centre.1 * scale), SCENERY_REACH * unit * scale)
}

/// The orb's scenery in a window of an ordinary size, for `--orb-preview`.
pub fn preview_scenery() -> Result<Scenery, image::ImageError> {
    let grid = Grid { cols: 213, rows: 54, cw: 9, ch: 20 };
    Ok(scenery_for(&backdrop_rgb(grid.cols as u32 * grid.cw, grid.rows as u32 * grid.ch)?, grid))
}

struct Scene {
    gpu: Gpu,
    backdrop: Option<u32>,
    /// The photograph round the orb, which its glass bends.
    scenery: Scenery,
    /// The glass of each module place that has been used: its cells and its picture.
    plates: Vec<Option<(PlateKey, u32)>>,
    /// The photograph blurred, which the glass cards lie over.
    frosted: Option<RgbImage>,
    /// The photograph unblurred and lightly blurred: the slash list's glass frosts from one to the other to `frosted`.
    sharp: Option<RgbImage>,
    soft: Option<RgbImage>,
    orb_image: Option<u32>,
    /// When the orb's picture was last drawn.
    orb_drawn: Instant,
    /// The cards now on screen: their cells and their pictures.
    card: Option<(Rect, u32)>,
    overlay: Option<(Rect, u32)>,
    /// The slash list: its cells, how frosted its glass is (0..=2) and its picture.
    menu: Option<(Rect, u8, u32)>,
}

/// How a kind of glass card is made and where it is placed.
struct Layer {
    slot: u32,
    z: i32,
    glass: Glass,
}

/// A frosted-glass card at `rect` in the layer's slot, made again only when its cells change; `None` removes it.
fn show_glass(
    gpu: &mut Gpu,
    frosted: Option<&RgbImage>,
    shown: &mut Option<(Rect, u32)>,
    grid: Grid,
    rect: Option<Rect>,
    layer: &Layer,
) -> io::Result<()> {
    if shown.map(|(cells, _)| cells) == rect {
        return Ok(());
    }
    let Some(rect) = rect else {
        gpu.set(layer.slot, None);
        if let Some((_, old)) = shown.take() {
            gpu.free(old);
        }
        return Ok(());
    };
    let Some(frosted) = frosted else { return Ok(()) };
    let (window_w, window_h) = (grid.cols as f32 * grid.cw as f32, grid.rows as f32 * grid.ch as f32);
    let scale = (frosted.width() as f32 / window_w, frosted.height() as f32 / window_h);
    let (x, y) = (rect.x as i64 * grid.cw as i64, rect.y as i64 * grid.ch as i64);
    let (w, h) = (rect.width as u32 * grid.cw, rect.height as u32 * grid.ch);
    let picture = backdrop::card(frosted, x, y, w, h, &layer.glass, scale);
    let id = gpu.upload_rgba(w, h, picture.as_raw())?;
    let placement = Placement { image: id, col: rect.x, row: rect.y, x_off: 0, y_off: 0, cells: Some((rect.width, rect.height)), crop: None, z: layer.z };
    gpu.set(layer.slot, Some(placement));
    if let Some((_, old)) = shown.replace((rect, id)) {
        gpu.free(old);
    }
    Ok(())
}

impl Scene {
    fn new(caps: Caps) -> Self {
        Scene {
            gpu: Gpu::new(caps.temp_dir),
            backdrop: None,
            scenery: Scenery::flat(theme::GLASS_TINT),
            plates: Vec::new(),
            frosted: None,
            sharp: None,
            soft: None,
            orb_image: None,
            orb_drawn: Instant::now(),
            card: None,
            overlay: None,
            menu: None,
        }
    }

    fn show_backdrop(&mut self, grid: Grid) -> io::Result<()> {
        let (width, height) = (grid.cols as u32 * grid.cw, grid.rows as u32 * grid.ch);
        let image = backdrop_rgb(width, height).map_err(io::Error::other)?;
        let id = self.gpu.upload_rgb(image.width(), image.height(), image.as_raw())?;
        let placement = Placement { image: id, col: 0, row: 0, x_off: 0, y_off: 0, cells: Some((grid.cols, grid.rows)), crop: None, z: Z_BACKDROP };
        self.gpu.set(BACKDROP_SLOT, Some(placement));
        self.backdrop = Some(id);
        self.scenery = scenery_for(&image, grid);
        self.frosted = Some(backdrop::blur(&image, 4, 8));
        self.soft = Some(backdrop::blur(&image, 4, 3));
        self.sharp = Some(image);
        Ok(())
    }

    /// The glass of the modules, each as its module is now: a line, a wider one, a card unfolding.
    /// Its size and place change every frame while it moves, so each frame has its own picture;
    /// once it has settled the picture of its place is the same, and only the first is made.
    fn show_modules(&mut self, grid: Grid, spots: &[ModuleSpot]) -> io::Result<()> {
        let used = spots.iter().map(|spot| spot.slot + 1).max().unwrap_or(0);
        if self.plates.len() < used {
            self.plates.resize(used, None);
        }
        for slot in 0..self.plates.len() {
            let Some(spot) = spots.iter().find(|spot| spot.slot == slot) else {
                self.gpu.set(MODULE_SLOT + slot as u32, None);
                continue;
            };
            let (x, y) = (spot.x.round().max(0.0) as i64, spot.y.round().max(0.0) as i64);
            let (w, h) = (spot.width.round().max(1.0) as u32, spot.height.round().max(1.0) as u32);
            let key = (x, y, w, h);
            let image = match self.plates[slot] {
                Some((made, image)) if made == key => image,
                _ => {
                    let Some(frosted) = self.frosted.as_ref() else { continue };
                    // Darker than the card under the orb: a module may lie over the bright horizon.
                    // The line it comes out as is a thread of light in the colour of its kind, and
                    // cools into glass as it opens.
                    let warm = (1.0 - spot.open).powf(1.6);
                    let tint = std::array::from_fn(|c| (theme::GLASS_TINT[c] as f32 + (spot.glow[c] as f32 - theme::GLASS_TINT[c] as f32) * warm).round() as u8);
                    let glass = Glass { tint, alpha: 0.74 + 0.2 * warm, radius: grid.ch as f32 * 0.6, rim: 0.18 + 0.45 * (1.0 - spot.open) };
                    let (window_w, window_h) = (grid.cols as f32 * grid.cw as f32, grid.rows as f32 * grid.ch as f32);
                    let scale = (frosted.width() as f32 / window_w, frosted.height() as f32 / window_h);
                    let picture = backdrop::card(frosted, x, y, w, h, &glass, scale);
                    let image = self.gpu.upload_rgba(w, h, picture.as_raw())?;
                    if let Some((_, old)) = self.plates[slot].replace((key, image)) {
                        self.gpu.free(old);
                    }
                    image
                }
            };
            // Pixel for pixel: the picture is made the size it is shown at.
            self.gpu.set(MODULE_SLOT + slot as u32, Some(Placement::at_px(image, spot.x, spot.y, grid.cw, grid.ch, None, Z_MODULE)));
        }
        Ok(())
    }

    fn show_orb(&mut self, voice: &Voice, grid: Grid, clock: f32, spots: &[ModuleSpot], now: Instant) -> io::Result<()> {
        // A waiting orb only breathes: a new picture of it a few times a second is as smooth
        // to the eye as thirty, and it waits for most of the day.
        let calm = voice.orb.calm() && spots.is_empty();
        if calm && self.orb_image.is_some() && now.saturating_duration_since(self.orb_drawn) < CALM_ORB_PERIOD {
            return Ok(());
        }
        self.orb_drawn = now;
        let spot = orb_box(grid);
        let twigs: Vec<Twig> = spots.iter().map(|module| module.twig).collect();
        let picture = voice.orb.render(clock, spot.width as usize, spot.height as usize, &self.scenery, &twigs);
        let id = self.gpu.upload_rgba(spot.width, spot.height, &picture)?;
        let placement = Placement {
            image: id,
            col: spot.col,
            row: spot.row,
            x_off: 0,
            y_off: 0,
            cells: Some((spot.cols, spot.rows)),
            crop: None,
            z: Z_ORB,
        };
        self.gpu.set(ORB_SLOT, Some(placement));
        if let Some(old) = self.orb_image.replace(id) {
            self.gpu.free(old);
        }
        Ok(())
    }

    /// The card under the orb, shown while no menu or dialog covers the place.
    fn show_card(&mut self, grid: Grid, rect: Option<Rect>) -> io::Result<()> {
        let glass = Glass { tint: theme::GLASS_TINT, alpha: 0.55, radius: grid.ch as f32 * 0.6, rim: 0.10 };
        show_glass(&mut self.gpu, self.frosted.as_ref(), &mut self.card, grid, rect, &Layer { slot: CARD_SLOT, z: Z_CARD, glass })
    }

    /// The menu or a dialog: lighter and more opaque than the card, like the conversation screen's dialogs.
    fn show_overlay(&mut self, grid: Grid, rect: Option<Rect>) -> io::Result<()> {
        let glass = Glass { tint: [0x26, 0x31, 0x4A], alpha: 0.80, radius: grid.ch as f32 * 0.6, rim: 0.24 };
        show_glass(&mut self.gpu, self.frosted.as_ref(), &mut self.overlay, grid, rect, &Layer { slot: OVERLAY_SLOT, z: Z_DIALOG, glass })
    }

    fn reset(&mut self) {
        self.gpu.reset();
        self.backdrop = None;
        self.frosted = None;
        self.sharp = None;
        self.soft = None;
        self.orb_image = None;
        self.plates.clear();
        self.card = None;
        self.overlay = None;
        self.menu = None;
    }

    /// The slash list's glass: clear at first, frosting as it opens (`open` 0..=1), and sliding up
    /// into place by under a row. A new frost stage makes a new picture; the slide only moves it.
    fn show_menu(&mut self, grid: Grid, rect: Option<Rect>, open: f32) -> io::Result<()> {
        let Some(rect) = rect else {
            self.gpu.set(MENU_SLOT, None);
            if let Some((_, _, old)) = self.menu.take() {
                self.gpu.free(old);
            }
            return Ok(());
        };
        let stage: u8 = if open < 0.34 { 0 } else if open < 0.67 { 1 } else { 2 };
        let source = match stage {
            0 => self.sharp.as_ref(),
            1 => self.soft.as_ref(),
            _ => self.frosted.as_ref(),
        };
        let Some(source) = source else { return Ok(()) };
        let id = match self.menu {
            Some((cells, shown, id)) if cells == rect && shown == stage => id,
            _ => {
                let glass = Glass { tint: [0x14, 0x1E, 0x34], alpha: 0.74, radius: grid.ch as f32 * 0.6, rim: 0.18 };
                let (window_w, window_h) = (grid.cols as f32 * grid.cw as f32, grid.rows as f32 * grid.ch as f32);
                let scale = (source.width() as f32 / window_w, source.height() as f32 / window_h);
                let (x, y) = (rect.x as i64 * grid.cw as i64, rect.y as i64 * grid.ch as i64);
                let (w, h) = (rect.width as u32 * grid.cw, rect.height as u32 * grid.ch);
                let picture = backdrop::card(source, x, y, w, h, &glass, scale);
                let id = self.gpu.upload_rgba(w, h, picture.as_raw())?;
                if let Some((_, _, old)) = self.menu.replace((rect, stage, id)) {
                    self.gpu.free(old);
                }
                id
            }
        };
        let slide = ((1.0 - open) * 0.8 * grid.ch as f32) as u32;
        let placement = Placement { image: id, col: rect.x, row: rect.y, x_off: 0, y_off: slide, cells: Some((rect.width, rect.height)), crop: None, z: Z_DIALOG };
        self.gpu.set(MENU_SLOT, Some(placement));
        Ok(())
    }
}

// ───────────────────────────── the screen ─────────────────────────────

/// Runs the voice screen; the result is the exit status.
pub fn run(caps: Caps, fps: u32, workspace: &Path, demo: bool, keep_session: bool) -> io::Result<u8> {
    // Fail on a bad command before the terminal is taken over.
    let ears_cmd = env_command("KVIST_VOICE_STT_CMD").map_err(io::Error::other)?;
    let voice_cmd = env_command("KVIST_VOICE_TTS_CMD").map_err(io::Error::other)?;
    crate::term::enter()?;
    let result = run_loop(caps, fps, workspace, demo, keep_session, ears_cmd, voice_cmd);
    let restored = crate::term::leave();
    let code = result?;
    restored?;
    // Switching interfaces keeps the terminal busy; only a real exit says goodbye.
    if code != SWITCH_FRONTEND
        && !demo
        && let Ok(core) = core::command(std::env::var("KVIST_CORE_CMD").ok().as_deref())
    {
        crate::farewell::print(&core::session_command_for(&core), workspace);
    }
    Ok(code)
}

fn run_loop(
    caps: Caps,
    fps: u32,
    workspace: &Path,
    demo: bool,
    keep_session: bool,
    ears_cmd: Option<Vec<String>>,
    voice_cmd: Option<Vec<String>>,
) -> io::Result<u8> {
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    terminal.clear()?;
    // The core reads the mode from its environment, and so does a core rebuilt by /provider.
    // SAFETY: nothing else runs yet; the reader threads start with the child processes below.
    unsafe { std::env::set_var("AGENT_VOICE_MODE", "1") };
    let started = Instant::now();
    let mut voice = Voice::new(demo, started);
    voice.fresh_session = !keep_session;
    let mut grid = crate::grid_now()?;
    let mut scene = Scene::new(caps);
    scene.show_backdrop(grid)?;

    let mut core: Option<Core> = None;
    let mut ears: Option<Sidecar> = None;
    let mut speaker: Option<Sidecar> = None;
    if !demo {
        match core::command(std::env::var("KVIST_CORE_CMD").ok().as_deref())
            .and_then(|argv| Core::spawn(&argv, workspace).map_err(|e| format!("{}: {e}", argv.join(" "))))
        {
            Ok(started_core) => core = Some(started_core),
            Err(reason) => {
                voice.core_alive = false;
                voice.notice = reason;
            }
        }
        if let Some(argv) = &ears_cmd {
            match Sidecar::spawn("ears", argv) {
                Ok(sidecar) => {
                    ears = Some(sidecar);
                    voice.ears = Ears::Loading;
                }
                Err(error) => voice.notice = format!("{}: {error}", argv.join(" ")),
            }
        }
        if let Some(argv) = &voice_cmd {
            match Sidecar::spawn("voice", argv) {
                Ok(sidecar) => {
                    speaker = Some(sidecar);
                    voice.has_voice = true;
                }
                Err(error) => voice.notice = format!("{}: {error}", argv.join(" ")),
            }
        }
    }

    let frame_rate = fps.min(ORB_FPS).max(*anim::FPS_RANGE.start());
    let period = Duration::from_secs_f64(1.0 / frame_rate as f64);
    let mut due = Instant::now();
    let mut last = Instant::now();
    let mut shown_title = String::from("Kvist");
    let mut out = io::stdout();

    while !voice.quit {
        let now = Instant::now();
        if let Some(core) = &core {
            while let Ok(message) = core.messages.try_recv() {
                match message {
                    Message::Event(event) => voice.on_core(&event, now),
                    Message::Gone(reason) => {
                        voice.core_alive = false;
                        voice.notice = format!("{}: {reason}", strings().core_gone);
                    }
                }
            }
        }
        if let Some(sidecar) = &ears {
            while let Ok(event) = sidecar.events.try_recv() {
                voice.on_ears(&event, now);
            }
        }
        if let Some(sidecar) = &speaker {
            while let Ok(event) = sidecar.events.try_recv() {
                voice.on_voice(&event);
            }
        }
        for message in voice.take_outbox() {
            let delivered = match message {
                Out::Core(value) => core.as_mut().map(|c| c.send(&value)),
                Out::Voice(value) => speaker.as_mut().map(|s| s.send(&value)),
                Out::Ears(value) => ears.as_mut().map(|s| s.send(&value)),
            };
            if let Some(Err(error)) = delivered {
                voice.notice = error.to_string();
            }
        }

        let dt = now.duration_since(last).as_secs_f32().min(0.25);
        last = now;
        let clock = now.duration_since(started).as_secs_f32();
        let orb = orb_box(grid).cells();
        let found = modules::places(grid, orb);
        voice.places = found.len();
        for tall in 0..modules::TALL_COUNT {
            voice.tall[tall].rect = modules::tall_place(grid, orb, tall);
            voice.tall[tall].covered = voice.tall[tall]
                .rect
                .map(|card| found.iter().enumerate().filter(|(_, place)| place.intersects(card)).map(|(slot, _)| slot).collect());
        }
        voice.tick(now, dt, clock);
        terminal.draw(|frame| voice.draw(frame, grid, now))?;
        let title = voice.window_title();
        if title != shown_title {
            crate::term::set_title(&title)?;
            shown_title = title;
        }
        let overlay = voice.overlay_plan(grid).map(|plan| plan.rect);
        scene.show_overlay(grid, overlay)?;
        scene.show_menu(grid, voice.menu_plan(grid), voice.menu_in.value(now))?;
        scene.show_card(grid, overlay.is_none().then(|| voice.card_plan(grid).rect))?;
        let spots = voice.module_spots(grid, now);
        scene.show_modules(grid, &spots)?;
        scene.show_orb(&voice, grid, clock, &spots, now)?;
        scene.gpu.flush(&mut out)?;

        due = anim::next_frame(due, now, period);
        while let Some(left) = due.checked_duration_since(Instant::now()).filter(|d| !d.is_zero()) {
            if !event::poll(left)? {
                break;
            }
            while event::poll(Duration::ZERO)? {
                match event::read()? {
                    Event::Resize(..) => {
                        let resized = crate::grid_now()?;
                        if resized != grid {
                            grid = resized;
                            scene.reset();
                            terminal.clear()?;
                            scene.show_backdrop(grid)?;
                        }
                    }
                    Event::Key(key) => voice.key(key, Instant::now()),
                    Event::Paste(text) => voice.paste(&text),
                    _ => {}
                }
            }
            if voice.quit {
                break;
            }
        }
    }
    Ok(voice.exit_code)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn voice() -> Voice {
        let mut voice = Voice::new(false, Instant::now());
        voice.core_ready = true;
        voice
    }

    fn confirm(id: &str) -> Value {
        json!({"type": "input_request", "request_id": id, "kind": "confirm", "title": "Send this?", "body": "to the client"})
    }

    fn command_choice(id: &str) -> Value {
        json!({"type": "input_request", "request_id": id, "kind": "choice", "title": "Run it?", "options": [
            {"value": "yes", "label": "Yes"}, {"value": "always", "label": "Always"}, {"value": "no", "label": "No"}]})
    }

    #[test]
    fn a_quiet_screen_waits_for_the_wake_word() {
        let now = Instant::now();
        let mut voice = voice();
        voice.ears = Ears::Idle;
        assert_eq!(voice.resolve(now), OrbState::Waiting);
        voice.core_ready = false;
        assert_eq!(voice.resolve(now), OrbState::Loading);
    }

    #[test]
    fn the_states_follow_the_priorities() {
        let now = Instant::now();
        let mut voice = voice();
        voice.ears = Ears::Idle;
        voice.turn = Turn::Working;
        assert_eq!(voice.resolve(now), OrbState::Working);
        voice.pretend_until = Some(now + Duration::from_secs(5));
        voice.voice_kind = Kind::Narration;
        assert_eq!(voice.resolve(now), OrbState::Narrating);
        voice.voice_kind = Kind::Answer;
        assert_eq!(voice.resolve(now), OrbState::Speaking);
        voice.ask = Ask::from_request(&confirm("r"));
        assert_eq!(voice.resolve(now), OrbState::Approval);
        voice.ears = Ears::Listening;
        assert_eq!(voice.resolve(now), OrbState::Listening);
        voice.error_until = Some(now + Duration::from_secs(1));
        assert_eq!(voice.resolve(now), OrbState::Error);
        voice.core_alive = false;
        assert_eq!(voice.resolve(now), OrbState::Offline);
    }

    #[test]
    fn the_error_state_ends_by_itself() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "error", "message": "boom"}), now);
        assert_eq!(voice.resolve(now), OrbState::Error);
        assert_eq!(voice.caption, "boom");
        assert_ne!(voice.resolve(now + Duration::from_secs(ERROR_SECONDS + 1)), OrbState::Error);
    }

    #[test]
    fn what_the_person_says_becomes_a_goal_and_cuts_the_voice_off() {
        let now = Instant::now();
        let mut voice = voice();
        voice.has_voice = true;
        voice.on_ears(&json!({"event": "state", "state": "listening"}), now);
        voice.on_ears(&json!({"event": "text", "text": " открой ютуб "}), now);
        let out = voice.take_outbox();
        assert!(out.contains(&Out::Voice(json!({"type": "stop"}))));
        assert!(out.contains(&Out::Core(json!({"type": "goal", "text": "открой ютуб"}))));
        assert_eq!(voice.turn, Turn::Thinking);
        assert!(voice.caption_from_person && voice.caption.ends_with("открой ютуб"));
    }

    #[test]
    fn a_final_the_core_marks_shown_is_not_spoken_again() {
        let now = Instant::now();
        let mut voice = voice();
        voice.has_voice = true;
        voice.on_core(&json!({"type": "final", "message": "Готово.", "shown": true}), now);
        assert!(voice.take_outbox().is_empty());
    }

    #[test]
    fn a_final_answer_is_spoken_once() {
        let now = Instant::now();
        let mut voice = voice();
        voice.has_voice = true;
        let event = json!({"type": "final", "message": "Готово. **Открыла** YouTube."});
        voice.on_core(&event, now);
        voice.on_core(&event, now);
        let spoken: Vec<Out> = voice.take_outbox();
        assert_eq!(spoken.len(), 1);
        match &spoken[0] {
            Out::Voice(v) => {
                assert_eq!(v["type"], "speak");
                assert_eq!(v["kind"], "answer");
                assert_eq!(v["text"], "Готово. Открыла YouTube.");
            }
            other => panic!("unexpected {other:?}"),
        }
    }

    #[test]
    fn without_a_voice_the_orb_speaks_for_as_long_as_the_words_take() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "final", "message": "Готово, создала файл с тремя строками"}), now);
        assert_eq!(voice.resolve(now), OrbState::Speaking);
        assert!(voice.take_outbox().is_empty());
        assert_ne!(voice.resolve(now + Duration::from_secs(13)), OrbState::Speaking);
    }

    #[test]
    fn only_replies_are_spoken_not_notices_or_skill_messages() {
        let now = Instant::now();
        let mut voice = voice();
        voice.has_voice = true;
        for (source, spoken) in [("reply", true), ("", true), ("notice", false), ("skill", false)] {
            let mut event = json!({"type": "message", "markdown": "Kvist закрыла процессы задачи: browser."});
            if !source.is_empty() {
                event["source"] = json!(source);
            }
            voice.on_core(&event, now);
            assert_eq!(!voice.take_outbox().is_empty(), spoken, "source {source:?}");
            assert_eq!(voice.caption.is_empty(), !spoken, "source {source:?}");
            voice.caption.clear();
            voice.last_spoken.clear();
        }
    }

    #[test]
    fn after_an_answer_is_spoken_the_ears_are_told_the_person_may_talk_on() {
        let now = Instant::now();
        let mut voice = voice();
        voice.has_voice = true;
        voice.ears = Ears::Idle;
        voice.on_core(&json!({"type": "final", "message": "Открыла не ту страницу."}), now);
        voice.take_outbox();
        // Nothing yet: the voice has not begun, and the ears must not wait out its speech.
        voice.tick(now, 0.016, 0.0);
        assert!(voice.take_outbox().iter().all(|out| !matches!(out, Out::Ears(v) if v["type"] == "converse")));
        voice.on_voice(&json!({"event": "speaking"}));
        voice.take_outbox();
        voice.tick(now, 0.016, 0.0);
        assert!(voice.take_outbox().is_empty(), "still speaking");
        voice.on_voice(&json!({"event": "idle"}));
        voice.take_outbox();
        voice.tick(now, 0.016, 0.0);
        assert_eq!(
            voice.take_outbox(),
            vec![Out::Ears(json!({"type": "converse", "seconds": CONVERSE_SECONDS, "min_words": CONVERSE_MIN_WORDS}))]
        );
        voice.tick(now, 0.016, 0.0);
        assert!(voice.take_outbox().is_empty(), "once");
    }

    #[test]
    fn without_a_voice_the_conversation_opens_when_the_words_would_have_ended() {
        let now = Instant::now();
        let mut voice = voice();
        voice.ears = Ears::Idle;
        voice.on_core(&json!({"type": "final", "message": "Готово."}), now);
        voice.tick(now, 0.016, 0.0);
        assert!(voice.take_outbox().is_empty());
        voice.tick(now + Duration::from_secs(3), 0.016, 0.0);
        assert!(matches!(voice.take_outbox().as_slice(), [Out::Ears(v)] if v["type"] == "converse"));
    }

    #[test]
    fn a_question_of_the_agent_opens_a_conversation_where_one_word_answers() {
        let now = Instant::now();
        let mut voice = voice();
        voice.ears = Ears::Idle;
        voice.turn = Turn::Working;
        voice.on_core(&confirm("c"), now);
        voice.tick(now, 0.016, 0.0);
        assert_eq!(
            voice.take_outbox(),
            vec![Out::Ears(json!({"type": "converse", "seconds": QUESTION_SECONDS, "min_words": 1}))],
            "mid-turn, the agent waits for the person"
        );
        // A step of a command is not a question to answer by voice.
        let mut command = self::voice();
        command.ears = Ears::Idle;
        command.start_command("models", false);
        command.take_outbox();
        command.on_core(&command_choice("m"), now);
        command.tick(now, 0.016, 0.0);
        assert!(command.take_outbox().is_empty());
    }

    #[test]
    fn speaking_or_a_new_session_cancels_the_pending_conversation() {
        let now = Instant::now();
        let mut voice = voice();
        voice.ears = Ears::Idle;
        voice.on_core(&json!({"type": "final", "message": "Готово."}), now);
        voice.on_ears(&json!({"event": "text", "text": "открой ещё одну"}), now);
        voice.take_outbox();
        voice.tick(now + Duration::from_secs(5), 0.016, 0.0);
        assert!(voice.take_outbox().is_empty());
        assert!(voice.converse.is_none());
    }

    #[test]
    fn no_conversation_is_opened_without_ears() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "final", "message": "Готово."}), now);
        voice.tick(now + Duration::from_secs(5), 0.016, 0.0);
        assert!(voice.take_outbox().is_empty());
    }

    #[test]
    fn narration_is_shown_as_narrating_and_never_over_a_question() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "narration", "text": "так... смотрю тесты."}), now);
        assert_eq!(voice.resolve(now), OrbState::Narrating);
        let mut asked = self::voice();
        asked.on_core(&confirm("q"), now);
        asked.on_core(&json!({"type": "narration", "text": "ага"}), now);
        assert_eq!(asked.resolve(now), OrbState::Approval);
        assert_ne!(asked.caption, "ага");
    }

    #[test]
    fn a_narration_through_the_voice_shows_narrating_and_never_over_an_answer() {
        let now = Instant::now();
        let mut voice = voice();
        voice.has_voice = true;
        voice.on_core(&json!({"type": "narration", "text": "так... смотрю тесты."}), now);
        voice.on_voice(&json!({"event": "speaking"}));
        assert_eq!(voice.resolve(now), OrbState::Narrating);
        voice.on_core(&json!({"type": "final", "message": "Готово."}), now);
        assert_eq!(voice.resolve(now), OrbState::Speaking);
        voice.on_core(&json!({"type": "narration", "text": "ага"}), now);
        assert_eq!(voice.resolve(now), OrbState::Speaking);
    }

    #[test]
    fn what_the_room_sounds_like_is_shown_dimly_and_then_goes() {
        let now = Instant::now();
        let grid = Grid { cols: 120, rows: 36, cw: 10, ch: 20 };
        let mut voice = voice();
        voice.ears = Ears::Idle;
        let plain = voice.card_plan(grid);
        voice.on_ears(&json!({"event": "heard", "text": " Хвост, открой ютуб "}), now);
        let with = voice.card_plan(grid);
        assert_eq!(with.rect.height, plain.rect.height + 1);
        let shown: String = with.overheard.as_ref().unwrap().iter().map(|(text, _)| text.as_str()).collect();
        assert_eq!(shown, "› Хвост, открой ютуб");
        voice.tick(now + Duration::from_secs(HEARD_SECONDS + 1), 0.016, 0.0);
        assert!(voice.card_plan(grid).overheard.is_none());
        // Empty phrases say nothing, and a phrase for Kvist replaces what was overheard.
        voice.on_ears(&json!({"event": "heard", "text": "  "}), now);
        assert!(voice.overheard.is_none());
        voice.on_ears(&json!({"event": "heard", "text": "шум"}), now);
        voice.on_ears(&json!({"event": "text", "text": "открой ютуб"}), now);
        assert!(voice.overheard.is_none());
    }

    #[test]
    fn the_card_holds_the_state_the_words_and_a_problem() {
        let now = Instant::now();
        let grid = Grid { cols: 100, rows: 30, cw: 10, ch: 20 };
        let mut voice = voice();
        voice.ears = Ears::Idle;
        voice.on_core(&json!({"type": "final", "message": "Готово, создала файл с тремя строками."}), now);
        voice.notice = "Озвучка остановилась".into();
        let plan = voice.card_plan(grid);
        assert_eq!(plan.caption.len(), 1);
        assert_eq!(plan.notice.as_deref(), Some("Озвучка остановилась"));
        assert_eq!(plan.rect.height, 1 + 1 + 1 + 2 * CARD_PAD_Y);
        assert!(plan.rect.x + plan.rect.width <= grid.cols && plan.rect.y + plan.rect.height < grid.rows);
        // A long reply keeps its newest lines and marks the cut.
        voice.on_core(&json!({"type": "stream_chunk", "stream_id": "s", "text": "слово ".repeat(120), "append": false}), now);
        let long = voice.card_plan(grid);
        assert_eq!(long.caption.len(), CAPTION_LINES);
        assert!(long.caption[0][0].0.starts_with('…'));
    }

    #[test]
    fn the_ears_are_muted_while_the_voice_talks() {
        let mut voice = voice();
        voice.on_voice(&json!({"event": "speaking"}));
        voice.on_voice(&json!({"event": "speaking"}));
        voice.on_voice(&json!({"event": "idle"}));
        assert_eq!(
            voice.take_outbox(),
            vec![Out::Ears(json!({"type": "mute", "value": true})), Out::Ears(json!({"type": "mute", "value": false}))]
        );
    }

    #[test]
    fn a_spoken_yes_or_no_answers_a_confirmation() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&confirm("c1"), now);
        assert_eq!(voice.resolve(now), OrbState::Approval);
        voice.on_ears(&json!({"event": "text", "text": "Ну... отправляй"}), now);
        assert!(voice.take_outbox().contains(&Out::Core(json!({"request_id": "c1", "approved": true, "remember": false}))));
        assert!(voice.ask.is_none());

        voice.on_core(&confirm("c2"), now);
        voice.on_ears(&json!({"event": "text", "text": "нет, не надо"}), now);
        assert!(voice.take_outbox().contains(&Out::Core(json!({"request_id": "c2", "approved": false, "remember": false}))));
    }

    #[test]
    fn an_unclear_answer_keeps_the_question_open_and_is_never_a_task() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&confirm("c1"), now);
        voice.on_ears(&json!({"event": "text", "text": "хм, дай подумать"}), now);
        assert!(voice.ask.is_some());
        assert!(voice.take_outbox().is_empty(), "no goal is sent while a question is open");
    }

    #[test]
    fn a_notice_is_shown_as_a_toast_and_is_not_spoken() {
        let mut voice = voice();
        let now = Instant::now();

        voice.on_core(&json!({"type": "notice", "text": "Connecting to work:/srv/app…", "level": "info"}), now);

        assert_eq!(voice.toast.as_ref().map(|(text, ok, _)| (text.as_str(), *ok)), Some(("Connecting to work:/srv/app…", true)));
        assert!(voice.take_outbox().is_empty());
    }

    #[test]
    fn a_session_another_frontend_drives_is_watched_not_written_to() {
        let mut voice = voice();
        let now = Instant::now();
        voice.on_core(&json!({"type": "control", "role": "observer", "notice": "watching · Enter — take over"}), now);

        voice.send_goal("write a poem");
        voice.on_ask(&json!({"type": "input_request", "request_id": "r", "kind": "confirm", "title": "Run it?"}));

        assert!(voice.take_outbox().is_empty());
        assert!(voice.ask.is_none());
        assert_eq!(voice.notice, "watching · Enter — take over");
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"type": "takeover"}))]);
        voice.on_core(&json!({"type": "control", "role": "driver", "notice": ""}), now);
        voice.send_goal("write a poem");
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"type": "goal", "text": "write a poem"}))]);
    }

    #[test]
    fn keys_answer_a_list_a_confirmation_and_cancel() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&command_choice("k"), now);
        voice.key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE), now);
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"request_id": "k", "value": "always"}))]);

        voice.on_core(&command_choice("k2"), now);
        for c in "no".chars() {
            voice.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), now);
        }
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"request_id": "k2", "value": "no"}))]);

        voice.on_core(&confirm("c"), now);
        voice.key(KeyEvent::new(KeyCode::Char('y'), KeyModifiers::NONE), now);
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"request_id": "c", "approved": true, "remember": false}))]);

        voice.on_core(&command_choice("k3"), now);
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"request_id": "k3", "value": Value::Null}))]);
        assert!(!voice.quit, "Esc closes a dialog, it does not quit");
    }

    #[test]
    fn a_text_field_is_filled_by_typing_and_pasting() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "input_request", "request_id": "t", "kind": "secret", "title": "API key"}), now);
        for c in "ab".chars() {
            voice.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), now);
        }
        voice.paste("sk-cd\n");
        voice.key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE), now);
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"request_id": "t", "value": "absk-c"}))]);
        assert!(voice.ask.is_none());
    }

    #[test]
    fn speech_is_never_a_task_while_the_menu_or_a_command_is_open() {
        let now = Instant::now();
        let mut voice = voice();
        voice.menu = Some(0);
        voice.on_ears(&json!({"event": "text", "text": "открой ютуб"}), now);
        assert!(voice.take_outbox().is_empty());
        voice.menu = None;
        voice.start_command("provider", false);
        voice.take_outbox();
        voice.on_ears(&json!({"event": "text", "text": "открой ютуб"}), now);
        assert!(voice.take_outbox().is_empty());
        assert!(voice.caption.is_empty());
    }

    #[test]
    fn tab_opens_the_menu_arrows_and_letters_run_a_command_and_esc_closes_it() {
        let now = Instant::now();
        let mut voice = voice();
        voice.key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE), now);
        assert_eq!(voice.menu, Some(0));
        voice.key(KeyEvent::new(KeyCode::Down, KeyModifiers::NONE), now);
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"type": "command", "name": "session", "args": []}))]);
        assert!(voice.menu.is_none() && voice.pending == Some(Pending { silent: false }));

        voice.pending = None;
        voice.key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE), now);
        voice.key(KeyEvent::new(KeyCode::Char('K'), KeyModifiers::NONE), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"type": "command", "name": "change-api", "args": []}))]);

        voice.pending = None;
        voice.key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE), now);
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), now);
        assert!(voice.menu.is_none() && !voice.mic_off, "Esc closes the menu first");
    }

    fn type_line(voice: &mut Voice, line: &str, now: Instant) {
        for c in line.chars() {
            voice.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), now);
        }
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
    }

    #[test]
    fn a_typed_line_is_a_request_and_a_slash_line_is_a_command_with_its_words() {
        let now = Instant::now();
        let mut voice = voice();
        type_line(&mut voice, "fix the build", now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"type": "goal", "text": "fix the build"}))]);
        assert_eq!(voice.turn, Turn::Thinking);
        assert!(voice.input.is_none());

        type_line(&mut voice, "/rename my idea", now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"type": "command", "name": "rename", "args": ["my", "idea"]}))]);
        assert_eq!(voice.pending, Some(Pending { silent: false }));

        type_line(&mut voice, "/export", now);
        assert!(voice.take_outbox().is_empty(), "a command waits for the one before it");
        assert_eq!(voice.input.as_deref(), Some("/export"));
        assert!(voice.toast.as_ref().is_some_and(|(_, ok, _)| !ok));
    }

    #[test]
    fn esc_drops_a_typed_line_and_speech_is_not_a_task_while_typing() {
        let now = Instant::now();
        let mut voice = voice();
        voice.key(KeyEvent::new(KeyCode::Char('h'), KeyModifiers::NONE), now);
        voice.on_heard("open youtube", now);
        assert!(voice.take_outbox().is_empty());
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), now);
        assert!(voice.input.is_none() && !voice.mic_off);
    }

    #[test]
    fn esc_switches_the_microphone_and_ctrl_c_needs_a_second_press() {
        let now = Instant::now();
        let mut voice = voice();
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), now);
        assert!(voice.mic_off && !voice.quit);
        assert_eq!(voice.take_outbox(), vec![Out::Ears(json!({"type": "mute", "value": true}))]);
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), now);
        assert!(!voice.mic_off);
        assert_eq!(voice.take_outbox(), vec![Out::Ears(json!({"type": "mute", "value": false}))]);

        let ctrl_c = KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL);
        voice.key(ctrl_c, now);
        assert!(!voice.quit, "the first Ctrl+C only asks");
        voice.key(ctrl_c, now + Duration::from_secs(1));
        assert!(voice.quit);

        let mut late = self::voice();
        late.key(ctrl_c, now);
        late.key(ctrl_c, now + QUIT_CONFIRM + Duration::from_secs(1));
        assert!(!late.quit, "an old question is asked again");
    }

    #[test]
    fn the_microphone_stays_off_while_the_voice_talks_and_after_it_stops() {
        let now = Instant::now();
        let mut voice = voice();
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), now);
        voice.take_outbox();
        voice.set_voice_busy(true);
        voice.set_voice_busy(false);
        assert!(voice.take_outbox().is_empty(), "the person's mute outlasts the voice");
    }

    #[test]
    fn the_interface_picker_leaves_with_the_chosen_name_and_stays_on_cancel() {
        let now = Instant::now();
        let mut voice = voice();
        type_line(&mut voice, "/tui", now);
        let ask = voice.ask.as_ref().expect("the picker opens");
        assert_eq!(ask.request_id, FRONTEND_REQUEST);
        let crate::dialog::Body::Choice { choices, .. } = &ask.body else { panic!("expected choices") };
        assert_eq!(choices.len(), 6);
        assert!(choices.iter().any(|choice| choice.value == "opencode" && choice.label == "OpenCode"));
        assert_ne!(voice.resolve(now), OrbState::Approval, "the picker is not an approval");
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), now);
        assert!(voice.ask.is_none() && !voice.quit && voice.take_outbox().is_empty());

        let file = std::env::temp_dir().join(format!("kvist-voice-switch-{}", std::process::id()));
        // SAFETY: only this test reads KVIST_SWITCH_FILE.
        unsafe { std::env::set_var("KVIST_SWITCH_FILE", &file) };
        voice.key(KeyEvent::new(KeyCode::Tab, KeyModifiers::NONE), now);
        voice.key(KeyEvent::new(KeyCode::Char('i'), KeyModifiers::NONE), now);
        for c in "OpenCode".chars() {
            voice.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), now);
        }
        voice.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::NONE), now);
        assert!(voice.quit && voice.exit_code == SWITCH_FRONTEND);
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "opencode\n");
        assert!(voice.take_outbox().is_empty(), "the choice never goes to the core");

        // The core asking to start again somewhere else ends the screen the same way.
        let mut again = self::voice();
        again.on_core(&json!({"type": "command_result", "name": "machines", "ok": true, "message": "Opening work",
            "effects": {"relaunch": {"switch": "@machine\twork\t/srv/app\t", "label": "work"}}}), now);
        assert!(again.quit && again.exit_code == SWITCH_FRONTEND);
        assert_eq!(std::fs::read_to_string(&file).unwrap(), "voice\n\n@machine\twork\t/srv/app\t");
        let _ = std::fs::remove_file(&file);
    }

    #[test]
    fn a_dialog_of_a_command_is_not_an_approval_and_is_not_read_aloud() {
        let now = Instant::now();
        let mut voice = voice();
        voice.has_voice = true;
        voice.ears = Ears::Idle;
        voice.start_command("models", false);
        voice.take_outbox();
        voice.on_core(&command_choice("m"), now);
        assert_ne!(voice.resolve(now), OrbState::Approval);
        assert!(voice.take_outbox().is_empty());

        // The agent's own question is read aloud and makes the orb ask.
        let mut agent = self::voice();
        agent.has_voice = true;
        agent.on_core(&confirm("c"), now);
        assert_eq!(agent.resolve(now), OrbState::Approval);
        assert!(matches!(agent.take_outbox().as_slice(), [Out::Voice(v)] if v["text"].as_str().unwrap().contains("Send this?")));
    }

    #[test]
    fn a_dialog_this_screen_cannot_show_is_cancelled_and_said() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "input_request", "request_id": "x", "kind": "mystery", "title": "?"}), now);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"request_id": "x", "value": Value::Null}))]);
        assert!(voice.ask.is_none() && !voice.notice.is_empty());
    }

    #[test]
    fn a_command_result_shows_how_it_went_and_takes_over_what_it_changed() {
        let now = Instant::now();
        let mut voice = voice();
        voice.start_command("models", false);
        voice.on_core(&json!({"type": "command_result", "name": "models", "ok": true, "message": "Models updated. / Модели обновлены.",
            "effects": {"provider": "openai", "models": {"fast": "gpt-fast", "smart": "gpt-smart"}}}), now);
        assert_eq!((voice.provider.as_str(), voice.model.as_str()), ("openai", "gpt-fast"));
        assert!(matches!(&voice.toast, Some((message, true, _)) if message.starts_with("Models updated") || message.starts_with("Модели")));
        assert!(voice.pending.is_none());

        voice.on_core(&json!({"type": "command_result", "name": "provider", "ok": false, "message": "Unknown provider: x"}), now);
        assert!(matches!(&voice.toast, Some((_, false, _))));
        voice.tick(now + Duration::from_secs(TOAST_SECONDS + 1), 0.016, 0.0);
        assert!(voice.toast.is_none());
    }

    #[test]
    fn a_new_or_another_session_clears_what_was_on_screen() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "final", "message": "Готово."}), now);
        voice.turn = Turn::Working;
        voice.start_command("session", false);
        voice.on_core(&json!({"type": "command_result", "name": "session", "ok": true, "message": "Switched.",
            "effects": {"changed": true, "title": "Идея про голос", "session": "chat-1"}}), now);
        assert!(voice.caption.is_empty() && voice.turn == Turn::Idle && voice.last_spoken.is_empty());
        assert_eq!(voice.session, "Идея про голос");

        // A cancelled dialog leaves the screen as it is, and says nothing.
        voice.toast = None;
        voice.on_core(&json!({"type": "final", "message": "Снова."}), now);
        voice.on_core(&json!({"type": "command_result", "name": "session", "ok": true, "message": "Cancelled.", "effects": {"cancelled": true}}), now);
        assert_eq!(voice.caption, "Снова.");
        assert!(voice.toast.is_none());
    }

    #[test]
    fn a_launch_opens_its_own_session_unless_told_to_continue() {
        let now = Instant::now();
        let hello = json!({"type": "hello", "provider": "deepseek", "models": {"fast": "flash"}});
        let mut fresh = voice();
        fresh.on_core(&hello, now);
        assert_eq!(fresh.take_outbox(), vec![Out::Core(json!({"type": "command", "name": "new", "args": []}))]);
        fresh.on_core(&hello, now);
        assert!(fresh.take_outbox().is_empty(), "only once");
        // Its result is not shown: nobody asked for it.
        fresh.on_core(&json!({"type": "command_result", "name": "new", "ok": true, "message": "Session chat-9 created.",
            "effects": {"changed": true, "session": "chat-9"}}), now);
        assert!(fresh.toast.is_none());
        assert_eq!(fresh.session, "chat-9");

        let mut kept = voice();
        kept.fresh_session = false;
        kept.on_core(&hello, now);
        assert!(kept.take_outbox().is_empty());
    }

    #[test]
    fn session_names_come_from_the_core() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "session", "session": "chat-2", "has_checkpoint": false}), now);
        assert_eq!(voice.session, "chat-2");
        voice.on_core(&json!({"type": "session_title", "session": "chat-2", "title": "Про голос"}), now);
        assert_eq!(voice.session, "Про голос");
    }

    #[test]
    fn the_window_is_named_after_the_session_only_once_it_has_a_title() {
        let now = Instant::now();
        let mut voice = voice();
        assert_eq!(voice.window_title(), "Kvist");
        voice.on_core(&json!({"type": "session", "session": "chat-2", "has_checkpoint": false}), now);
        assert_eq!(voice.window_title(), "Kvist");
        voice.on_core(&json!({"type": "session_title", "session": "chat-1", "title": "Not this one"}), now);
        assert_eq!(voice.window_title(), "Kvist", "a title for another session is not this window's");
        voice.on_core(&json!({"type": "session_title", "session": "chat-2", "title": "Про голос\u{7}"}), now);
        assert_eq!(voice.window_title(), "Kvist | Про голос");
    }

    #[test]
    fn a_slash_opens_the_command_list_and_tab_enter_and_the_arrows_work_it() {
        let now = Instant::now();
        let grid = Grid { cols: 120, rows: 36, cw: 10, ch: 20 };
        let mut voice = voice();
        let press = |voice: &mut Voice, code: KeyCode| voice.key(KeyEvent::new(code, KeyModifiers::NONE), now);
        assert!(voice.menu_plan(grid).is_none());
        press(&mut voice, KeyCode::Char('/'));
        assert_eq!(voice.menu_in.target(), 1.0);
        let card = voice.menu_plan(grid).expect("the list opens above the input");
        assert!(card.bottom() < voice.overlay_plan(grid).unwrap().rect.y);

        press(&mut voice, KeyCode::Char('s'));
        press(&mut voice, KeyCode::Down);
        press(&mut voice, KeyCode::Tab);
        assert_eq!(voice.input.as_deref(), Some("/style "));
        assert!(voice.menu_plan(grid).is_none(), "typed arguments close the list");
        assert_eq!(voice.menu_in.target(), 0.0);

        voice.input = Some("/ne".into());
        voice.sync_menu(now);
        press(&mut voice, KeyCode::Enter);
        assert_eq!(voice.take_outbox(), vec![Out::Core(json!({"type": "command", "name": "new", "args": []}))]);
    }

    #[test]
    fn the_menu_and_the_dialogs_take_the_bottom_of_the_screen_above_the_footer() {
        let now = Instant::now();
        let grid = Grid { cols: 120, rows: 36, cw: 10, ch: 20 };
        let mut voice = voice();
        assert!(voice.overlay_plan(grid).is_none());
        voice.model = "deepseek-flash".into();
        voice.menu = Some(2);
        let menu = voice.overlay_plan(grid).unwrap();
        assert!(menu.rect.x + menu.rect.width <= grid.cols && menu.rect.y + menu.rect.height + 1 < grid.rows);
        assert!(menu.rows.iter().any(|row| row.line.iter().any(|(text, _)| text.contains("deepseek-flash"))));
        voice.menu = None;
        voice.on_core(&command_choice("k"), now);
        assert!(voice.overlay_plan(grid).is_some());
        // A small window still fits it.
        let small = Grid { cols: 50, rows: 20, cw: 10, ch: 20 };
        let plan = voice.overlay_plan(small).unwrap();
        assert!(plan.rect.x + plan.rect.width <= small.cols && plan.rect.y + plan.rect.height <= small.rows);
    }

    #[test]
    fn the_result_of_a_command_sits_on_the_card() {
        let now = Instant::now();
        let grid = Grid { cols: 100, rows: 30, cw: 10, ch: 20 };
        let mut voice = voice();
        voice.ears = Ears::Idle;
        let plain = voice.card_plan(grid);
        voice.toast = Some(("Provider changed.".into(), true, now));
        let with = voice.card_plan(grid);
        assert_eq!(with.rect.height, plain.rect.height + 1);
        let shown: String = with.toast.as_ref().unwrap().iter().map(|(text, _)| text.as_str()).collect();
        assert!(shown.ends_with("Provider changed."));
    }

    #[test]
    fn the_core_events_move_the_turn_and_fill_the_status_line() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "hello", "provider": "deepseek", "models": {"fast": "flash", "smart": "pro"}}), now);
        assert_eq!((voice.provider.as_str(), voice.model.as_str()), ("deepseek", "flash"));
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "s"}), now);
        assert_eq!(voice.turn, Turn::Thinking);
        voice.on_core(&json!({"type": "step", "action_type": "read_file"}), now);
        assert_eq!(voice.turn, Turn::Working);
        voice.on_core(&json!({"type": "step", "action_type": "final_answer"}), now);
        assert_eq!(voice.turn, Turn::Working);
        voice.on_core(&json!({"type": "usage", "model": "pro", "context_tokens": 25_000, "context_limit": 100_000}), now);
        assert_eq!(voice.model, "pro");
        assert_eq!(voice.context, Some(0.25));
        voice.on_core(&json!({"type": "turn_end"}), now);
        assert_eq!(voice.turn, Turn::Idle);
    }

    #[test]
    fn a_streamed_reply_builds_the_caption() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "stream_start", "stream_id": "s"}), now);
        voice.on_core(&json!({"type": "stream_chunk", "stream_id": "s", "text": "Hel", "append": true}), now);
        voice.on_core(&json!({"type": "stream_chunk", "stream_id": "s", "text": "lo", "append": true}), now);
        assert_eq!(voice.caption, "Hello");
        voice.on_core(&json!({"type": "stream_chunk", "stream_id": "s", "text": "Fresh", "append": false}), now);
        assert_eq!(voice.caption, "Fresh");
    }

    #[test]
    fn the_demo_walks_through_every_state_and_can_be_steered() {
        let start = Instant::now();
        let mut voice = Voice::new(true, start);
        let mut seen = vec![voice.resolve(start)];
        let mut elapsed = 0;
        for step in 1..ALL_STATES.len() {
            elapsed += demo_seconds(ALL_STATES[step - 1]);
            voice.tick(start + Duration::from_secs(elapsed), 0.016, 0.0);
            seen.push(voice.resolve(start));
        }
        assert_eq!(seen, ALL_STATES.to_vec());
        voice.key(KeyEvent::new(KeyCode::Left, KeyModifiers::NONE), start);
        assert_eq!(voice.resolve(start), ALL_STATES[ALL_STATES.len() - 2]);
        voice.key(KeyEvent::new(KeyCode::Esc, KeyModifiers::NONE), start);
        assert!(voice.quit);
    }

    #[test]
    fn speakable_text_loses_markdown_and_line_breaks() {
        assert_eq!(speakable("# Done\n\n- **one** `two`"), "Done - one two");
        assert_eq!(speakable("  \n "), "");
    }

    #[test]
    fn a_formula_is_spoken_as_text_not_as_latex() {
        assert_eq!(speakable("Ответ: $\\sqrt{9}\\approx3$, **готово**"), "Ответ: √9≈3, готово");
    }

    #[test]
    fn speech_takes_a_third_of_a_second_a_word_within_bounds() {
        assert_eq!(speech_estimate("да"), Duration::from_secs(1));
        assert!(speech_estimate(&"слово ".repeat(100)) <= Duration::from_secs(12));
    }

    #[test]
    fn the_orb_sits_above_the_text_and_inside_the_window() {
        for (cols, rows) in [(120, 36), (80, 24), (200, 60), (40, 12)] {
            let grid = Grid { cols, rows, cw: 10, ch: 20 };
            let spot = orb_box(grid);
            assert!(spot.col + spot.cols <= cols, "{cols}x{rows}: fits across");
            assert!(spot.row + spot.rows + TEXT_ROWS <= rows + 1, "{cols}x{rows}: leaves room for the text");
            // Drawn at the size it is shown at: the picture has the pixels of its cells.
            assert_eq!((spot.width, spot.height), (spot.cols as u32 * 10, spot.rows as u32 * 20), "{cols}x{rows}");
        }
    }

    #[test]
    fn a_very_tall_window_gets_a_smaller_picture_of_the_same_shape() {
        let spot = orb_box(Grid { cols: 400, rows: 120, cw: 10, ch: 22 });
        assert_eq!(spot.height, ORB_TEXTURE_MAX);
        let (cells_w, cells_h) = (spot.cols as f32 * 10.0, spot.rows as f32 * 22.0);
        assert!((spot.width as f32 / spot.height as f32 - cells_w / cells_h).abs() < 0.005);
    }

    #[test]
    fn the_tools_of_a_turn_become_modules_and_the_turn_s_end_ends_them() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "step", "batch_id": "b", "call_id": "c1", "action_type": "skill_call", "action_args": {"skill": "browser"}, "action_description": "youtube.com"}), now);
        voice.on_core(&json!({"type": "step", "batch_id": "b", "call_id": "c2", "action_type": "run_shell", "action_description": "runs the tests"}), now);
        // The words that end a turn are spoken, not shown as work.
        voice.on_core(&json!({"type": "step", "batch_id": "b", "call_id": "c3", "action_type": "final_answer"}), now);
        voice.tick(now, 0.016, 0.0);
        let shown: Vec<(String, Status)> = voice.modules.shown().iter().map(|m| (module_face(m).1, m.status())).collect();
        assert_eq!(shown, [(strings().module_browser.to_string(), Status::Running), (strings().module_shell.to_string(), Status::Running)]);

        voice.on_core(&json!({"type": "action_result", "call_id": "c2", "ok": false, "summary": "Command exited with code 1"}), now);
        let shell = &voice.modules.shown()[1];
        assert_eq!((shell.status(), shell.detail.as_str()), (Status::Failed, "Command exited with code 1"));
        voice.on_core(&json!({"type": "turn_end"}), now);
        assert_eq!(voice.modules.shown()[0].status(), Status::Done);
    }

    #[test]
    fn a_skill_is_named_by_itself_and_a_new_session_has_no_modules() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "step", "call_id": "c1", "action_type": "skill_call", "action_args": {"skill": "calendar"}, "action_description": "adds an interview"}), now);
        voice.tick(now, 0.016, 0.0);
        assert_eq!(module_face(&voice.modules.shown()[0]).1, "calendar");
        voice.reset_conversation();
        assert!(voice.modules.shown().is_empty());
    }

    #[test]
    fn a_module_unfolds_out_of_the_ball_and_its_text_comes_last() {
        let now = Instant::now();
        let grid = Grid { cols: 213, rows: 54, cw: 9, ch: 20 };
        let mut voice = voice();
        voice.on_core(&json!({"type": "step", "call_id": "c1", "action_type": "fetch_url", "action_description": "youtube.com"}), now);
        voice.tick(now, 0.016, 0.0);
        let orb = orb_box(grid);
        let centre = (orb.col as f32 + orb.cols as f32 / 2.0) * 9.0;

        // Inside the ball there is nothing to see, and the first of it is a line at its centre.
        assert!(voice.module_spots(grid, now).is_empty());
        let line = voice.module_spots(grid, now + Duration::from_millis(250))[0];
        assert!(line.height < 6.0 && line.width > line.height, "a thin line: {line:?}");
        assert!((line.x + line.width / 2.0 - centre).abs() < line.cells.width as f32 * 9.0 / 2.0, "it is still near the ball's centre");
        assert_eq!(line.text, 0.0);

        // Half way it is a broad line on its way out, not yet a card.
        let half = voice.module_spots(grid, now + Duration::from_millis(400))[0];
        assert!(half.width > half.cells.width as f32 * 9.0 * 0.8 && half.height < half.cells.height as f32 * 20.0 * 0.4, "{half:?}");

        let arrived = voice.module_spots(grid, now + Duration::from_secs(2))[0];
        assert_eq!((arrived.x, arrived.y), (arrived.cells.x as f32 * 9.0, arrived.cells.y as f32 * 20.0));
        assert_eq!((arrived.width, arrived.height), (arrived.cells.width as f32 * 9.0, arrived.cells.height as f32 * 20.0));
        assert_eq!(arrived.text, 1.0);
        // The twig ends on the card's near edge, outside the ball.
        assert!(arrived.twig.to.0 > crate::orb::BALL * 1.2 && arrived.twig.flow == 1.0);

        // An open menu covers nothing up there, but a dialog over a module hides it.
        voice.menu = Some(0);
        let hidden_by = voice.overlay_plan(grid).unwrap().rect;
        let visible = voice.module_spots(grid, now + Duration::from_secs(2)).len();
        assert_eq!(visible == 0, hidden_by.intersects(arrived.cells));
    }

    #[test]
    fn the_models_thoughts_open_a_card_with_the_first_words_and_close_it_when_it_has_thought() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "s"}), now);
        voice.tick(now, 0.016, 0.0);
        assert!(voice.modules.shown().is_empty(), "no words yet, so nothing to show");

        voice.on_core(&json!({"type": "reasoning_chunk", "stream_id": "s", "text": "  Сначала   открою\nсайт "}), now);
        voice.tick(now, 0.016, 0.0);
        let card = &voice.modules.shown()[0];
        assert_eq!((card.kind, card.detail.as_str(), card.status()), (ModuleKind::Thoughts, "Сначала открою\nсайт", Status::Running));

        // The text is a snapshot that grows: the same card, not a new one for each.
        voice.on_core(&json!({"type": "reasoning_chunk", "stream_id": "s", "text": "Сначала открою сайт, потом поищу."}), now);
        assert_eq!(voice.modules.shown().len(), 1);
        assert_eq!(voice.modules.shown()[0].detail, "Сначала открою сайт, потом поищу.");

        let ended = now + Duration::from_secs(4);
        voice.on_core(&json!({"type": "reasoning_commit", "stream_id": "s", "duration_seconds": 4.0}), ended);
        assert_eq!(voice.modules.shown()[0].status(), Status::Done);
        voice.tick(ended + Duration::from_millis(1000), 0.016, 0.0);
        assert_eq!(voice.modules.shown()[0].out.target(), 0.0, "it closes soon after the thinking");

        // The next thought opens a card again.
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "t"}), ended);
        voice.on_core(&json!({"type": "reasoning_chunk", "stream_id": "t", "text": "Теперь пауза."}), ended);
        // The place is the old card's until it has folded away.
        voice.tick(ended + Duration::from_millis(1100), 0.016, 0.0);
        voice.tick(ended + Duration::from_millis(1700), 0.016, 0.0);
        voice.tick(ended + Duration::from_millis(1710), 0.016, 0.0);
        assert!(voice.modules.shown().iter().any(|card| card.detail == "Теперь пауза." && card.out.target() == 1.0));
    }

    #[test]
    fn the_core_sends_the_thought_in_pieces_and_they_are_put_together() {
        let now = Instant::now();
        let mut voice = voice();
        let piece = |text: &str, append: Value| json!({"type": "reasoning_chunk", "stream_id": "s", "text": text, "append": append});
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "s"}), now);
        voice.on_core(&piece("Сначала  открою", json!(true)), now);
        voice.on_core(&piece(" сайт.\n\n", json!(true)), now);
        voice.on_core(&piece("- потом   поищу", json!(true)), now);
        voice.tick(now, 0.016, 0.0);
        assert_eq!(voice.modules.shown()[0].detail, "Сначала открою сайт.\n- потом поищу", "lines stay lines");

        // The text rewound: what comes is the whole thought now.
        voice.on_core(&piece("Передумала.", json!(false)), now);
        assert_eq!(voice.modules.shown()[0].detail, "Передумала.");

        // The next thought starts from nothing, not from the end of this one.
        voice.on_core(&json!({"type": "reasoning_commit", "stream_id": "s", "duration_seconds": 1.0}), now);
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "t"}), now);
        voice.on_core(&piece("Новая.", json!(true)), now);
        assert!(voice.modules.has("thoughts:2"));
        assert_eq!(voice.modules.shown()[0].detail, "Передумала.", "the old card keeps its own words");
    }

    #[test]
    fn the_thoughts_card_is_as_tall_as_its_words_and_no_taller_than_its_window() {
        let now = Instant::now();
        let grid = Grid { cols: 213, rows: 54, cw: 9, ch: 20 };
        let mut voice = voice();
        voice.tall[modules::TALL_THOUGHTS].rect = modules::tall_place(grid, orb_box(grid).cells(), modules::TALL_THOUGHTS);
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "s"}), now);
        voice.on_core(&json!({"type": "reasoning_chunk", "stream_id": "s", "text": "Коротко.", "append": true}), now);
        voice.tick(now, 0.016, 0.0);
        let rows = |voice: &Voice| voice.tall[modules::TALL_THOUGHTS].rows;
        assert_eq!(rows(&voice), 4.0, "a header, one line, a blank row above and below");

        let long = "слово ".repeat(200);
        voice.on_core(&json!({"type": "reasoning_chunk", "stream_id": "s", "text": long, "append": true}), now);
        for _ in 0..200 {
            voice.tick(now, 0.016, 0.0);
        }
        assert!((rows(&voice) - (3.0 + modules::TALL_LINES as f32)).abs() < 0.05, "{}", rows(&voice));
    }

    #[test]
    fn a_write_shows_its_diff_first_from_its_arguments_and_then_as_the_core_made_it() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "step", "call_id": "w1", "batch_id": "b", "action_type": "write_file", "action_description": "writes a.py",
            "action_args": {"path": "src/a.py", "content": "import os\nprint(os.getcwd())\n"}}), now);
        voice.tick(now, 0.016, 0.0);
        let card = &voice.modules.shown()[0];
        assert_eq!((card.kind, card.slot), (ModuleKind::Edit, modules::TALL_SLOT + modules::TALL_EDITS));
        let preview = card.preview.as_ref().unwrap();
        assert_eq!((preview.path.as_str(), preview.added, preview.removed), ("src/a.py", 2, 0));
        assert!(preview.patch.contains("+import os\n+print(os.getcwd())"));

        // A replacement: the old fragment goes, the new one comes.
        voice.on_core(&json!({"type": "step", "call_id": "w2", "batch_id": "b", "action_type": "replace_in_file",
            "action_args": {"path": "src/a.py", "old_text": "a\nb", "new_text": "c"}}), now);
        let replaced = &voice.modules.shown()[0];
        assert_eq!(replaced.progress().1, 2, "two writes of a response share the card, which shows the newest");
        assert!(replaced.preview.as_ref().unwrap().patch.contains("-a\n-b\n+c"));

        // The core's own diff has the file's line numbers and the true counts.
        let patch = "--- a/src/a.py\n+++ b/src/a.py\n@@ -10,2 +10,3 @@\n x\n+import os\n+print(1)\n y\n";
        voice.on_core(&json!({"type": "action_result", "call_id": "w1", "ok": true, "summary": "Wrote 2 lines", "path": "src/a.py",
            "lines_added": 2, "lines_removed": 0, "patch": patch}), now);
        let preview = voice.modules.shown()[0].preview.as_ref().unwrap();
        assert!(preview.patch.starts_with("--- a/src/a.py") && preview.added == 2);
        assert_eq!(voice.edit_generation, 3, "each new diff makes the drawn lines stale");
    }

    #[test]
    fn a_command_shows_as_a_terminal_and_its_output_comes_line_by_line() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "step", "call_id": "s1", "batch_id": "b", "action_type": "run_shell", "action_description": "run the tests",
            "action_args": {"command": ["python", "-m", "pytest", "-q", "my tests"]}}), now);
        voice.tick(now, 0.016, 0.0);
        let card = &voice.modules.shown()[0];
        assert_eq!(card.slot, modules::TALL_SLOT + modules::TALL_SHELL);
        assert_eq!(card.shell.as_ref().unwrap().command, "python -m pytest -q 'my tests'");

        voice.on_core(&json!({"type": "action_result", "call_id": "s1", "ok": false, "summary": "Command exited with code 1", "exit_code": 1,
            "output": "FAILED test_a\n1 failed\n"}), now);
        let shell = voice.modules.shown()[0].shell.as_ref().unwrap();
        assert_eq!((shell.output.as_deref(), shell.exit), (Some("FAILED test_a\n1 failed\n"), Some(1)));
        // The command is on screen at once; the output follows, a line at a time.
        let (head, lines) = shell_lines(shell, 40);
        assert_eq!((head, lines.len()), (1, 4), "the command, two lines of output, how it ended");
        assert_eq!(shown_rows(head, lines.len(), None, true), 1);
        assert_eq!(shown_rows(head, lines.len(), Some(0.0), true), 1);
        assert!(shown_rows(head, lines.len(), Some(0.05), true) > 1);
        assert_eq!(shown_rows(head, lines.len(), Some(5.0), true), 4);
        assert_eq!(shown_rows(head, 9, None, false), 9, "a card that is not a terminal shows all");
    }

    #[test]
    fn a_tool_that_is_not_a_write_has_no_diff() {
        assert!(preview_from_args("read_file", &json!({"path": "a.py"})).is_none());
        assert!(preview_from_args("write_file", &json!({"content": "x"})).is_none(), "no path");
    }

    #[test]
    fn the_card_of_a_write_is_as_tall_as_its_diff_and_no_taller_than_its_window() {
        let now = Instant::now();
        let grid = Grid { cols: 213, rows: 54, cw: 9, ch: 20 };
        let mut voice = voice();
        let orb = orb_box(grid).cells();
        voice.tall[modules::TALL_EDITS].rect = modules::tall_place(grid, orb, modules::TALL_EDITS);
        let content: String = (0..40).map(|n| format!("line {n}\n")).collect();
        voice.on_core(&json!({"type": "step", "call_id": "w1", "batch_id": "b", "action_type": "write_file",
            "action_args": {"path": "a.py", "content": content}}), now);
        for _ in 0..200 {
            voice.tick(now, 0.016, 0.0);
        }
        let rows = voice.tall[modules::TALL_EDITS].rows;
        assert!((rows - (3.0 + modules::TALL_LINES as f32)).abs() < 0.05, "{rows}");
    }

    #[test]
    fn a_model_that_does_not_show_its_thinking_gets_no_card() {
        // Some providers send the time it thought for and no words (the grey block stays empty).
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "s"}), now);
        voice.on_core(&json!({"type": "reasoning_chunk", "stream_id": "s", "text": ""}), now);
        voice.on_core(&json!({"type": "reasoning_commit", "stream_id": "s", "duration_seconds": 9.0}), now);
        voice.tick(now, 0.016, 0.0);
        assert!(voice.modules.shown().is_empty());
    }

    #[test]
    fn only_the_end_of_a_very_long_thought_is_kept() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_core(&json!({"type": "reasoning_start", "stream_id": "s"}), now);
        let long = format!("{} конец мысли", "слово ".repeat(500));
        voice.on_core(&json!({"type": "reasoning_chunk", "stream_id": "s", "text": long}), now);
        voice.tick(now, 0.016, 0.0);
        let kept = &voice.modules.shown()[0].detail;
        assert!(kept.chars().count() <= THOUGHT_KEPT_CHARS + 1 && kept.ends_with("конец мысли"), "{} chars", kept.chars().count());
    }

    #[test]
    fn the_person_s_voice_shows_on_the_orb_while_they_speak() {
        let now = Instant::now();
        let mut voice = voice();
        voice.ears = Ears::Idle;
        voice.on_ears(&json!({"event": "phrase", "on": true}), now);
        voice.on_ears(&json!({"event": "level", "value": 0.8, "pause": 0.0}), now);
        for _ in 0..20 {
            voice.tick(now, 0.016, 0.0);
            voice.on_ears(&json!({"event": "level", "value": 0.8, "pause": 0.4}), now);
        }
        assert_eq!(voice.resolve(now), OrbState::Waiting, "hearing somebody is not yet being spoken to");
        assert!(voice.orb.level > 0.5, "the light follows the voice: {}", voice.orb.level);
        assert!((voice.orb.pause - 0.4).abs() < 1e-6);

        voice.on_ears(&json!({"event": "phrase", "on": false}), now);
        for _ in 0..60 {
            voice.tick(now, 0.016, 0.0);
        }
        assert!(voice.orb.level < 0.05 && voice.orb.pause == 0.0);
    }

    #[test]
    fn between_the_phrases_of_one_request_the_orb_listens() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_ears(&json!({"event": "state", "state": "transcribing"}), now);
        assert_eq!(voice.resolve(now), OrbState::Transcribing);
        // The first phrase is being written down and the person is already saying the next.
        voice.on_ears(&json!({"event": "phrase", "on": true}), now);
        assert_eq!(voice.resolve(now), OrbState::Listening);
        voice.on_ears(&json!({"event": "phrase", "on": false}), now);
        assert_eq!(voice.resolve(now), OrbState::Transcribing);
    }

    #[test]
    fn lost_words_are_admitted_on_the_card() {
        let now = Instant::now();
        let mut voice = voice();
        voice.on_ears(&json!({"event": "missed"}), now);
        assert_eq!(voice.toast.as_ref().map(|(message, ok, _)| (message.as_str(), *ok)), Some((strings().voice_missed, false)));
    }

    #[test]
    fn the_demo_plays_modules_while_it_shows_the_working_state() {
        let start = Instant::now();
        let mut voice = Voice::new(true, start);
        let working = ALL_STATES.iter().position(|state| *state == OrbState::Working).unwrap();
        voice.step_demo(working, start);
        // First the model thinks, and its card is the only one.
        voice.tick(start + Duration::from_millis(1500), 0.016, 1.5);
        assert_eq!(voice.modules.shown().iter().map(|module| module.kind).collect::<Vec<_>>(), [ModuleKind::Thoughts]);
        // Then the tools come out, three of them, and the thoughts have closed.
        voice.tick(start + Duration::from_millis(4600), 0.016, 4.6);
        let tools = voice.modules.shown().iter().filter(|module| module.kind != ModuleKind::Thoughts && !module.leaving()).count();
        assert_eq!(tools, 3);
        voice.tick(start + Duration::from_millis(6800), 0.016, 6.8);
        assert!(voice.modules.shown().iter().all(|module| module.status() == Status::Done));
    }

    #[test]
    fn the_scenery_is_the_photograph_round_the_orb() {
        // Left half red, right half blue; the orb in the middle of the window.
        let grid = Grid { cols: 100, rows: 40, cw: 10, ch: 20 };
        let image = RgbImage::from_fn(1000, 800, |x, _| image::Rgb(if x < 500 { [200, 0, 0] } else { [0, 0, 200] }));
        let scenery = scenery_for(&image, grid);
        let picture = Orb::new(OrbState::Offline).render(0.0, 200, 200, &scenery, &[]);
        let at = |x: usize| picture[(100 * 200 + x) * 4];
        // The glass turns the picture round: what is to the right shows on the left.
        assert!(at(100 - 20) < at(100 + 20), "left of centre is the darker grey of blue");
    }

    #[test]
    fn a_bad_sidecar_command_is_refused_with_its_name() {
        let name = "KVIST_VOICE_TEST_COMMAND";
        assert_eq!(env_command(name), Ok(None));
        // SAFETY: tests in this module read this variable only here.
        unsafe { std::env::set_var(name, "not json") };
        assert!(env_command(name).unwrap_err().contains(name));
        unsafe { std::env::set_var(name, r#"["python", "main.py"]"#) };
        assert_eq!(env_command(name), Ok(Some(vec!["python".to_string(), "main.py".to_string()])));
        unsafe { std::env::remove_var(name) };
    }
}
