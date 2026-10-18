// The interface's state and its loop: core events in, keyboard and mouse in,
// cells drawn by ratatui, pictures placed through kitty. The core runs as a
// child process speaking `--frontend stdio-multi` (core.rs).

use std::io::{self, Write};
use std::path::Path;
use std::sync::mpsc::{Receiver, TryRecvError};
use std::time::{Duration, Instant};

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use crossterm::terminal;
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::Rect;
use serde_json::{Value, json};

use crate::anim::{self, Ease, Tween};
use crate::backdrop::LEVELS;
use crate::core::{self, Core, History, Message};
use crate::dialog::{Ask, Body, Choice};
use crate::edit;
use crate::kitty::Gpu;
use crate::layout::{self, BROWSER_ASPECT, Grid, Layout, MARGIN_X, Shape};
use crate::model::{CoreEvent, Session};
use crate::models::ModelPicker;
use crate::pictures::{
    Backdrop, CardSpec, CardStyle, Pictures, PopupSpec, SLOT_BROWSER_CARD, SLOT_DIALOG, SLOT_HELPERS, SLOT_INPUT,
    SLOT_LEFT, SLOT_MENU, SLOT_READING, Scene, ThumbSpec, Worker,
};
use crate::probe::Caps;
use crate::protocol;
use crate::screen::Feed;
use crate::slash::{self, Command, Enter};
use crate::strings::{self, strings};
use crate::term;
use crate::view::{self, Hit};

/// The photograph stays alone on screen at least this long.
const SPLASH_MIN: Duration = Duration::from_millis(2400);
/// How long it takes to rise from black.
const RISE: Duration = Duration::from_millis(800);
/// A replayed journal counts as shown this long ago, so its answers appear at
/// once instead of typing out again.
const LONG_AGO: Duration = Duration::from_secs(3600);
/// The backdrop keeps moving even while the interface itself is idle.
const IDLE_FRAME: Duration = crate::video::FRAME_TIME;
/// How long a notice stays in the status row.
const TOAST: Duration = Duration::from_millis(4500);
/// The request id of `/tui`, a dialog answered here and never sent to the core.
const FRONTEND_REQUEST: &str = "local:frontend";
/// Exit status that asks the launcher to start another interface.
const SWITCH_FRONTEND: u8 = 64;
/// A second Esc within this time stops the running turn.
const STOP_CONFIRM: Duration = Duration::from_secs(3);

pub enum Phase {
    Splash,
    Main { since: Instant },
}

pub enum Modal {
    /// A dialog of the core; `None` while the core works on the answer.
    Ask(Option<Ask>),
    /// The model picker across providers; `None` while the catalog loads
    /// or the chosen models are applied.
    Models(Option<ModelPicker>),
    Browser,
}

pub struct Toast {
    pub text: String,
    pub error: bool,
    pub at: Instant,
}

/// What the core reported about the agent it runs.
#[derive(Default)]
pub struct Agent {
    pub provider: String,
    pub fast: String,
    pub smart: String,
    pub effort: String,
}

pub struct App {
    pub grid: Grid,
    pub started: Instant,
    pub phase: Phase,
    canvas_at: Option<Instant>,
    pub session: Session,
    core: Option<Core>,
    /// Frames of the agent's browser, while it has one.
    feed: Option<Feed>,
    /// The browser closed: its pictures are to be dropped.
    web_closed: bool,
    /// Width / height of the browser's latest frame.
    pub web_aspect: Option<f32>,
    /// When Esc was pressed once during a turn.
    stop_armed: Option<Instant>,
    /// Why the core is not available, with where its log is.
    pub core_error: Option<String>,
    pub agent: Agent,
    /// The slash command the core is running.
    command: Option<String>,
    /// The journal of the session just switched to, on its way.
    replay: Option<Receiver<Result<History, String>>>,
    pub toast: Option<Toast>,
    pub input: String,
    pub cursor: usize,
    /// Earlier inputs of this workspace, oldest first.
    history: Vec<String>,
    /// The inputs on their way from the core.
    history_in: Option<Receiver<Result<Vec<String>, String>>>,
    /// How far back in `history` the box shows (0 = the newest); `None` while typing.
    recall: Option<usize>,
    /// What was typed before walking back through the history.
    draft: String,
    /// The slash menu's chosen row (clamped to the list; reset by every edit).
    pick: usize,
    /// 0 while the slash menu is closed, 1 once it has opened.
    pub menu_in: Tween,
    pub last_typed: Instant,
    pub workspace: String,
    pub branch: String,
    pub left_in: Tween,
    pub input_pos: Tween,
    pub input_rise: Tween,
    pub helpers_in: Tween,
    pub browser_in: Tween,
    pending_plan: Option<String>,
    pub modal: Option<Modal>,
    pub modal_p: Tween,
    /// Where the open dialog is; kept while the core works, so the pane
    /// does not jump between two questions.
    pub dialog_rect: Rect,
    pub helper_focus: Option<usize>,
    pub hover: Option<Hit>,
    last_mouse: (u16, u16),
    hand: bool,
    hand_sent: Option<bool>,
    hits: Vec<(Rect, Hit)>,
    reading: Option<Rect>,
    pub scroll: usize,
    pub layout: Layout,
    quit: bool,
    /// What the program exits with: 0, or 64 to start another interface.
    exit_code: u8,
}

impl App {
    fn new(grid: Grid, now: Instant, workspace: &Path) -> Self {
        App {
            grid,
            started: now,
            phase: Phase::Splash,
            canvas_at: None,
            session: Session::default(),
            core: None,
            feed: None,
            web_closed: false,
            web_aspect: None,
            stop_armed: None,
            core_error: None,
            agent: Agent::default(),
            command: None,
            replay: None,
            toast: None,
            input: String::new(),
            cursor: 0,
            history: Vec::new(),
            history_in: None,
            recall: None,
            draft: String::new(),
            pick: 0,
            menu_in: Tween::settled(0.0),
            last_typed: now,
            workspace: workspace_label(workspace),
            branch: git_branch(workspace).unwrap_or_default(),
            left_in: Tween::settled(0.0),
            input_pos: Tween::settled(0.0),
            input_rise: Tween::settled(0.0),
            helpers_in: Tween::settled(0.0),
            browser_in: Tween::settled(0.0),
            pending_plan: None,
            modal: None,
            modal_p: Tween::settled(0.0),
            dialog_rect: Rect::default(),
            helper_focus: None,
            hover: None,
            last_mouse: (0, 0),
            hand: false,
            hand_sent: None,
            hits: Vec::new(),
            reading: None,
            scroll: 0,
            layout: Layout::default(),
            quit: false,
            exit_code: 0,
        }
    }

    fn shape(&self, input_lines: u16, now: Instant) -> Shape {
        let files = self.session.files.len() as u16;
        let helpers = self.session.helpers.len() as u16;
        let mut left_rows = if files > 0 { files + 1 } else { 0 };
        if helpers > 0 {
            left_rows += helpers + 1 + u16::from(files > 0);
        }
        Shape {
            left_rows,
            browser: self.session.browser.as_ref().map(|_| self.web_aspect.unwrap_or(BROWSER_ASPECT)),
            input_lines,
            input_at_bottom: self.input_pos.value(now),
        }
    }

    fn relayout(&mut self, now: Instant) {
        let mut shape = self.shape(1, now);
        let first = layout::compute(&self.grid, &shape);
        let (lines, _) = view::input_lines(&self.input, self.cursor, first.input.width.saturating_sub(6) as usize);
        shape.input_lines = lines.len() as u16;
        self.layout = layout::compute(&self.grid, &shape);
        match &self.modal {
            Some(Modal::Ask(Some(ask))) => {
                self.dialog_rect = layout::dialog(&self.grid, view::ask_height(ask, self.grid.cols));
            }
            Some(Modal::Models(_)) => self.dialog_rect = layout::dialog(&self.grid, view::MODELS_HEIGHT),
            _ => {}
        }
    }

    fn tick(&mut self, now: Instant, pictures: &mut Pictures) {
        self.poll_core(now);
        self.poll_replay(now);
        self.poll_input_history();
        self.sync_menu(now);
        self.poll_feed(now, pictures);
        if matches!(self.phase, Phase::Splash) && self.splash_over(now, pictures) {
            self.phase = Phase::Main { since: now };
            self.input_rise.go(1.0, now, 600, Ease::Burst);
            pictures.drop_rise();
        }
        if self.modal.is_some() && self.modal_p.target() == 0.0 && !self.modal_p.running(now) {
            self.modal = None;
        }
        if self.toast.as_ref().is_some_and(|t| now.saturating_duration_since(t.at) > TOAST) {
            self.toast = None;
        }
    }

    /// The commands the input is asking for; none while a dialog is open or
    /// the session is closed.
    pub fn menu(&self) -> Vec<&'static Command> {
        if self.modal.is_some() || self.session.closed.is_some() || !matches!(self.phase, Phase::Main { .. }) {
            return Vec::new();
        }
        slash::suggest(&self.input)
    }

    /// The menu's chosen row.
    pub fn chosen(&self) -> usize {
        self.pick.min(self.menu().len().saturating_sub(1))
    }

    /// Opens the menu with a short animation when the list appears; it hides
    /// at once when the list goes, since its rows are gone with it.
    fn sync_menu(&mut self, now: Instant) {
        let open = !self.menu().is_empty();
        if open && self.menu_in.target() == 0.0 {
            self.menu_in.go(1.0, now, 170, Ease::OutCubic);
        } else if !open && self.menu_in.target() != 0.0 {
            self.menu_in = Tween::settled(0.0);
        }
    }

    fn splash_over(&self, now: Instant, pictures: &Pictures) -> bool {
        let risen = self.canvas_at.is_some_and(|at| now.saturating_duration_since(at) >= RISE);
        let long_enough = now.saturating_duration_since(self.started) >= SPLASH_MIN;
        self.session.ready && risen && long_enough && pictures.has_canvas_for(&self.grid)
    }

    /// Applies one session event, starting the motion it causes.
    fn on_session(&mut self, event: CoreEvent, now: Instant) {
        match &event {
            CoreEvent::FileChanged { .. } | CoreEvent::Helper { .. } => self.left_in.go(1.0, now, 480, Ease::Burst),
            CoreEvent::Screen { frame_url, .. } => {
                if self.feed.as_ref().is_none_or(|feed| feed.url != *frame_url) {
                    match Feed::start(frame_url) {
                        Ok(feed) => self.feed = Some(feed),
                        Err(error) => self.notify(error, true, now),
                    }
                }
                self.browser_in.go(1.0, now, 520, Ease::OutBack);
            }
            CoreEvent::ScreenClosed => {
                self.feed = None;
                self.web_closed = true;
                self.browser_in = Tween::settled(0.0);
                if matches!(self.modal, Some(Modal::Browser)) {
                    self.close_modal(now);
                }
            }
            _ => {}
        }
        self.session.apply(event, now);
    }

    fn poll_feed(&mut self, now: Instant, pictures: &mut Pictures) {
        if std::mem::take(&mut self.web_closed) {
            pictures.set_web(None);
        }
        let mut failure = None;
        if let Some(feed) = self.feed.as_ref() {
            while let Ok(frame) = feed.frames.try_recv() {
                match frame {
                    Ok(frame) => pictures.set_web(Some(frame)),
                    Err(error) => failure = Some(error),
                }
            }
        }
        if let Some(error) = failure {
            // One failed fetch ends this preview; the next `screen` event
            // starts a fresh one.
            self.feed = None;
            self.notify(error, true, now);
        }
        self.web_aspect = pictures.web_aspect();
    }

    /// Asks the core for the workspace's earlier inputs, for the Up arrow.
    fn load_input_history(&mut self) {
        self.history_in = self.core.as_ref().map(Core::input_history);
    }

    fn poll_input_history(&mut self) {
        let Some(incoming) = self.history_in.as_ref() else { return };
        match incoming.try_recv() {
            Err(TryRecvError::Empty) => {}
            // What was typed and sent while the list was on its way is newer.
            Ok(Ok(mut earlier)) => {
                earlier.append(&mut self.history);
                self.history = earlier;
                self.history_in = None;
            }
            Ok(Err(_)) | Err(TryRecvError::Disconnected) => self.history_in = None,
        }
    }

    fn poll_replay(&mut self, now: Instant) {
        let Some(replay) = self.replay.as_ref() else { return };
        let reply = match replay.try_recv() {
            Ok(reply) => reply,
            Err(TryRecvError::Empty) => return,
            Err(TryRecvError::Disconnected) => {
                self.replay = None;
                return;
            }
        };
        self.replay = None;
        match reply {
            Ok(history) => self.show_history(history, now),
            Err(error) => self.notify(error, true, now),
        }
    }

    /// Fills the feed from a journal, unless the person already started on
    /// the new session: their own turn is the newer truth.
    fn show_history(&mut self, history: History, now: Instant) {
        if self.session.busy || !self.session.items.is_empty() || history.events.is_empty() {
            return;
        }
        let past = now.checked_sub(LONG_AGO).unwrap_or(now);
        for event in &history.events {
            for change in protocol::translate(event) {
                self.session.apply(change, past);
            }
        }
        self.session.settle();
        if !self.session.files.is_empty() || !self.session.helpers.is_empty() {
            self.left_in.go(1.0, now, 480, Ease::Burst);
        }
        if history.has_more {
            self.notify(strings().earlier_not_shown.to_string(), false, now);
        }
    }

    fn poll_core(&mut self, now: Instant) {
        let Some(core) = self.core.as_ref() else { return };
        let mut events = Vec::new();
        let mut gone = None;
        while let Ok(message) = core.messages.try_recv() {
            match message {
                Message::Event(event) => events.push(event),
                Message::Gone(reason) => {
                    gone = Some(format!("{reason}. {}: {}", strings().core_log, core.log.display()));
                    break;
                }
            }
        }
        for event in events {
            self.on_core(&event, now);
        }
        if let Some(reason) = gone {
            self.core = None;
            self.command = None;
            if matches!(self.modal, Some(Modal::Ask(_))) {
                self.close_modal(now);
            }
            self.notify(format!("{}: {reason}", strings().core_gone), true, now);
            self.core_error = Some(reason);
        }
    }

    fn on_core(&mut self, event: &Value, now: Instant) {
        let text = |key: &str| event.get(key).and_then(Value::as_str).unwrap_or("").to_string();
        match event.get("type").and_then(Value::as_str).unwrap_or("") {
            "hello" => {
                self.session.ready = true;
                self.agent.provider = text("provider");
                self.agent.effort = text("effort");
                self.take_models(event.get("models"));
            }
            "plan" => {
                self.pending_plan = Some(text("markdown"));
                for change in protocol::translate(event) {
                    self.on_session(change, now);
                }
            }
            "wake" => {
                self.pending_plan = None;
                self.on_session(crate::model::CoreEvent::TurnStarted, now);
            }
            "input_request" => self.on_request(event, now),
            "command_result" => self.on_command_result(event, now),
            // An error outside a turn (a failed command) is only a notice.
            "error" if !self.session.busy => self.notify(text("message"), true, now),
            _ => {
                for change in protocol::translate(event) {
                    self.on_session(change, now);
                }
            }
        }
    }

    fn take_models(&mut self, models: Option<&Value>) {
        let Some(models) = models else { return };
        let field = |key: &str| models.get(key).and_then(Value::as_str).map(str::to_string);
        if let Some(fast) = field("fast") {
            self.agent.fast = fast;
        }
        if let Some(smart) = field("smart") {
            self.agent.smart = smart;
        }
    }

    fn on_request(&mut self, event: &Value, now: Instant) {
        let Some(mut ask) = Ask::from_request(event) else {
            // A dialog this interface cannot draw must still be answered, or
            // the core waits for ever.
            let id = event.get("request_id").cloned().unwrap_or(Value::Null);
            self.send(&json!({"request_id": id, "value": null}), now);
            self.notify(strings().unsupported_dialog.to_string(), true, now);
            return;
        };
        if let Body::Choice { choices, .. } = &ask.body
            && choices.iter().any(|choice| choice.value == "accept")
            && choices.iter().any(|choice| choice.value == "revise")
            && let Some(plan) = self.pending_plan.take()
        {
            ask.text = plan;
        }
        let opening = !matches!(self.modal, Some(Modal::Ask(_))) || self.modal_p.target() == 0.0;
        self.modal = Some(Modal::Ask(Some(ask)));
        if opening {
            self.modal_p.go(1.0, now, 200, Ease::OutCubic);
        }
    }

    fn on_command_result(&mut self, event: &Value, now: Instant) {
        self.command = None;
        let ok = event.get("ok").and_then(Value::as_bool).unwrap_or(false);
        if event.get("name").and_then(Value::as_str) == Some("catalog") && ok {
            return self.open_catalog(event.get("effects"), now);
        }
        if matches!(self.modal, Some(Modal::Ask(_) | Modal::Models(_))) {
            self.close_modal(now);
        }
        let effects = event.get("effects");
        if let Some(effects) = effects {
            // `/new` and switching sessions: the feed starts over, and a
            // switch refills it from the session's journal.
            if effects.get("changed").and_then(Value::as_bool) == Some(true) {
                self.reset_view(now);
                let name = event.get("name").and_then(Value::as_str);
                let session = effects.get("session").and_then(Value::as_str);
                if let (Some(core), Some(session), true) = (self.core.as_ref(), session, name != Some("new")) {
                    self.replay = Some(core.history(session));
                }
            }
            if let Some(provider) = effects.get("provider").and_then(Value::as_str) {
                self.agent.provider = provider.to_string();
            }
            if let Some(effort) = effects.get("effort").and_then(Value::as_str) {
                self.agent.effort = effort.to_string();
            }
            self.take_models(effects.get("models"));
        }
        let message = event.get("message").and_then(Value::as_str).unwrap_or("");
        let mut text = core::localized(message, strings::russian()).to_string();
        let warnings: Vec<&str> = effects
            .and_then(|e| e.get("warnings"))
            .and_then(Value::as_array)
            .map(|w| w.iter().filter_map(Value::as_str).collect())
            .unwrap_or_default();
        if let Some(first) = warnings.first() {
            text = format!("{text} {first}");
        }
        self.notify(text, !ok, now);
    }

    /// Ctrl+P: the pane opens at once and waits for the core's catalog of
    /// every connected provider's models.
    fn open_models(&mut self, now: Instant) {
        if self.core.is_none() || self.command.is_some() || self.session.busy {
            self.command("catalog", &[], now);
            return;
        }
        self.modal = Some(Modal::Models(None));
        self.modal_p.go(1.0, now, 200, Ease::OutCubic);
        self.command("catalog", &[], now);
    }

    fn open_catalog(&mut self, effects: Option<&Value>, now: Instant) {
        if !matches!(self.modal, Some(Modal::Models(None))) || self.modal_p.target() == 0.0 {
            // The pane was closed while the catalog loaded.
            return;
        }
        match effects.and_then(ModelPicker::from_catalog) {
            Some(picker) => {
                self.modal = Some(Modal::Models(Some(picker)));
                self.follow_models();
            }
            None => {
                self.close_modal(now);
                self.notify(strings().no_providers.to_string(), true, now);
            }
        }
    }

    fn follow_models(&mut self) {
        let rows = view::models_list_rows(self.dialog_rect);
        if let Some(Modal::Models(Some(picker))) = self.modal.as_mut() {
            picker.follow(rows);
        }
    }

    /// Applies the highlighted model: provider, fast and smart in one rebuild.
    fn choose_model(&mut self, now: Instant) {
        let Some(Modal::Models(Some(picker))) = self.modal.as_ref() else { return };
        let Some([provider, fast, smart]) = picker.choice() else { return };
        self.modal = Some(Modal::Models(None));
        self.command("models", &[&provider, &fast, &smart], now);
    }

    fn notify(&mut self, text: String, error: bool, now: Instant) {
        if !text.trim().is_empty() {
            self.toast = Some(Toast { text, error, at: now });
        }
    }

    fn send(&mut self, message: &Value, now: Instant) {
        let Some(core) = self.core.as_mut() else {
            self.notify(strings().no_core.to_string(), true, now);
            return;
        };
        if let Err(error) = core.send(message) {
            self.notify(format!("{}: {error}", strings().core_gone), true, now);
        }
    }

    /// Runs a slash command in the core; its dialogs arrive as
    /// `input_request` and it ends with `command_result`.
    fn command(&mut self, name: &str, args: &[&str], now: Instant) {
        if self.core.is_none() {
            self.notify(strings().no_core.to_string(), true, now);
            return;
        }
        if self.command.is_some() || self.session.busy {
            self.notify(strings().core_busy.to_string(), true, now);
            return;
        }
        self.command = Some(name.to_string());
        self.send(&json!({"type": "command", "name": name, "args": args}), now);
    }

    /// Sends a dialog's answer. A command may ask again, so its pane stays
    /// (waiting) until `command_result`; a question asked during a turn (an
    /// approval) closes at once.
    fn answer(&mut self, reply: Value, now: Instant) {
        if reply.get("request_id").and_then(Value::as_str) == Some(FRONTEND_REQUEST) {
            return self.switch_frontend(reply.get("value").and_then(Value::as_str), now);
        }
        self.send(&reply, now);
        if self.command.is_none() {
            self.close_modal(now);
            return;
        }
        if let Some(Modal::Ask(slot)) = self.modal.as_mut() {
            *slot = None;
        }
    }

    fn animating(&self, now: Instant) -> bool {
        let tweens = [&self.left_in, &self.input_pos, &self.input_rise, &self.helpers_in, &self.browser_in, &self.modal_p, &self.menu_in];
        matches!(self.phase, Phase::Splash)
            || self.session.busy
            || self.command.is_some()
            || tweens.iter().any(|t| t.running(now))
            || view::revealing(self, now)
            || self.session.files.iter().any(|f| now.saturating_duration_since(f.touched) < Duration::from_millis(1700))
            || self.session.helpers.iter().any(|h| h.state == crate::model::HelperState::Running)
    }

    fn draw(&mut self, frame: &mut ratatui::Frame, now: Instant) {
        self.relayout(now);
        let drawn = view::draw(self, frame.buffer_mut(), now);
        self.hits = drawn.hits;
        self.reading = drawn.reading;
        if let Some(position) = drawn.cursor {
            frame.set_cursor_position(position);
        }
    }

    fn scene(&self, now: Instant, pictures: &Pictures) -> Scene {
        let grid = &self.grid;
        if matches!(self.phase, Phase::Splash) {
            let backdrop = match self.canvas_at {
                None => Backdrop::Black,
                Some(at) => {
                    let t = now.saturating_duration_since(at).as_secs_f32() / RISE.as_secs_f32();
                    let frames = pictures.rise_frames();
                    if t < 1.0 && frames > 0 { Backdrop::Rise((t * frames as f32) as usize) } else { Backdrop::Photo }
                }
            };
            return Scene { backdrop, cards: Vec::new(), thumb: None, popup: None };
        }
        let modal = self.modal_p.value(now).clamp(0.0, 1.0);
        let level = (modal * LEVELS as f32).round() as usize;
        let backdrop = if level == 0 { Backdrop::Sharp } else { Backdrop::Level(level) };
        let dim = modal > 0.35;
        let style = |normal: CardStyle| if dim { CardStyle::Dimmed } else { normal };
        let cw = grid.cw as f32;
        let ch = grid.ch as f32;
        let slide = |rect: Rect, v: f32| (1.0 - v) * (rect.width + MARGIN_X + 1) as f32 * cw;

        let mut cards = Vec::new();
        if let Some(rect) = self.layout.left {
            cards.push(CardSpec { slot: SLOT_LEFT, style: style(CardStyle::Side), rect, dx: -slide(rect, self.left_in.value(now)), dy: 0.0 });
        }
        let browser_dx = self.layout.browser_card.map_or(0.0, |rect| slide(rect, self.browser_in.value(now)));
        if let Some(rect) = self.layout.browser_card {
            cards.push(CardSpec { slot: SLOT_BROWSER_CARD, style: style(CardStyle::Side), rect, dx: browser_dx, dy: 0.0 });
        }
        let helpers = self.helpers_in.value(now);
        if let Some(rect) = self.layout.helpers
            && helpers > 0.001
        {
            cards.push(CardSpec { slot: SLOT_HELPERS, style: style(CardStyle::Side), rect, dx: slide(rect, helpers), dy: 0.0 });
        }
        if let Some(rect) = self.reading {
            cards.push(CardSpec { slot: SLOT_READING, style: style(CardStyle::Reading), rect, dx: 0.0, dy: 0.0 });
        }
        let rise = (1.0 - self.input_rise.value(now)) * 8.0 * ch;
        cards.push(CardSpec { slot: SLOT_INPUT, style: style(CardStyle::Input), rect: self.layout.input, dx: 0.0, dy: rise });
        let shown = self.menu().len();
        let menu = layout::menu(self.layout.input, shown);
        if menu.height > 0 {
            let open = self.menu_in.value(now).clamp(0.0, 1.0);
            let stage = (open * 2.0).round() as u8;
            cards.push(CardSpec { slot: SLOT_MENU, style: CardStyle::Menu(stage), rect: menu, dx: 0.0, dy: (1.0 - open) * 0.8 * ch });
        }
        if let Some(Modal::Ask(_) | Modal::Models(_)) = self.modal
            && modal > 0.05
        {
            let dy = ((1.0 - modal) * 1.4).round() * ch;
            cards.push(CardSpec { slot: SLOT_DIALOG, style: CardStyle::Dialog, rect: self.dialog_rect, dx: 0.0, dy });
        }

        let browser_open = matches!(self.modal, Some(Modal::Browser));
        let thumb = match (self.layout.thumb, &self.session.browser) {
            (Some(rect), Some(_)) if !(browser_open && self.modal_p.value(now) > 0.001) => {
                Some(ThumbSpec { rect, dx: browser_dx, dim })
            }
            _ => None,
        };
        let popup = browser_open.then(|| self.popup(now)).flatten();
        Scene { backdrop, cards, thumb, popup }
    }

    /// The browser growing from its thumbnail to the middle of the screen.
    fn popup(&self, now: Instant) -> Option<PopupSpec> {
        let open = self.modal_p.value(now);
        if open <= 0.001 {
            return None;
        }
        let grid = &self.grid;
        let to = grid.px(layout::browser_full(grid, self.web_aspect.unwrap_or(BROWSER_ASPECT)));
        let from = self.layout.thumb.map(|r| grid.px(r)).unwrap_or((to.0 + to.2 * 0.4, to.1 + to.3 * 0.4, to.2 * 0.2, to.3 * 0.2));
        let lerp = |a: f32, b: f32| a + (b - a) * open;
        let clamped = open.clamp(0.0, 1.0);
        let sharpness = if clamped < 0.45 { 0 } else if clamped < 0.8 { 1 } else { 2 };
        Some(PopupSpec { x: lerp(from.0, to.0), y: lerp(from.1, to.1), w: lerp(from.2, to.2), h: lerp(from.3, to.3), sharpness })
    }

    fn resize(&mut self, grid: Grid) {
        self.grid = grid;
        self.hover = None;
    }

    fn key(&mut self, key: KeyEvent, now: Instant) {
        if key.kind == KeyEventKind::Release {
            return;
        }
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if ctrl && key.code == KeyCode::Char('c') {
            self.quit = true;
            return;
        }
        if matches!(self.phase, Phase::Splash) {
            return;
        }
        match self.modal {
            Some(Modal::Ask(_)) => self.ask_key(key, now),
            Some(Modal::Models(_)) => self.models_key(key, now),
            Some(Modal::Browser) => {
                if key.code == KeyCode::Esc {
                    self.close_modal(now);
                }
            }
            None => self.main_key(key, now),
        }
    }

    fn models_key(&mut self, key: KeyEvent, now: Instant) {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        if key.code == KeyCode::Esc || (ctrl && key.code == KeyCode::Char('p')) {
            return self.close_modal(now);
        }
        if key.code == KeyCode::Enter {
            return self.choose_model(now);
        }
        let Some(Modal::Models(Some(picker))) = self.modal.as_mut() else { return };
        match key.code {
            KeyCode::Tab | KeyCode::BackTab | KeyCode::Left | KeyCode::Right => picker.toggle_role(),
            KeyCode::Up => picker.step(-1),
            KeyCode::Down => picker.step(1),
            KeyCode::PageUp => picker.step(-10),
            KeyCode::PageDown => picker.step(10),
            KeyCode::Backspace => picker.backspace(),
            KeyCode::Char(c) if !ctrl => picker.type_char(c),
            _ => {}
        }
        self.follow_models();
    }

    fn main_key(&mut self, key: KeyEvent, now: Instant) {
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        let alt = key.modifiers.contains(KeyModifiers::ALT);
        let shift = key.modifiers.contains(KeyModifiers::SHIFT);
        match key.code {
            KeyCode::Char('p') if ctrl => return self.open_models(now),
            KeyCode::Char('t') if ctrl => return self.toggle_helpers(None, now),
            KeyCode::Esc if self.session.busy => return self.stop(now),
            KeyCode::Esc if self.helpers_in.target() > 0.0 => return self.toggle_helpers(None, now),
            _ => {}
        }
        if self.session.closed.is_some() {
            if key.code == KeyCode::Enter {
                self.command("new", &[], now);
            }
            return;
        }
        let shown = self.menu().len();
        // A recalled command keeps walking the history; the list is for typing one.
        if shown > 0 && self.recall.is_none() && !ctrl && !alt {
            match key.code {
                KeyCode::Up | KeyCode::Down => {
                    self.pick = slash::step(shown, self.chosen(), if key.code == KeyCode::Up { -1 } else { 1 });
                    return;
                }
                KeyCode::Tab => {
                    let name = self.menu()[self.chosen()].name;
                    return self.fill(format!("/{name} "), now);
                }
                _ => {}
            }
        }
        match key.code {
            KeyCode::Char('u') if ctrl => {
                self.input.clear();
                self.cursor = 0;
                self.pick = 0;
                self.recall = None;
            }
            KeyCode::Enter if shift || alt => self.insert("\n", now),
            KeyCode::Enter => self.enter(now),
            KeyCode::Left if ctrl || alt => self.cursor = edit::word_start(&self.input, self.cursor),
            KeyCode::Right if ctrl || alt => self.cursor = edit::word_end(&self.input, self.cursor),
            // Readline's spelling of the same two moves, which some terminals send for Alt+arrows.
            KeyCode::Char('b') if alt && !ctrl => self.cursor = edit::word_start(&self.input, self.cursor),
            KeyCode::Char('f') if alt && !ctrl => self.cursor = edit::word_end(&self.input, self.cursor),
            KeyCode::Backspace if ctrl || alt => self.delete_chars(edit::word_start(&self.input, self.cursor), self.cursor, now),
            KeyCode::Delete if ctrl || alt => self.delete_chars(self.cursor, edit::word_end(&self.input, self.cursor), now),
            KeyCode::Backspace if self.cursor > 0 => {
                self.cursor -= 1;
                self.remove_at(self.cursor);
            }
            KeyCode::Delete if self.cursor < self.input.chars().count() => self.remove_at(self.cursor),
            KeyCode::Up | KeyCode::Down => self.move_vertically(key.code == KeyCode::Up, now),
            KeyCode::Left => self.cursor = self.cursor.saturating_sub(1),
            KeyCode::Right => self.cursor = (self.cursor + 1).min(self.input.chars().count()),
            KeyCode::Home => self.cursor = 0,
            KeyCode::End => self.cursor = self.input.chars().count(),
            KeyCode::PageUp => self.scroll += 8,
            KeyCode::PageDown => self.scroll = self.scroll.saturating_sub(8),
            KeyCode::Char(c) if !ctrl => self.insert(&c.to_string(), now),
            _ => {}
        }
    }

    fn ask_key(&mut self, key: KeyEvent, now: Instant) {
        let card = self.dialog_rect;
        let Some(Modal::Ask(Some(ask))) = self.modal.as_mut() else { return };
        let rows = view::ask_list_rows(ask, card);
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        let reply = match key.code {
            KeyCode::PageUp => { ask.text_scroll = ask.text_scroll.saturating_sub(6); None }
            KeyCode::PageDown => {
                ask.text_scroll = (ask.text_scroll + 6).min(view::ask_text_max_scroll(ask, card));
                None
            }
            KeyCode::Esc => Some(ask.cancel()),
            KeyCode::Enter => ask.accept(),
            KeyCode::Up => {
                ask.step(-1);
                None
            }
            KeyCode::Down | KeyCode::Tab | KeyCode::Left | KeyCode::Right => {
                ask.step(if key.code == KeyCode::Left { -1 } else { 1 });
                None
            }
            KeyCode::Backspace => {
                ask.backspace();
                None
            }
            KeyCode::Char('u') if ctrl => {
                ask.clear();
                None
            }
            KeyCode::Char(c) if !ctrl => {
                ask.type_char(c);
                None
            }
            _ => None,
        };
        ask.follow(rows);
        if let Some(reply) = reply {
            self.answer(reply, now);
        }
    }

    /// Up/Down move the caret one drawn row, keeping its column. At the first
    /// or last row of typed text they go to its start or end, and once there
    /// Up walks back through the earlier inputs and Down forward again.
    fn move_vertically(&mut self, up: bool, now: Instant) {
        self.last_typed = now;
        let width = self.layout.input.width.saturating_sub(6) as usize;
        if let Some(cursor) = view::move_row(&self.input, self.cursor, width, up) {
            self.cursor = cursor;
            return;
        }
        if self.recall.is_none() && !self.input.is_empty() {
            let edge = if up { 0 } else { self.input.chars().count() };
            if self.cursor != edge {
                self.cursor = edge;
                return;
            }
            if !up {
                return;
            }
        }
        self.walk_history(up);
    }

    fn walk_history(&mut self, back: bool) {
        let newest_last = self.history.len() as i64 - 1;
        let current = self.recall.map_or(-1, |at| at as i64);
        let next = (current + if back { 1 } else { -1 }).clamp(-1, newest_last);
        if next == current {
            return;
        }
        if self.recall.is_none() {
            self.draft = self.input.clone();
        }
        self.recall = (next >= 0).then_some(next as usize);
        self.input = match self.recall {
            Some(at) => self.history[self.history.len() - 1 - at].clone(),
            None => std::mem::take(&mut self.draft),
        };
        self.cursor = self.input.chars().count();
    }

    /// Replaces the input and puts the caret at its end.
    fn fill(&mut self, text: String, now: Instant) {
        self.cursor = text.chars().count();
        self.input = text;
        self.pick = 0;
        self.recall = None;
        self.last_typed = now;
    }

    /// Enter: while the slash menu is open it runs (or completes) the chosen
    /// command; otherwise it sends what was typed.
    fn enter(&mut self, now: Instant) {
        match slash::resolve_enter(&self.input, self.chosen()) {
            Enter::Fill(text) => self.fill(text, now),
            Enter::Submit(text) => {
                self.input = text;
                self.submit(now);
            }
        }
    }

    fn insert(&mut self, s: &str, now: Instant) {
        self.pick = 0;
        self.recall = None;
        let at = self.byte_at(self.cursor);
        self.input.insert_str(at, s);
        self.cursor += s.chars().count();
        self.last_typed = now;
    }

    /// Removes the characters `from..to` and leaves the cursor at `from`.
    fn delete_chars(&mut self, from: usize, to: usize, now: Instant) {
        if from >= to {
            return;
        }
        self.pick = 0;
        self.recall = None;
        let (start, end) = (self.byte_at(from), self.byte_at(to));
        self.input.replace_range(start..end, "");
        self.cursor = from;
        self.last_typed = now;
    }

    fn remove_at(&mut self, index: usize) {
        self.pick = 0;
        self.recall = None;
        let at = self.byte_at(index);
        self.input.remove(at);
    }

    fn byte_at(&self, index: usize) -> usize {
        self.input.char_indices().nth(index).map_or(self.input.len(), |(i, _)| i)
    }

    /// Enter: a `/command` runs in the core; anything else is a request.
    fn submit(&mut self, now: Instant) {
        let text = self.input.trim().to_string();
        if text.is_empty() {
            return;
        }
        self.recall = None;
        self.history.push(text.clone());
        if let Some(core) = self.core.as_ref() {
            core.remember_input(&text);
        }
        if let Some(command) = text.strip_prefix('/') {
            let mut words = command.split_whitespace();
            let name = words.next().unwrap_or("").to_string();
            let args: Vec<&str> = words.collect();
            self.input.clear();
            self.cursor = 0;
            // `/tui` belongs to the launcher, not the core: it picks the interface.
            if name == "tui" {
                return self.ask_frontend(now);
            }
            self.command(&name, &args, now);
            return;
        }
        if self.session.busy || self.command.is_some() {
            self.notify(strings().core_busy.to_string(), true, now);
            return;
        }
        if self.core.is_none() {
            self.notify(strings().no_core.to_string(), true, now);
            return;
        }
        self.send(&json!({"type": "goal", "text": text}), now);
        self.session.submit(text, now);
        self.input.clear();
        self.cursor = 0;
        self.scroll = 0;
        self.input_pos.go(1.0, now, 540, Ease::Burst);
    }

    /// `/tui`: the terminal interfaces the launcher can start, this one first
    /// highlighted. The same list as the Ink app's (frontends/tui/ink/commands.ts).
    fn ask_frontend(&mut self, now: Instant) {
        let s = strings();
        let choices = [
            ("glass", "Classic", s.frontend_glass),
            ("tui", "Ink", s.frontend_ink),
            ("claude", "Claude-style", s.frontend_claude),
            ("terminal", "Classic Legacy", s.frontend_classic),
        ]
        .iter()
        .map(|&(value, label, detail)| Choice { value: value.into(), label: label.into(), detail: detail.into() })
        .collect();
        self.modal = Some(Modal::Ask(Some(Ask::local_choice(FRONTEND_REQUEST, s.frontend_title, choices, "glass"))));
        self.modal_p.go(1.0, now, 200, Ease::OutCubic);
    }

    /// Leaves for another interface: the launcher reads its name from
    /// `KVIST_SWITCH_FILE` when this program exits with code 64.
    fn switch_frontend(&mut self, chosen: Option<&str>, now: Instant) {
        self.close_modal(now);
        let Some(chosen) = chosen.filter(|value| *value != "glass") else { return };
        let Some(path) = std::env::var_os("KVIST_SWITCH_FILE") else {
            return self.notify(strings().frontend_needs_launcher.to_string(), true, now);
        };
        match std::fs::write(&path, format!("{chosen}\n")) {
            Ok(()) => {
                self.exit_code = SWITCH_FRONTEND;
                self.quit = true;
            }
            Err(error) => self.notify(error.to_string(), true, now),
        }
    }

    /// Esc once arms the stop and says so; Esc again within a few seconds
    /// stops the turn and everything it started (`cancel`, §8f).
    fn stop(&mut self, now: Instant) {
        if self.stop_armed.is_some_and(|at| now.saturating_duration_since(at) < STOP_CONFIRM) {
            self.stop_armed = None;
            self.send(&json!({"type": "cancel"}), now);
            return;
        }
        self.stop_armed = Some(now);
        self.notify(strings().stop_hint.to_string(), false, now);
    }

    /// The core moved to another session: the cards go and the input glides
    /// back to the middle.
    fn reset_view(&mut self, now: Instant) {
        self.session = Session::fresh();
        self.replay = None;
        self.feed = None;
        self.web_closed = true;
        self.input.clear();
        self.cursor = 0;
        self.scroll = 0;
        self.helper_focus = None;
        self.input_pos.go(0.0, now, 540, Ease::Burst);
        self.left_in = Tween::settled(0.0);
        self.browser_in = Tween::settled(0.0);
        self.helpers_in.go(0.0, now, 300, Ease::Burst);
    }

    fn paste(&mut self, text: &str, now: Instant) {
        if !matches!(self.phase, Phase::Main { .. }) {
            return;
        }
        match self.modal.as_mut() {
            Some(Modal::Ask(Some(ask))) => ask.paste(text),
            Some(_) => {}
            None if self.session.closed.is_none() => self.insert(&text.replace("\r\n", "\n"), now),
            None => {}
        }
    }

    fn open_browser(&mut self, now: Instant) {
        if self.session.browser.is_none() {
            return;
        }
        self.modal = Some(Modal::Browser);
        self.modal_p.go(1.0, now, 460, Ease::OutBack);
    }

    fn close_modal(&mut self, now: Instant) {
        match self.modal {
            Some(Modal::Browser) => self.modal_p.go(0.0, now, 360, Ease::Burst),
            _ => self.modal_p.go(0.0, now, 150, Ease::OutCubic),
        }
    }

    fn toggle_helpers(&mut self, focus: Option<usize>, now: Instant) {
        let open = self.helpers_in.target() > 0.0;
        if open && (focus.is_none() || focus == self.helper_focus) {
            self.helpers_in.go(0.0, now, 300, Ease::Burst);
            return;
        }
        self.helper_focus = focus;
        self.helpers_in.go(1.0, now, 420, Ease::Burst);
    }

    fn mouse(&mut self, m: MouseEvent, now: Instant) {
        let hit = self.hit_at(m.column, m.row);
        self.hover = hit;
        self.hand = hit.is_some_and(Hit::clickable);
        if matches!(self.phase, Phase::Splash) {
            return;
        }
        match m.kind {
            MouseEventKind::Moved => match (hit, self.modal.as_mut()) {
                (Some(Hit::Choice(row)), Some(Modal::Ask(Some(ask)))) => ask.select_row(row),
                (Some(Hit::ModelRow(row)), Some(Modal::Models(Some(picker)))) => picker.select_row(row),
                (Some(Hit::Command(row)), None) => self.pick = row,
                _ => {}
            },
            MouseEventKind::Down(MouseButton::Left) => self.click(hit, now),
            MouseEventKind::ScrollUp => self.wheel(-1),
            MouseEventKind::ScrollDown => self.wheel(1),
            _ => {}
        }
    }

    fn wheel(&mut self, delta: i32) {
        let card = self.dialog_rect;
        match self.modal.as_mut() {
            Some(Modal::Ask(Some(ask))) => {
                ask.step(delta);
                let rows = view::ask_list_rows(ask, card);
                ask.follow(rows);
            }
            Some(Modal::Models(Some(picker))) => {
                picker.step(delta);
                picker.follow(view::models_list_rows(card));
            }
            Some(_) => {}
            None if delta < 0 => self.scroll += 3,
            None => self.scroll = self.scroll.saturating_sub(3),
        }
    }

    fn click(&mut self, hit: Option<Hit>, now: Instant) {
        match self.modal.as_mut() {
            // A wizard of the core is never cancelled by a stray click; Esc
            // or its own answers end it.
            Some(Modal::Ask(Some(ask))) => {
                let reply = match hit {
                    Some(Hit::Choice(row)) => {
                        ask.select_row(row);
                        ask.accept()
                    }
                    Some(Hit::Answer(answer)) => {
                        ask.pick(answer);
                        ask.accept()
                    }
                    _ => None,
                };
                if let Some(reply) = reply {
                    self.answer(reply, now);
                }
            }
            Some(Modal::Ask(None) | Modal::Models(None)) => {}
            Some(Modal::Models(Some(picker))) => match hit {
                Some(Hit::ModelRow(row)) => {
                    picker.select_row(row);
                    self.choose_model(now);
                }
                Some(Hit::Role(role)) => picker.set_role(role),
                Some(_) => {}
                // A click outside the pane closes it; inside, nothing happens.
                None if !contains(self.dialog_rect, self.last_mouse) => self.close_modal(now),
                None => {}
            },
            Some(Modal::Browser) => self.close_modal(now),
            None => match hit {
                Some(Hit::Thumb) => self.open_browser(now),
                Some(Hit::Helper(id)) => self.toggle_helpers(Some(id), now),
                Some(Hit::HelpersTitle | Hit::HelpersClose) => self.toggle_helpers(None, now),
                Some(Hit::NewSession) => self.command("new", &[], now),
                Some(Hit::Provider) => self.command("provider", &[], now),
                Some(Hit::Models) => self.open_models(now),
                Some(Hit::Command(row)) => {
                    self.pick = row;
                    self.enter(now);
                }
                _ => {}
            },
        }
    }

    fn hit_at(&mut self, x: u16, y: u16) -> Option<Hit> {
        self.last_mouse = (x, y);
        self.hits.iter().rev().find(|(rect, _)| contains(*rect, (x, y))).map(|(_, hit)| *hit)
    }

    fn flush_pointer(&mut self, out: &mut impl Write) -> io::Result<()> {
        if self.hand_sent == Some(self.hand) {
            return Ok(());
        }
        out.write_all(term::pointer_shape(self.hand))?;
        out.flush()?;
        self.hand_sent = Some(self.hand);
        Ok(())
    }
}

fn contains(rect: Rect, (x, y): (u16, u16)) -> bool {
    x >= rect.x && x < rect.right() && y >= rect.y && y < rect.bottom()
}

fn workspace_label(workspace: &Path) -> String {
    let path = workspace.display().to_string();
    match std::env::var("HOME") {
        Ok(home) if !home.is_empty() && path.starts_with(&home) => format!("~{}", &path[home.len()..]),
        _ => path,
    }
}

fn git_branch(dir: &Path) -> Option<String> {
    let mut dir = std::fs::canonicalize(dir).ok()?;
    loop {
        let head = dir.join(".git/HEAD");
        if let Ok(text) = std::fs::read_to_string(&head) {
            return Some(text.trim().strip_prefix("ref: refs/heads/").unwrap_or("detached").to_string());
        }
        if !dir.pop() {
            return None;
        }
    }
}

fn grid_now() -> io::Result<Grid> {
    let size = terminal::window_size()?;
    let cols = size.columns.max(1);
    let rows = size.rows.max(1);
    Ok(Grid { cols, rows, cw: (size.width / cols).max(1) as u32, ch: (size.height / rows).max(1) as u32 })
}

/// Starts the core, or says why it could not.
fn start_core(workspace: &Path) -> Result<Core, String> {
    let argv = core::command(std::env::var("KVIST_CORE_CMD").ok().as_deref())?;
    Core::spawn(&argv, workspace).map_err(|error| format!("{}: {error}", argv.join(" ")))
}

/// Runs the interface; the result is the exit status (64: start the
/// interface named in `KVIST_SWITCH_FILE`).
pub fn run(caps: Caps, fps: u32, workspace: &Path) -> io::Result<u8> {
    term::enter()?;
    let result = run_loop(caps, fps, workspace);
    let restored = term::leave();
    let code = result?;
    restored.map(|()| code)
}

type Screen = Terminal<CrosstermBackend<io::Stdout>>;

fn run_loop(caps: Caps, fps: u32, workspace: &Path) -> io::Result<u8> {
    let mut terminal = Terminal::new(CrosstermBackend::new(io::stdout()))?;
    terminal.clear()?;
    crate::highlight::warm();
    let worker = Worker::spawn();
    let mut pictures = Pictures::new(Gpu::new(caps.temp_dir));
    let mut app = App::new(grid_now()?, Instant::now(), workspace);
    match start_core(workspace) {
        Ok(core) => app.core = Some(core),
        Err(reason) => app.core_error = Some(reason),
    }
    app.load_input_history();
    worker.request(app.grid.cols as u32 * app.grid.cw, app.grid.rows as u32 * app.grid.ch)?;
    let mut out = io::stdout();
    let period = Duration::from_secs_f64(1.0 / fps as f64);
    let mut due = Instant::now();

    while !app.quit {
        let now = Instant::now();
        while matches!(app.phase, Phase::Main { .. }) && let Ok(error) = worker.errors.try_recv() {
            let message = if strings::russian() {
                format!("Видео фона недоступно, показан неподвижный кадр. Проверьте ffmpeg: {error}")
            } else {
                format!("Background video unavailable; showing a still frame. Check ffmpeg: {error}")
            };
            app.notify(message, true, now);
        }
        while let Ok(canvas) = worker.results.try_recv() {
            if canvas.width != app.grid.cols as u32 * app.grid.cw
                || canvas.height != app.grid.rows as u32 * app.grid.ch { continue; }
            if app.canvas_at.is_none() {
                app.canvas_at = Some(now);
            }
            pictures.accept(canvas);
            if let Some(osc) = pictures.padding_colour() {
                out.write_all(osc.as_bytes())?;
            }
        }
        app.tick(now, &mut pictures);
        terminal.draw(|frame| app.draw(frame, now))?;
        let scene = app.scene(now, &pictures);
        pictures.show(&app.grid, &scene)?;
        pictures.gpu.flush(&mut out)?;
        app.flush_pointer(&mut out)?;

        due = anim::next_frame(due, now, period);
        let animating = app.animating(now);
        let wake = if animating { due } else { now + IDLE_FRAME };
        wait_for_events(&mut app, &mut terminal, &mut pictures, &worker, wake, animating)?;
    }
    Ok(app.exit_code)
}

/// Handles input until `wake`. While nothing moves, input is drawn at once;
/// during an animation frames keep their beat.
fn wait_for_events(
    app: &mut App,
    terminal: &mut Screen,
    pictures: &mut Pictures,
    worker: &Worker,
    wake: Instant,
    animating: bool,
) -> io::Result<()> {
    loop {
        let left = wake.saturating_duration_since(Instant::now());
        if left.is_zero() || !event::poll(left)? {
            return Ok(());
        }
        while event::poll(Duration::ZERO)? {
            let now = Instant::now();
            match event::read()? {
                Event::Resize(..) => {
                    let grid = grid_now()?;
                    if grid != app.grid {
                        app.resize(grid);
                        pictures.reset();
                        terminal.clear()?;
                        worker.request(grid.cols as u32 * grid.cw, grid.rows as u32 * grid.ch)?;
                    }
                }
                Event::Key(key) => app.key(key, now),
                Event::Mouse(mouse) => app.mouse(mouse, now),
                Event::Paste(text) => app.paste(&text, now),
                _ => {}
            }
        }
        if app.quit || !animating {
            return Ok(());
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::Item;

    fn app() -> App {
        let grid = Grid { cols: 120, rows: 36, cw: 10, ch: 20 };
        App::new(grid, Instant::now(), Path::new("/tmp"))
    }

    #[test]
    fn plan_is_visible_in_the_feed_and_the_acceptance_dialog() {
        let mut app = app();
        let now = Instant::now();
        let plan = (0..40).map(|n| format!("Step {n}\n")).collect::<String>();
        app.on_core(&json!({"type": "plan", "markdown": plan}), now);
        assert!(!app.session.items.is_empty());
        app.on_core(&json!({"type": "input_request", "request_id": "plan", "kind": "choice",
            "title": "Accept this plan?", "options": [
                {"value": "accept", "label": "Accept"}, {"value": "revise", "label": "Revise"}
            ]}), now);
        let Some(Modal::Ask(Some(ask))) = &app.modal else { panic!("missing dialog") };
        assert_eq!(ask.text, plan);
        app.dialog_rect = layout::dialog(&app.grid, view::ask_height(ask, app.grid.cols));
        app.ask_key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE), now);
        let Some(Modal::Ask(Some(ask))) = &app.modal else { panic!("missing dialog") };
        assert_eq!(ask.text_scroll, 6);
        for _ in 0..20 { app.ask_key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE), now); }
        let Some(Modal::Ask(Some(ask))) = &app.modal else { panic!("missing dialog") };
        assert_eq!(ask.text_scroll, view::ask_text_max_scroll(ask, app.dialog_rect));
    }

    fn journal() -> History {
        let events = [
            json!({"type": "user", "text": "fix it"}),
            json!({"type": "usage", "kind": "turn"}),
            json!({"type": "step", "step": 1, "call_id": "c1", "action_type": "read_file", "user_message": ""}),
            json!({"type": "final", "message": "Done."}),
        ];
        History { events: events.to_vec(), has_more: false }
    }

    #[test]
    fn a_switched_to_session_is_redrawn_from_its_journal() {
        let mut app = app();
        app.show_history(journal(), Instant::now());
        assert!(!app.session.busy);
        assert!(matches!(&app.session.items[..], [Item::User(_), Item::Tool { .. }, Item::Agent { .. }]));
        // The cut journal never reported the call's result.
        assert!(matches!(&app.session.items[1], Item::Tool { state: crate::model::CallState::Done { ok: false, .. }, .. }));
    }

    fn typing(app: &mut App, text: &str) {
        for c in text.chars() {
            app.key(KeyEvent::new(KeyCode::Char(c), KeyModifiers::NONE), Instant::now());
        }
    }

    fn press(app: &mut App, code: KeyCode) {
        app.key(KeyEvent::new(code, KeyModifiers::NONE), Instant::now());
    }

    fn main_app() -> App {
        let mut app = app();
        app.phase = Phase::Main { since: Instant::now() };
        app
    }

    #[test]
    fn the_arrows_choose_a_command_and_tab_completes_its_name() {
        let mut app = main_app();
        typing(&mut app, "/s");
        assert_eq!(app.menu().len(), 4);
        press(&mut app, KeyCode::Down);
        press(&mut app, KeyCode::Tab);
        assert_eq!(app.input, "/style ");
        assert_eq!(app.cursor, 7);
        assert!(app.menu().is_empty());
    }

    #[test]
    fn the_arrows_wrap_round_the_list() {
        let mut app = main_app();
        typing(&mut app, "/s");
        press(&mut app, KeyCode::Up);
        assert_eq!(app.chosen(), 3);
        press(&mut app, KeyCode::Down);
        assert_eq!(app.chosen(), 0);
    }

    #[test]
    fn typing_more_returns_the_choice_to_the_first_row() {
        let mut app = main_app();
        typing(&mut app, "/s");
        press(&mut app, KeyCode::Down);
        typing(&mut app, "e");
        assert_eq!(app.chosen(), 0);
    }

    #[test]
    fn enter_completes_a_command_that_needs_an_argument_and_runs_the_others() {
        let mut app = main_app();
        typing(&mut app, "/ren");
        press(&mut app, KeyCode::Enter);
        assert_eq!(app.input, "/rename ");
        let mut app = main_app();
        typing(&mut app, "/ne");
        press(&mut app, KeyCode::Enter);
        // Nothing is connected in the test, so the command is refused after it was taken from the box.
        assert!(app.input.is_empty());
        assert!(app.toast.is_some());
    }

    #[test]
    fn up_and_down_walk_the_rows_of_a_multi_line_input() {
        let mut app = main_app();
        app.layout.input = Rect::new(10, 20, 80, 5);
        typing(&mut app, "ab");
        app.key(KeyEvent::new(KeyCode::Enter, KeyModifiers::ALT), Instant::now());
        typing(&mut app, "cd");
        press(&mut app, KeyCode::Up);
        assert_eq!(app.cursor, 2);
        press(&mut app, KeyCode::Up);
        assert_eq!(app.cursor, 0);
        press(&mut app, KeyCode::Down);
        assert_eq!(app.cursor, 3);
        press(&mut app, KeyCode::Down);
        assert_eq!(app.cursor, 5);
    }

    #[test]
    fn up_recalls_what_was_sent_and_down_returns_to_the_draft() {
        let mut app = main_app();
        app.layout.input = Rect::new(10, 20, 80, 5);
        app.history = vec!["first".into(), "second".into()];
        typing(&mut app, "dra");
        press(&mut app, KeyCode::Up); // to the start of the draft
        assert_eq!((app.input.as_str(), app.cursor), ("dra", 0));
        press(&mut app, KeyCode::Up);
        assert_eq!(app.input, "second");
        press(&mut app, KeyCode::Up);
        assert_eq!(app.input, "first");
        press(&mut app, KeyCode::Up);
        assert_eq!(app.input, "first");
        press(&mut app, KeyCode::Down);
        assert_eq!(app.input, "second");
        press(&mut app, KeyCode::Down);
        assert_eq!(app.input, "dra");
        assert_eq!(app.recall, None);
    }

    #[test]
    fn up_in_an_empty_box_recalls_the_last_input_and_typing_ends_the_recall() {
        let mut app = main_app();
        app.layout.input = Rect::new(10, 20, 80, 5);
        app.history = vec!["fix the tests".into()];
        press(&mut app, KeyCode::Up);
        assert_eq!(app.input, "fix the tests");
        typing(&mut app, "!");
        assert_eq!(app.recall, None);
        assert_eq!(app.input, "fix the tests!");
    }

    #[test]
    fn a_recalled_command_keeps_walking_the_history_instead_of_the_menu() {
        let mut app = main_app();
        app.layout.input = Rect::new(10, 20, 80, 5);
        app.history = vec!["older".into(), "/style".into()];
        press(&mut app, KeyCode::Up);
        assert_eq!(app.input, "/style");
        press(&mut app, KeyCode::Up);
        assert_eq!(app.input, "older");
    }

    fn press_with(app: &mut App, code: KeyCode, modifiers: KeyModifiers) {
        app.key(KeyEvent::new(code, modifiers), Instant::now());
    }

    #[test]
    fn alt_and_ctrl_backspace_delete_the_word_before_the_cursor() {
        for modifiers in [KeyModifiers::ALT, KeyModifiers::CONTROL] {
            let mut app = main_app();
            typing(&mut app, "fix the тесты");
            press_with(&mut app, KeyCode::Backspace, modifiers);
            assert_eq!((app.input.as_str(), app.cursor), ("fix the ", 8));
            press_with(&mut app, KeyCode::Backspace, modifiers);
            assert_eq!((app.input.as_str(), app.cursor), ("fix ", 4));
        }
    }

    #[test]
    fn deleting_a_word_in_the_middle_keeps_the_rest_and_ends_a_recall() {
        let mut app = main_app();
        app.history = vec!["one two three".into()];
        press(&mut app, KeyCode::Up);
        assert!(app.recall.is_some());
        app.cursor = 7;
        press_with(&mut app, KeyCode::Backspace, KeyModifiers::ALT);
        assert_eq!((app.input.as_str(), app.cursor, app.recall), ("one  three", 4, None));
    }

    #[test]
    fn alt_and_ctrl_delete_remove_the_word_after_the_cursor() {
        let mut app = main_app();
        typing(&mut app, "one two three");
        app.cursor = 4;
        press_with(&mut app, KeyCode::Delete, KeyModifiers::CONTROL);
        assert_eq!((app.input.as_str(), app.cursor), ("one  three", 4));
    }

    #[test]
    fn alt_and_ctrl_arrows_jump_by_word() {
        for modifiers in [KeyModifiers::ALT, KeyModifiers::CONTROL] {
            let mut app = main_app();
            typing(&mut app, "один два три");
            press_with(&mut app, KeyCode::Left, modifiers);
            assert_eq!(app.cursor, 9);
            press_with(&mut app, KeyCode::Left, modifiers);
            assert_eq!(app.cursor, 5);
            press_with(&mut app, KeyCode::Right, modifiers);
            assert_eq!(app.cursor, 8);
            press_with(&mut app, KeyCode::Right, modifiers);
            assert_eq!(app.cursor, 12);
            press_with(&mut app, KeyCode::Right, modifiers);
            assert_eq!(app.cursor, 12);
        }
    }

    #[test]
    fn alt_b_and_alt_f_are_the_readline_spelling_and_type_nothing() {
        let mut app = main_app();
        typing(&mut app, "ab cd");
        press_with(&mut app, KeyCode::Char('b'), KeyModifiers::ALT);
        assert_eq!(app.cursor, 3);
        press_with(&mut app, KeyCode::Char('f'), KeyModifiers::ALT);
        assert_eq!((app.cursor, app.input.as_str()), (5, "ab cd"));
    }

    #[test]
    fn a_session_the_person_already_typed_into_keeps_its_own_feed() {
        let mut app = app();
        app.session.submit("new request".into(), Instant::now());
        app.show_history(journal(), Instant::now());
        assert_eq!(app.session.items.len(), 1);
    }
}
