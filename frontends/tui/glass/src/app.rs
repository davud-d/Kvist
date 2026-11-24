// The interface's state and its loop: core events in, keyboard and mouse in,
// cells drawn by ratatui, pictures placed through kitty. The core runs as a
// child process speaking `--frontend stdio-multi` (core.rs).

use std::io::{self, Write};
use std::path::Path;
use std::sync::Arc;
use std::sync::mpsc::{Receiver, TryRecvError};
use std::time::{Duration, Instant};

use crossterm::event::{self, Event, KeyCode, KeyEvent, KeyEventKind, KeyModifiers, MouseButton, MouseEvent, MouseEventKind};
use crossterm::terminal;
use ratatui::Terminal;
use ratatui::backend::CrosstermBackend;
use ratatui::layout::Rect;
use serde_json::{Value, json};

use crate::anim::{self, Ease, Tween};
use crate::arena::{Arena, Council};
use crate::attach::{self, Attachment};
use crate::code::CodeBlock;
use crate::backdrop::{LEVELS, WebFrame};
use crate::core::{self, Core, History, Message};
use crate::decision::{Decision, Page};
use crate::dialog::{Ask, Body, Choice};
use crate::execlog::{ExecLog, LogState};
use crate::diff;
use crate::edit;
use crate::kitty::Gpu;
use crate::layout::{self, BROWSER_ASPECT, Grid, Layout, MARGIN_X, Shape};
use crate::model::{CoreEvent, Session};
use crate::models::ModelPicker;
use crate::pictures::{
    Backdrop, CardSpec, CardStyle, Pictures, PopupSpec, SLOT_BROWSER_CARD, SLOT_DIALOG, SLOT_DIFF, SLOT_HELPERS,
    DEVELOP_STEP_MS, DEVELOP_STEPS, SLOT_ATTACH, SLOT_INPUT, SLOT_LEFT, SLOT_MENU, SLOT_READING, Scene, ThumbSpec, WaterSpec, Worker,
};
use crate::probe::Caps;
use crate::protocol;
use crate::screen::{Feed, load_image};
use crate::screens;
use crate::slash::{self, Command, Enter};
use crate::strings::{self, strings};
use crate::term;
use crate::view::{self, Hit};
use crate::water::{self, Pos, Pour, TextView};

/// The photograph stays alone on screen at least this long.
/// Columns a long dialog line pans per ←/→ press.
const PAN_STEP: i32 = 8;
const SPLASH_MIN: Duration = Duration::from_millis(2400);
/// How long it takes to rise from black.
const RISE: Duration = Duration::from_millis(800);
/// A replayed journal counts as shown this long ago, so its answers appear at
/// once instead of typing out again.
const LONG_AGO: Duration = Duration::from_secs(3600);
/// The backdrop keeps moving even while the interface itself is idle.
const IDLE_FRAME: Duration = crate::video::FRAME_TIME;
/// Shorter windows keep the input card as it is: the strip would not leave room for the conversation.
const MIN_ROWS_FOR_STRIP: u16 = 24;
/// How long a notice stays in the status row.
const TOAST: Duration = Duration::from_millis(4500);
/// The request id of `/tui`, a dialog answered here and never sent to the core.
const FRONTEND_REQUEST: &str = "local:frontend";
/// Exit status that asks the launcher to start another interface.
const SWITCH_FRONTEND: u8 = 64;
/// A second Esc within this time stops the running turn.
const STOP_CONFIRM: Duration = Duration::from_secs(3);
/// A second Ctrl+C within the notice's life leaves.
const EXIT_CONFIRM: Duration = TOAST;
/// How long a finished write's code block stays before it folds.
const CODE_LINGER: Duration = Duration::from_secs(2);
/// Two presses on the same cell this close are a double click (a word), three a line.
const MULTI_CLICK: Duration = Duration::from_millis(400);
/// While a drag stays above or below the text, it scrolls one line this often.
const DRAG_SCROLL: Duration = Duration::from_millis(45);
/// Seconds the caustics of a pool keep drifting.
const CAUSTIC_LIFE: f32 = 2.5;
/// Core commands that are whole turns: the core streams the turn's events, then `turn_end`, then
/// `command_result`. The input stays shut and Esc stops the turn until the result comes.
const TURN_COMMANDS: [&str; 2] = ["best-of", "council"];

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
    /// An attached picture opened to full size, by the N of its marker.
    Image(u32),
    /// The question a best-of run ends with: the verdict table and four actions.
    Decision(Decision),
}

/// What stands in the conversation's place.
pub enum Pane<'a> {
    Log(&'a ExecLog),
    Arena(&'a Arena),
    Council(&'a Council),
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
    /// The newest frame of that feed already shown.
    feed_version: u64,
    /// Every screen the agent has open (browser, app screen), the one announced last at the end: it owns the card.
    screens: Vec<(String, CoreEvent)>,
    /// The core's page for the agent's screen: a real browser shows it at 30-60 fps, the card at about one.
    screen_url: String,
    /// A picture is being drawn or decoded: the card shows a spinner until it arrives.
    pub image_pending: bool,
    /// When the latest picture arrived; it develops from blurred and dark.
    developed_at: Option<Instant>,
    /// A saved picture being decoded for the browser's card.
    image_in: Option<Receiver<Result<Arc<WebFrame>, String>>>,
    /// Versions of saved pictures, above any a browser feed reaches, so the
    /// card's textures are never mistaken for a browser frame's.
    image_version: u64,
    /// The browser closed: its pictures are to be dropped.
    web_closed: bool,
    /// A core on a server that another frontend drives: its notice takes the input's place and Enter takes over.
    pub observer: Option<String>,
    /// The link to that server while it is down or coming back.
    pub remote: Option<String>,
    /// Width / height of the browser's latest frame.
    pub web_aspect: Option<f32>,
    /// When Esc was pressed once during a turn.
    stop_armed: Option<Instant>,
    /// Why the core is not available, with where its log is.
    pub core_error: Option<String>,
    pub agent: Agent,
    /// The slash command the core is running.
    command: Option<String>,
    /// An executor's journal opened from the arena, read only.
    log: Option<ExecLog>,
    /// The arena's row the keys are on.
    pub arena_row: usize,
    /// The journal of the session just switched to, on its way.
    replay: Option<Receiver<Result<History, String>>>,
    pub toast: Option<Toast>,
    pub input: String,
    pub cursor: usize,
    /// Pictures pasted into the input; each is named by an `[Image #N]` marker in it.
    attachments: Vec<Attachment>,
    next_image: u32,
    /// 0 while no picture is attached, 1 once the strip over the input has opened.
    pub strip_in: Tween,
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
    /// Where the core works when it is on a server: the machine's name, a colon, the folder there. Empty on this computer.
    pub location: String,
    pub branch: String,
    pub left_in: Tween,
    pub input_pos: Tween,
    pub input_rise: Tween,
    pub helpers_in: Tween,
    pub browser_in: Tween,
    /// The file whose diff is open on the right; it stays until the panel has slid out.
    pub diff: Option<diff::View>,
    pub diff_in: Tween,
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
    /// The pointer is over the conversation's text.
    over_text: bool,
    hand_sent: Option<(bool, bool)>,
    /// The active session and its title, for the window name.
    session_id: String,
    session_title: String,
    title_sent: Option<String>,
    hits: Vec<(Rect, Hit)>,
    reading: Option<Rect>,
    /// The water poured on the conversation by the left button, if any.
    pour: Option<Pour>,
    /// The conversation as the last frame drew it.
    text_view: Option<TextView>,
    /// The writes being typed (or just typed), shown as code blocks in the conversation.
    pub code: Vec<CodeBlock>,
    code_rects: Vec<(Rect, usize)>,
    fold_rects: Vec<(Rect, crate::calls::Fold)>,
    /// How far the left card's list is scrolled; `None` follows its end, where the newest file is.
    left_scroll: Option<usize>,
    /// While the scrollbar's thumb is held: where in the thumb it was grabbed.
    pub left_drag: Option<usize>,
    widths: Vec<usize>,
    plain: Vec<String>,
    /// The display formulas showing now, which the pictures draw over their reserved rows.
    formulas: Vec<view::FormulaSpot>,
    /// The pool to draw this frame.
    water: Option<water::Shape>,
    /// The last press: when, where, and how many in a row.
    last_press: Option<(Instant, Pos, u8)>,
    last_drag_scroll: Instant,
    /// Text on its way to the clipboard.
    clipboard: Option<String>,
    pub scroll: usize,
    pub layout: Layout,
    quit: bool,
    /// When Ctrl+C asked for a second press on the empty input; the second one leaves.
    quit_armed: Option<Instant>,
    /// What the program exits with: 0, or 64 to start another interface.
    exit_code: u8,
    /// Everything sent to the core, for tests that have no core to receive it.
    #[cfg(test)]
    sent: Vec<Value>,
}

impl App {
    pub(crate) fn new(grid: Grid, now: Instant, workspace: &Path) -> Self {
        App {
            grid,
            started: now,
            phase: Phase::Splash,
            canvas_at: None,
            session: Session::default(),
            core: None,
            feed: None,
            feed_version: 0,
            screens: Vec::new(),
            screen_url: String::new(),
            image_in: None,
            image_pending: false,
            developed_at: None,
            image_version: 1 << 40,
            web_closed: false,
            observer: None,
            remote: None,
            web_aspect: None,
            stop_armed: None,
            core_error: None,
            agent: Agent::default(),
            command: None,
            log: None,
            arena_row: 0,
            replay: None,
            toast: None,
            input: String::new(),
            cursor: 0,
            attachments: Vec::new(),
            next_image: 1,
            strip_in: Tween::settled(0.0),
            history: Vec::new(),
            history_in: None,
            recall: None,
            draft: String::new(),
            pick: 0,
            menu_in: Tween::settled(0.0),
            last_typed: now,
            workspace: workspace_label(workspace),
            location: String::new(),
            branch: git_branch(workspace).unwrap_or_default(),
            left_in: Tween::settled(0.0),
            input_pos: Tween::settled(0.0),
            input_rise: Tween::settled(0.0),
            helpers_in: Tween::settled(0.0),
            browser_in: Tween::settled(0.0),
            diff: None,
            diff_in: Tween::settled(0.0),
            pending_plan: None,
            modal: None,
            modal_p: Tween::settled(0.0),
            dialog_rect: Rect::default(),
            helper_focus: None,
            hover: None,
            last_mouse: (0, 0),
            hand: false,
            over_text: false,
            hand_sent: None,
            session_id: String::new(),
            session_title: String::new(),
            title_sent: None,
            hits: Vec::new(),
            reading: None,
            pour: None,
            text_view: None,
            code: Vec::new(),
            code_rects: Vec::new(),
            fold_rects: Vec::new(),
            left_scroll: None,
            left_drag: None,
            widths: Vec::new(),
            plain: Vec::new(),
            formulas: Vec::new(),
            water: None,
            last_press: None,
            last_drag_scroll: now,
            clipboard: None,
            scroll: 0,
            layout: Layout::default(),
            quit: false,
            quit_armed: None,
            exit_code: 0,
            #[cfg(test)]
            sent: Vec::new(),
        }
    }

    /// Content rows of the left card: the changed files, then the helpers.
    pub fn left_rows(&self) -> u16 {
        let files = self.session.files.len() as u16;
        let helpers = self.session.helpers.len() as u16;
        let mut rows = if files > 0 { files + 1 } else { 0 };
        if helpers > 0 {
            rows += helpers + 1 + u16::from(files > 0);
        }
        rows
    }

    pub fn left_offset(&self, max: usize) -> usize {
        self.left_scroll.map_or(max, |at| at.min(max))
    }

    fn set_left_scroll(&mut self, at: usize, max: usize) {
        self.left_scroll = (at < max).then_some(at);
    }

    /// A press on the left card's scrollbar: the thumb is taken where it is, the track pulls it to the pointer.
    fn press_left_bar(&mut self, hit: Option<Hit>, row: u16) -> bool {
        let (Some(scroll), Some(card)) = (view::left_scroll(self), self.layout.left) else { return false };
        let top = card.y as usize + 1 + scroll.pinned;
        let (height, offset) = view::thumb(scroll, self.left_offset(scroll.max));
        match hit {
            Some(Hit::LeftThumb) => self.left_drag = Some((row as usize).saturating_sub(top + offset)),
            Some(Hit::LeftTrack) => {
                self.left_drag = Some(height / 2);
                self.drag_left_bar(row);
            }
            _ => return false,
        }
        true
    }

    fn drag_left_bar(&mut self, row: u16) {
        let (Some(scroll), Some(card), Some(grab)) = (view::left_scroll(self), self.layout.left, self.left_drag) else { return };
        let (height, _) = view::thumb(scroll, 0);
        let track = scroll.view.saturating_sub(height);
        if track == 0 {
            return;
        }
        let top = card.y as usize + 1 + scroll.pinned;
        let place = (row as usize).saturating_sub(top + grab).min(track);
        self.set_left_scroll((place * scroll.max + track / 2) / track, scroll.max);
    }

    fn shape(&self, input_lines: u16, now: Instant) -> Shape {
        Shape {
            left_rows: self.left_rows(),
            browser: self.session.browser.as_ref().map(|_| self.web_aspect.unwrap_or(if self.image_pending { 1.0 } else { BROWSER_ASPECT })),
            input_lines,
            input_at_bottom: self.input_pos.value(now),
            diff: self.diff.is_some(),
            attach_rows: self.strip_rows(now),
        }
    }

    fn relayout(&mut self, now: Instant) {
        let mut shape = self.shape(1, now);
        let first = layout::compute(&self.grid, &shape);
        let (lines, _) = view::input_lines(&self.input, self.cursor, first.input.width.saturating_sub(6) as usize);
        shape.input_lines = lines.len() as u16;
        self.layout = layout::compute(&self.grid, &shape);
        self.sync_diff();
        let width = self.layout.transcript.width.saturating_sub(6) as usize;
        for block in &mut self.code {
            block.rebuild(width);
        }
        match &self.modal {
            Some(Modal::Ask(Some(ask))) => {
                self.dialog_rect = layout::dialog(&self.grid, view::ask_width(ask), view::ask_height(ask, self.grid.cols));
            }
            Some(Modal::Models(_)) => self.dialog_rect = layout::dialog(&self.grid, layout::DIALOG_WIDTH, view::MODELS_HEIGHT),
            Some(Modal::Decision(decision)) => {
                let width = layout::dialog(&self.grid, screens::DECISION_WIDTH, 1).width;
                self.dialog_rect = layout::dialog(&self.grid, screens::DECISION_WIDTH, screens::decision_height(decision, width));
            }
            _ => {}
        }
    }

    /// Builds the open diff's rows for the panel's width. A panel that does
    /// not fit (the window narrowed) is dropped.
    fn sync_diff(&mut self) {
        let Some(view) = self.diff.as_mut() else { return };
        let Some(panel) = self.layout.diff else {
            self.diff = None;
            return;
        };
        let patches = self.session.files.iter().find(|f| f.path == view.path).map_or(&[][..], |f| f.patches.as_slice());
        view.sync(patches, panel.width.saturating_sub(4) as usize, strings().diff_cut);
    }

    /// Rows of the open diff's text: the panel without its header, footer and padding.
    pub fn diff_rows(&self) -> usize {
        self.layout.diff.map_or(0, |panel| panel.height.saturating_sub(5) as usize)
    }

    fn tick(&mut self, now: Instant, pictures: &mut Pictures) {
        self.poll_core(now);
        self.poll_replay(now);
        self.poll_log(now);
        self.poll_input_history();
        self.sync_menu(now);
        self.sync_strip(now);
        self.poll_feed(now, pictures);
        self.session.tick(now);
        if matches!(self.phase, Phase::Splash) && self.splash_over(now, pictures) {
            self.phase = Phase::Main { since: now };
            self.input_rise.go(1.0, now, 600, Ease::Burst);
            pictures.drop_rise();
        }
        if self.modal.is_some() && self.modal_p.target() == 0.0 && !self.modal_p.running(now) {
            self.modal = None;
        }
        self.fold_code(now);
        self.drop_closed_diff(now);
        self.keep_pouring(now);
        if self.toast.as_ref().is_some_and(|t| now.saturating_duration_since(t.at) > TOAST) {
            self.toast = None;
        }
    }

    /// The screen that stands in the conversation's place: an executor's log
    /// over the arena, the arena, or the council.
    pub fn pane(&self) -> Option<Pane<'_>> {
        if let Some(log) = &self.log {
            return Some(Pane::Log(log));
        }
        if let Some(arena) = &self.session.arena {
            return Some(Pane::Arena(arena));
        }
        self.session.council.as_ref().map(Pane::Council)
    }

    /// Reads the journal that is on its way; a log of a run that has gone is dropped.
    fn poll_log(&mut self, now: Instant) {
        if self.session.arena.is_none() {
            self.log = None;
        }
        if let Some(log) = self.log.as_mut() {
            log.poll(now);
        }
    }

    /// Opens the read-only log of the arena's executor at `index`.
    fn open_log(&mut self, index: usize, now: Instant) {
        let Some(executor) = self.session.arena.as_ref().and_then(|arena| arena.executors.get(index)) else { return };
        if executor.session.is_empty() {
            return self.notify(strings().log_no_session.to_string(), true, now);
        }
        self.log = Some(ExecLog::open(&executor.id, &executor.style, &executor.session, self.core.as_ref()));
        self.scroll = 0;
    }

    fn close_log(&mut self) {
        self.log = None;
        self.scroll = 0;
    }

    /// Reads the open log again: its executor may have worked on since.
    fn refresh_log(&mut self, now: Instant) {
        let Some(id) = self.log.as_ref().map(|log| log.id.clone()) else { return };
        let Some(index) = self.session.arena.as_ref().and_then(|arena| arena.executors.iter().position(|e| e.id == id)) else { return };
        self.open_log(index, now);
    }

    /// Takes the arena or the council off the screen and gives the conversation back.
    fn dismiss_pane(&mut self) {
        self.session.clear_screens();
        self.log = None;
        self.arena_row = 0;
        self.scroll = 0;
    }

    /// Nothing can be typed: the session is closed, another frontend drives it, or the server is out of reach.
    pub fn input_locked(&self) -> bool {
        self.session.closed.is_some() || self.observer.is_some() || self.remote.is_some()
    }

    /// The commands the input is asking for; none while a dialog is open or
    /// the input is locked.
    pub fn menu(&self) -> Vec<&'static Command> {
        if self.modal.is_some() || self.input_locked() || !matches!(self.phase, Phase::Main { .. }) {
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

    /// The pictures whose marker is still in the input, in the order of the markers.
    pub fn attached(&self) -> Vec<&Attachment> {
        attach::markers(&self.input).into_iter().filter_map(|id| self.attachment(id)).collect()
    }

    pub fn attachment(&self, id: u32) -> Option<&Attachment> {
        self.attachments.iter().find(|a| a.id == id)
    }

    /// The rows the strip adds to the input card right now; none where the
    /// window is too short to spare them.
    fn strip_rows(&self, now: Instant) -> u16 {
        if self.grid.rows < MIN_ROWS_FOR_STRIP {
            return 0;
        }
        (layout::STRIP_ROWS as f32 * self.strip_in.value(now)).round() as u16
    }

    /// The pictures over the input once the strip has opened, each with its
    /// place, and how many did not fit.
    pub fn strip(&self, now: Instant) -> (Vec<(Rect, &Attachment)>, usize) {
        let open = self.strip_rows(now) == layout::STRIP_ROWS;
        if !open || self.input_locked() {
            return (Vec::new(), 0);
        }
        let attached = self.attached();
        let aspects: Vec<f32> = attached.iter().map(|a| a.aspect).collect();
        let (rects, hidden) = layout::strip(self.layout.input, &self.grid, &aspects);
        (rects.into_iter().zip(attached).collect(), hidden)
    }

    /// Opens the strip when a picture is attached and closes it when the last
    /// marker is gone; pictures whose marker is nowhere (not even in the
    /// draft a walk through the history keeps) are forgotten.
    fn sync_strip(&mut self, now: Instant) {
        let wanted = !self.attached().is_empty();
        if wanted != (self.strip_in.target() > 0.0) {
            self.strip_in.go(if wanted { 1.0 } else { 0.0 }, now, if wanted { 260 } else { 180 }, Ease::OutCubic);
        }
        let kept: Vec<u32> = attach::markers(&self.input).into_iter().chain(attach::markers(&self.draft)).collect();
        self.attachments.retain(|a| kept.contains(&a.id));
    }

    fn splash_over(&self, now: Instant, pictures: &Pictures) -> bool {
        let risen = self.canvas_at.is_some_and(|at| now.saturating_duration_since(at) >= RISE);
        let long_enough = now.saturating_duration_since(self.started) >= SPLASH_MIN;
        self.session.ready && risen && long_enough && pictures.has_canvas_for(&self.grid)
    }

    /// Applies one session event, starting the motion it causes.
    fn on_session(&mut self, event: CoreEvent, now: Instant) {
        let Some(event) = self.route_screen(event) else { return };
        match &event {
            CoreEvent::FileChanged { .. } | CoreEvent::Helper { .. } => self.left_in.go(1.0, now, 480, Ease::Burst),
            CoreEvent::Screen { stream_url, url, .. } => {
                self.screen_url = url.clone();
                if self.feed.as_ref().is_none_or(|feed| feed.url != *stream_url) {
                    match Feed::start(stream_url) {
                        Ok(feed) => {
                            self.feed = Some(feed);
                            self.feed_version = 0;
                        }
                        Err(error) => self.notify(error, true, now),
                    }
                }
                self.image_pending = false;
                self.developed_at = None;
                self.browser_in.go(1.0, now, 520, Ease::OutBack);
            }
            CoreEvent::ImageDrawing { .. } => {
                self.feed = None;
                self.image_in = None;
                // The last picture goes; the card waits for the new one.
                self.web_closed = true;
                self.image_pending = true;
                self.developed_at = None;
                self.browser_in.go(1.0, now, 520, Ease::OutBack);
            }
            CoreEvent::ImageFailed { error } => {
                self.image_pending = false;
                self.web_closed = true;
                self.browser_in = Tween::settled(0.0);
                self.notify(error.clone(), true, now);
            }
            CoreEvent::Image { path, .. } => {
                // The picture takes the card from a live browser feed, whose
                // next frame would otherwise cover it.
                self.feed = None;
                self.image_pending = true;
                self.image_in = Some(load_image(path.clone(), self.image_version));
                self.image_version += 1;
                self.browser_in.go(1.0, now, 520, Ease::OutBack);
            }
            CoreEvent::ScreenClosed { .. } => {
                self.feed = None;
                self.image_in = None;
                self.image_pending = false;
                self.web_closed = true;
                self.browser_in = Tween::settled(0.0);
                if matches!(self.modal, Some(Modal::Browser)) {
                    self.close_modal(now);
                }
            }
            CoreEvent::CallStream { call, tool, path, field, line, text } => self.stream_code(call, tool, path, field, *line, text, now),
            CoreEvent::Result { call, .. } => self.finish_code(Some(call), now),
            CoreEvent::TurnEnd => {
                // A resumed run ends with `turn_end` alone, no `command_result`.
                if self.command.as_deref() == Some("resume") {
                    self.command = None;
                }
                self.finish_code(None, now);
            }
            _ => {}
        }
        self.session.apply(event, now);
    }

    /// The card shows one screen at a time. A screen announced last takes it; when the one on show closes and
    /// another is still open, the card goes to that one instead of closing; a screen that is not on show may
    /// close without the card noticing.
    fn route_screen(&mut self, event: CoreEvent) -> Option<CoreEvent> {
        match event {
            CoreEvent::Screen { ref screen_id, .. } => {
                let id = screen_id.clone();
                self.screens.retain(|(known, _)| *known != id);
                self.screens.push((id, event.clone()));
                Some(event)
            }
            CoreEvent::ScreenClosed { screen_id } => {
                let on_show = self.screens.last().is_some_and(|(known, _)| *known == screen_id);
                self.screens.retain(|(known, _)| *known != screen_id);
                match self.screens.last() {
                    Some((_, next)) if on_show => Some(next.clone()),
                    Some(_) => None,
                    None => Some(CoreEvent::ScreenClosed { screen_id }),
                }
            }
            other => Some(other),
        }
    }

    fn poll_feed(&mut self, now: Instant, pictures: &mut Pictures) {
        if std::mem::take(&mut self.web_closed) {
            pictures.set_web(None);
        }
        let mut failure = None;
        if let Some(incoming) = self.image_in.as_ref() {
            match incoming.try_recv() {
                Err(TryRecvError::Empty) => {}
                Ok(Ok(frame)) => {
                    pictures.set_web(Some(frame));
                    self.image_in = None;
                    self.image_pending = false;
                    self.developed_at = Some(now);
                }
                Ok(Err(error)) => {
                    failure = Some(error);
                    self.image_in = None;
                    self.image_pending = false;
                }
                Err(TryRecvError::Disconnected) => {
                    self.image_in = None;
                    self.image_pending = false;
                }
            }
        }
        if let Some(feed) = self.feed.as_ref() {
            while let Ok(frame) = feed.frames.try_recv() {
                match frame {
                    // Frames are decoded by several threads, so one can arrive after a newer one.
                    Ok(frame) if frame.version > self.feed_version => {
                        self.feed_version = frame.version;
                        pictures.set_web(Some(frame));
                    }
                    Ok(_) => {}
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
        // A run of the past has nothing left to watch or decide; its words are in the conversation.
        self.session.clear_screens();
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
            if matches!(self.modal, Some(Modal::Ask(_) | Modal::Decision(_))) {
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
                // A core on a server says where it works (`machine:/folder`); this screen's own folder is only a stand-in.
                self.location = text("location");
                self.take_models(event.get("models"));
            }
            "manifest" => self.take_plugins(event, now),
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
            "session" => {
                self.session_id = text("session");
                self.session_title = text("title");
                // A closed session still reports its end through the common translation.
                for change in protocol::translate(event) {
                    self.on_session(change, now);
                }
            }
            // A title can arrive for a session the person has already left.
            "session_title" => {
                if self.session_id.is_empty() || text("session") == self.session_id {
                    self.session_title = text("title");
                }
            }
            // The driver's to answer; a frontend that takes over is sent the ones still open.
            "input_request" if self.observer.is_some() => {}
            "input_request" => self.on_request(event, now),
            "control" => self.observer = (text("role") == "observer").then(|| text("notice")),
            "remote_state" => self.remote = (text("state") != "connected").then(|| remote_notice(event)),
            "command_result" => self.on_command_result(event, now),
            // An error outside a turn (a failed command) is only a notice.
            "error" if !self.session.busy => self.notify(text("message"), true, now),
            // A word for the person that is not part of the conversation.
            "notice" => self.notify(text("text"), text("level") != "info", now),
            _ => {
                for change in protocol::translate(event) {
                    self.on_session(change, now);
                }
            }
        }
    }

    fn take_plugins(&mut self, manifest: &Value, now: Instant) {
        let list = |key: &str| manifest.get(key).and_then(Value::as_array).cloned().unwrap_or_default();
        let field = |entry: &Value, key: &str| entry.get(key).and_then(Value::as_str).unwrap_or("").to_string();
        let plugins: Vec<(String, String)> = list("plugin_commands").iter().map(|entry| (field(entry, "name"), field(entry, "description"))).collect();
        slash::set_plugins(&plugins);
        let problems: Vec<String> = list("plugin_problems").iter().filter_map(|problem| problem.as_str().map(str::to_string)).collect();
        if !problems.is_empty() {
            self.notify(format!("{}: {}", strings().plugin_skipped, problems.join("; ")), true, now);
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

    /// Shows `modal` as the open dialog; one that follows another does not slide in again.
    fn open_dialog(&mut self, modal: Modal, now: Instant) {
        let opening = !matches!(self.modal, Some(Modal::Ask(_) | Modal::Decision(_))) || self.modal_p.target() == 0.0;
        self.modal = Some(modal);
        if opening {
            self.modal_p.go(1.0, now, 200, Ease::OutCubic);
        }
    }

    fn on_request(&mut self, event: &Value, now: Instant) {
        if let Some(decision) = self.session.arena.as_mut().and_then(|arena| Decision::from_request(event, arena)) {
            return self.open_dialog(Modal::Decision(decision), now);
        }
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
        self.open_dialog(Modal::Ask(Some(ask)), now);
    }

    fn on_command_result(&mut self, event: &Value, now: Instant) {
        // A command that failed before its turn began sends no `turn_end`.
        if self.command.take().is_some_and(|name| name == "resume" || TURN_COMMANDS.contains(&name.as_str())) {
            self.session.apply(CoreEvent::TurnEnd, now);
        }
        let ok = event.get("ok").and_then(Value::as_bool).unwrap_or(false);
        if event.get("name").and_then(Value::as_str) == Some("catalog") && ok {
            return self.open_catalog(event.get("effects"), now);
        }
        if matches!(self.modal, Some(Modal::Ask(_) | Modal::Models(_) | Modal::Decision(_))) {
            self.close_modal(now);
        }
        let effects = event.get("effects");
        if let Some(choice) = effects.and_then(|e| e.get("relaunch")).and_then(|r| r.get("switch")).and_then(Value::as_str)
            && !choice.is_empty()
        {
            return self.start_again(choice, now);
        }
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
        #[cfg(test)]
        self.sent.push(message.clone());
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
        self.start_command(name, args, now);
    }

    fn start_command(&mut self, name: &str, args: &[&str], now: Instant) {
        self.command = Some(name.to_string());
        self.send(&json!({"type": "command", "name": name, "args": args}), now);
        // `resume` is a turn the core starts without a request: it streams events, then `turn_end`.
        if name == "resume" {
            self.session.apply(CoreEvent::TurnStarted, now);
        }
        if TURN_COMMANDS.contains(&name) {
            let typed = std::iter::once(format!("/{name}")).chain(args.iter().map(|arg| arg.to_string())).collect::<Vec<_>>().join(" ");
            self.session.submit(typed, now);
            self.scroll = 0;
            self.input_pos.go(1.0, now, 540, Ease::Burst);
        }
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

    /// How far the latest picture has developed; a browser frame is always whole.
    fn develop_step(&self, now: Instant) -> u8 {
        self.developed_at.map_or(DEVELOP_STEPS, |at| {
            (now.saturating_duration_since(at).as_millis() / DEVELOP_STEP_MS).min(u128::from(DEVELOP_STEPS)) as u8
        })
    }

    fn animating(&self, now: Instant) -> bool {
        let tweens = [&self.left_in, &self.input_pos, &self.input_rise, &self.helpers_in, &self.browser_in, &self.diff_in, &self.modal_p, &self.menu_in, &self.strip_in];
        matches!(self.phase, Phase::Splash)
            || self.session.busy
            // The cache countdown in the hover tip.
            || self.hover == Some(Hit::Context)
            || self.command.is_some()
            || tweens.iter().any(|t| t.running(now))
            || self.session.thinking_moves(now)
            || self.session.folds.moving(now)
            || self.image_pending
            || self.log.as_ref().is_some_and(|log| matches!(log.state, LogState::Loading))
            || !self.code.is_empty()
            || self.develop_step(now) < DEVELOP_STEPS
            || self.pour.as_ref().is_some_and(|pour| pour.moving(now))
            || self.session.files.iter().any(|f| now.saturating_duration_since(f.touched) < Duration::from_millis(1700))
            || self.session.helpers.iter().any(|h| h.state == crate::model::HelperState::Running)
    }

    fn draw(&mut self, frame: &mut ratatui::Frame, now: Instant) {
        self.relayout(now);
        let drawn = view::draw(self, frame.buffer_mut(), now);
        self.hits = drawn.hits;
        self.reading = drawn.reading;
        self.text_view = drawn.text;
        self.code_rects = drawn.code;
        self.fold_rects = drawn.folds;
        self.widths = drawn.widths;
        self.plain = drawn.plain;
        self.formulas = drawn.formulas;
        self.wet_the_text(frame.buffer_mut(), now);
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
            return Scene { backdrop, cards: Vec::new(), thumb: None, attachments: Vec::new(), popup: None, water: None, formulas: Vec::new(), formulas_dim: false };
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
        let diff = self.diff_in.value(now);
        if let Some(rect) = self.layout.diff
            && diff > 0.001
        {
            cards.push(CardSpec { slot: SLOT_DIFF, style: style(CardStyle::Side), rect, dx: slide(rect, diff), dy: 0.0 });
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
        if let Some(Modal::Ask(_) | Modal::Models(_) | Modal::Decision(_)) = self.modal
            && modal > 0.05
        {
            let dy = ((1.0 - modal) * 1.4).round() * ch;
            cards.push(CardSpec { slot: SLOT_DIALOG, style: CardStyle::Dialog, rect: self.dialog_rect, dx: 0.0, dy });
        }

        let browser_open = matches!(self.modal, Some(Modal::Browser));
        let thumb = match (self.layout.thumb, &self.session.browser) {
            (Some(rect), Some(_)) if !(browser_open && self.modal_p.value(now) > 0.001) => {
                Some(ThumbSpec { rect, dx: browser_dx, dy: 0.0, frame: None, dim, develop: self.develop_step(now) })
            }
            _ => None,
        };
        let opened = match self.modal {
            Some(Modal::Image(id)) if self.modal_p.value(now) > 0.001 => Some(id),
            _ => None,
        };
        let input_dy = (1.0 - self.input_rise.value(now)) * 8.0 * ch;
        let attachments = self
            .strip(now)
            .0
            .into_iter()
            .enumerate()
            .filter(|(_, (_, attachment))| Some(attachment.id) != opened)
            .map(|(i, (rect, attachment))| {
                let spec = ThumbSpec { rect, dx: 0.0, dy: input_dy, frame: Some(attachment.frame.clone()), dim, develop: DEVELOP_STEPS };
                (SLOT_ATTACH + i as u32, spec)
            })
            .collect();
        let popup = match &self.modal {
            Some(Modal::Browser) => self.popup(now, self.web_aspect.unwrap_or(BROWSER_ASPECT), self.layout.thumb, None),
            Some(Modal::Image(id)) => self.attachment(*id).and_then(|a| {
                let from = self.strip(now).0.into_iter().find(|(_, shown)| shown.id == a.id).map(|(rect, _)| rect);
                self.popup(now, a.aspect, from, Some(a.frame.clone()))
            }),
            _ => None,
        };
        // The caustics drift while the pool is young and then hold still, so a
        // pool at rest costs no redrawing.
        let clock = self.pour.as_ref().map_or(0.0, |pour| pour.age(now).min(CAUSTIC_LIFE));
        let water = self.water.clone().map(|shape| WaterSpec { shape, clock });
        Scene { backdrop, cards, thumb, attachments, popup, water, formulas: self.formulas.clone(), formulas_dim: dim }
    }

    /// A picture growing from its thumbnail to the middle of the screen.
    fn popup(&self, now: Instant, aspect: f32, thumb: Option<Rect>, frame: Option<Arc<WebFrame>>) -> Option<PopupSpec> {
        let open = self.modal_p.value(now);
        if open <= 0.001 {
            return None;
        }
        let grid = &self.grid;
        let to = grid.px(layout::browser_full(grid, aspect));
        let from = thumb.map(|r| grid.px(r)).unwrap_or((to.0 + to.2 * 0.4, to.1 + to.3 * 0.4, to.2 * 0.2, to.3 * 0.2));
        let lerp = |a: f32, b: f32| a + (b - a) * open;
        let clamped = open.clamp(0.0, 1.0);
        let sharpness = if clamped < 0.45 { 0 } else if clamped < 0.8 { 1 } else { 2 };
        Some(PopupSpec { x: lerp(from.0, to.0), y: lerp(from.1, to.1), w: lerp(from.2, to.2), h: lerp(from.3, to.3), sharpness, frame })
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
            return self.interrupt(now);
        }
        if matches!(self.phase, Phase::Splash) {
            return;
        }
        match self.modal {
            Some(Modal::Ask(_)) => self.ask_key(key, now),
            Some(Modal::Decision(_)) => self.decision_key(key, now),
            Some(Modal::Models(_)) => self.models_key(key, now),
            Some(Modal::Browser | Modal::Image(_)) => {
                match key.code {
                    KeyCode::Esc => self.close_modal(now),
                    KeyCode::Char('o' | 'O') => self.open_screen_in_browser(now),
                    _ => {}
                }
            }
            None => self.main_key(key, now),
        }
    }

    fn open_screen_in_browser(&mut self, now: Instant) {
        if self.screen_url.is_empty() || !self.screen_url.starts_with("http://127.0.0.1:") {
            return;
        }
        let opener = if cfg!(target_os = "macos") { "open" } else { "xdg-open" };
        let started = std::process::Command::new(opener)
            .arg(&self.screen_url)
            .stdin(std::process::Stdio::null())
            .stdout(std::process::Stdio::null())
            .stderr(std::process::Stdio::null())
            .spawn();
        if let Err(error) = started {
            self.notify(format!("{opener}: {error}"), true, now);
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
            KeyCode::Char('v') if ctrl && !self.input_locked() => return self.paste_from_clipboard(now),
            KeyCode::Esc if self.pour.is_some() => return self.drain_water(now),
            KeyCode::Esc if self.diff_in.target() > 0.0 => return self.close_diff(now),
            KeyCode::Esc if self.log.is_some() => return self.close_log(),
            KeyCode::Esc if self.session.busy => return self.stop(now),
            KeyCode::Esc if self.helpers_in.target() > 0.0 => return self.toggle_helpers(None, now),
            KeyCode::Esc if self.pane().is_some() => return self.dismiss_pane(),
            KeyCode::Char('r') if ctrl && self.log.is_some() => return self.refresh_log(now),
            _ => {}
        }
        if self.input_locked() {
            if key.code == KeyCode::Enter {
                self.enter_on_banner(now);
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
        if !ctrl && !alt && self.input.is_empty() && self.pane_key(key.code, now) {
            return;
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
            KeyCode::Backspace if attach::marker_before(&self.input, self.cursor).is_some() => {
                let start = attach::marker_before(&self.input, self.cursor).unwrap_or(self.cursor);
                self.delete_chars(start, self.cursor, now);
            }
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

    /// Up, Down and Enter on the arena or its log while the input is empty;
    /// true when the key was theirs.
    fn pane_key(&mut self, code: KeyCode, now: Instant) -> bool {
        let rows = self.session.arena.as_ref().map_or(0, |arena| arena.executors.len());
        match (self.log.is_some(), code) {
            (true, KeyCode::Up) => self.scroll += 1,
            (true, KeyCode::Down) => self.scroll = self.scroll.saturating_sub(1),
            (false, KeyCode::Up) if rows > 0 => self.arena_row = self.arena_row.min(rows - 1).saturating_sub(1),
            (false, KeyCode::Down) if rows > 0 => self.arena_row = (self.arena_row + 1).min(rows - 1),
            (false, KeyCode::Enter) if rows > 0 => self.open_log(self.arena_row.min(rows - 1), now),
            _ => return false,
        }
        true
    }

    /// Keys of the decision dialog: rows, actions, the patch, and a letter for each action.
    fn decision_key(&mut self, key: KeyEvent, now: Instant) {
        let card = self.dialog_rect;
        let ctrl = key.modifiers.contains(KeyModifiers::CONTROL);
        let Some(Modal::Decision(decision)) = self.modal.as_mut() else { return };
        if decision.waiting {
            return;
        }
        let patch_page = decision.page == Page::Patch;
        let most = screens::patch_max_scroll(decision, card, strings());
        let reply = match key.code {
            KeyCode::Esc => {
                let cancel = decision.cancel();
                self.send(&cancel, now);
                return self.close_modal(now);
            }
            KeyCode::Enter => decision.accept(),
            KeyCode::Tab if decision.patch.is_some() => {
                decision.page = if patch_page { Page::Table } else { Page::Patch };
                None
            }
            KeyCode::Up | KeyCode::Down | KeyCode::PageUp | KeyCode::PageDown if patch_page => {
                let step: i32 = match key.code {
                    KeyCode::Up => -1,
                    KeyCode::Down => 1,
                    KeyCode::PageUp => -10,
                    _ => 10,
                };
                decision.scroll = (decision.scroll as i32 + step).clamp(0, most as i32) as usize;
                None
            }
            KeyCode::Up => {
                decision.step_row(-1);
                None
            }
            KeyCode::Down => {
                decision.step_row(1);
                None
            }
            KeyCode::Left => {
                decision.step_action(-1);
                None
            }
            KeyCode::Right => {
                decision.step_action(1);
                None
            }
            KeyCode::Char(c) if !ctrl => decision.hotkey(c).map(|action| decision.reply(action)),
            _ => None,
        };
        if let Some(reply) = reply {
            self.answer_decision(reply, now);
        }
    }

    /// Sends the decision. The core may ask again (after the diff), so while a
    /// command runs the dialog waits for the next question or the command's end.
    fn answer_decision(&mut self, reply: Value, now: Instant) {
        self.send(&reply, now);
        if self.command.is_none() {
            return self.close_modal(now);
        }
        if let Some(Modal::Decision(decision)) = self.modal.as_mut() {
            decision.waiting = true;
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
            KeyCode::Up | KeyCode::Down if ask.diff.is_some() => {
                let most = view::ask_text_max_scroll(ask, card);
                let delta = if key.code == KeyCode::Up { -1 } else { 1 };
                ask.text_scroll = (ask.text_scroll as i32 + delta).clamp(0, most as i32) as usize;
                None
            }
            KeyCode::Left | KeyCode::Right if matches!(ask.body, Body::Choice { .. }) => {
                let delta = if key.code == KeyCode::Left { -PAN_STEP } else { PAN_STEP };
                ask.hscroll = (ask.hscroll as i32 + delta).clamp(0, view::ask_pan_max(ask, card) as i32) as usize;
                None
            }
            KeyCode::Up => {
                ask.hscroll = 0;
                ask.step(-1);
                None
            }
            KeyCode::Down | KeyCode::Tab | KeyCode::Left | KeyCode::Right => {
                ask.hscroll = 0;
                ask.step(if key.code == KeyCode::Left { -1 } else { 1 });
                None
            }
            KeyCode::Backspace => {
                ask.hscroll = 0;
                ask.backspace();
                None
            }
            KeyCode::Char('u') if ctrl => {
                ask.hscroll = 0;
                ask.clear();
                None
            }
            KeyCode::Char(c) if !ctrl => {
                ask.hscroll = 0;
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
        // `/website` and the plugins are ordinary requests the core expands itself.
        let command = text.strip_prefix('/').filter(|command| !slash::is_goal(&slash::canonical(command.split_whitespace().next().unwrap_or(""))));
        if let Some(command) = command {
            let mut words = command.split_whitespace();
            let name = slash::canonical(words.next().unwrap_or(""));
            let args: Vec<&str> = words.collect();
            self.input.clear();
            self.cursor = 0;
            if self.run_local(&name, &args, now) {
                return;
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
        let goal = attach::pack(&text, &self.attached());
        self.send(&json!({"type": "goal", "text": goal}), now);
        self.session.submit(text, now);
        self.input.clear();
        self.cursor = 0;
        self.attachments.clear();
        self.next_image = 1;
        self.scroll = 0;
        self.input_pos.go(1.0, now, 540, Ease::Burst);
    }

    /// The commands this interface answers itself, never the core; false for any other.
    fn run_local(&mut self, name: &str, args: &[&str], now: Instant) -> bool {
        match name {
            // `/tui` belongs to the launcher: it picks the interface.
            "tui" => self.ask_frontend(now),
            "quit" => self.quit = true,
            "help" => self.session.note(slash::help_text()),
            "copy" => self.copy_answer(args, now),
            "clear" if self.session.busy => self.notify(strings().core_busy.to_string(), true, now),
            "clear" => {
                self.session.clear_view();
                self.code.clear();
                self.scroll = 0;
            }
            _ => return false,
        }
        true
    }

    /// `/copy [N]`: the agent's latest answer (or the N-th from the end) goes to the clipboard.
    fn copy_answer(&mut self, args: &[&str], now: Instant) {
        let from_end = args.first().and_then(|n| n.parse::<usize>().ok()).unwrap_or(1).max(1);
        let Some(text) = self.session.answer_from_end(from_end).map(str::to_string) else {
            return self.notify(strings().no_answer.to_string(), true, now);
        };
        self.clipboard = Some(text);
        self.notify(strings().copied.to_string(), false, now);
    }

    /// `/tui`: the terminal interfaces the launcher can start, this one first
    /// highlighted. The same list as the Ink app's (frontends/tui/ink/commands.ts).
    fn ask_frontend(&mut self, now: Instant) {
        let s = strings();
        let choices = [
            ("glass", "Classic", s.frontend_glass),
            ("tui", "Ink", s.frontend_ink),
            ("claude", "Claude-style", s.frontend_claude),
            ("opencode", "OpenCode", s.frontend_opencode),
            ("voice", "Voice", s.frontend_voice),
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
        self.write_switch(format!("{chosen}\n"), now);
    }

    /// The core asked for this interface to end so the launcher starts it again somewhere else (`effects.relaunch`,
    /// §8g): leave it a note of where (this interface, an empty line, the choice) and exit with the code that says so.
    fn start_again(&mut self, choice: &str, now: Instant) {
        self.write_switch(format!("glass\n\n{choice}"), now);
    }

    fn write_switch(&mut self, contents: String, now: Instant) {
        let Some(path) = std::env::var_os("KVIST_SWITCH_FILE") else {
            return self.notify(strings().frontend_needs_launcher.to_string(), true, now);
        };
        match std::fs::write(&path, contents) {
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

    /// Ctrl+C empties the input first. On an empty one a notice asks for a
    /// second press, and that one leaves; the splash has no input and leaves
    /// at once.
    fn interrupt(&mut self, now: Instant) {
        if matches!(self.phase, Phase::Splash) {
            self.quit = true;
            return;
        }
        if !self.input.is_empty() && !self.input_locked() {
            self.input.clear();
            self.cursor = 0;
            self.pick = 0;
            self.recall = None;
            self.quit_armed = None;
            return;
        }
        if self.quit_armed.is_some_and(|at| now.saturating_duration_since(at) < EXIT_CONFIRM) {
            self.quit_armed = None;
            self.quit = true;
            return;
        }
        self.quit_armed = Some(now);
        self.notify(strings().quit_hint.to_string(), false, now);
    }

    /// The core moved to another session: the cards go and the input glides
    /// back to the middle.
    /// What Enter does on the banner that stands in for the input.
    fn enter_on_banner(&mut self, now: Instant) {
        if self.remote.is_some() {
            return;
        }
        if self.observer.is_some() {
            return self.take_over(now);
        }
        self.command("new", &[], now);
    }

    /// Ask the core on the server to let this frontend write.
    fn take_over(&mut self, now: Instant) {
        self.send(&json!({"type": "takeover"}), now);
    }

    fn reset_view(&mut self, now: Instant) {
        self.session = Session::fresh();
        self.replay = None;
        self.feed = None;
        self.web_closed = true;
        self.input.clear();
        self.cursor = 0;
        self.attachments.clear();
        self.next_image = 1;
        self.strip_in = Tween::settled(0.0);
        self.scroll = 0;
        self.helper_focus = None;
        self.log = None;
        self.arena_row = 0;
        self.code.clear();
        self.input_pos.go(0.0, now, 540, Ease::Burst);
        self.left_in = Tween::settled(0.0);
        self.browser_in = Tween::settled(0.0);
        self.helpers_in.go(0.0, now, 300, Ease::Burst);
    }

    fn paste(&mut self, text: &str, now: Instant) {
        if !matches!(self.phase, Phase::Main { .. }) {
            return;
        }
        let locked = self.input_locked();
        match self.modal.as_mut() {
            Some(Modal::Ask(Some(ask))) => ask.paste(text),
            Some(_) => {}
            // A terminal that has only a picture to paste sends nothing at all.
            None if !locked && text.is_empty() => self.paste_from_clipboard(now),
            None if !locked => match attach::from_path(self.next_image, text) {
                Some(made) => self.put_attachment(made, now),
                None => self.insert(&text.replace("\r\n", "\n"), now),
            },
            None => {}
        }
    }

    /// Ctrl+V: a picture on the clipboard is attached; otherwise its text is pasted.
    fn paste_from_clipboard(&mut self, now: Instant) {
        match attach::from_clipboard(self.next_image) {
            Ok(Some(attachment)) => self.put_attachment(Ok(attachment), now),
            Ok(None) => match attach::clipboard_text() {
                Some(text) => self.paste(&text, now),
                None => self.notify(strings().no_clipboard.to_string(), true, now),
            },
            Err(error) => self.notify(error, true, now),
        }
    }

    fn put_attachment(&mut self, made: Result<Attachment, String>, now: Instant) {
        let attachment = match made {
            Ok(attachment) => attachment,
            Err(error) => return self.notify(error, true, now),
        };
        if self.attached().len() >= attach::MAX_IMAGES {
            return self.notify(strings().too_many_images.to_string(), true, now);
        }
        let marker = format!("[Image #{}] ", attachment.id);
        self.next_image += 1;
        self.attachments.push(attachment);
        self.insert(&marker, now);
    }

    fn open_image(&mut self, id: u32, now: Instant) {
        if self.attachment(id).is_none() {
            return;
        }
        self.modal = Some(Modal::Image(id));
        self.modal_p.go(1.0, now, 460, Ease::OutBack);
    }

    fn open_browser(&mut self, now: Instant) {
        if self.session.browser.is_none() || self.image_pending {
            return;
        }
        self.modal = Some(Modal::Browser);
        self.modal_p.go(1.0, now, 460, Ease::OutBack);
    }

    fn close_modal(&mut self, now: Instant) {
        match self.modal {
            Some(Modal::Browser | Modal::Image(_)) => self.modal_p.go(0.0, now, 360, Ease::Burst),
            _ => self.modal_p.go(0.0, now, 150, Ease::OutCubic),
        }
    }

    /// Opens the diff of the file at `index` of the left card; clicking the
    /// file that is open closes it. The diff takes the helpers panel's place.
    fn open_diff(&mut self, index: usize, now: Instant) {
        let Some(file) = self.session.files.get(index) else { return };
        let showing = self.diff_in.target() > 0.0;
        if showing && self.diff.as_ref().is_some_and(|view| view.path == file.path) {
            return self.close_diff(now);
        }
        self.diff = Some(diff::View::new(file.path.clone()));
        self.helpers_in = Tween::settled(0.0);
        if !showing {
            self.diff_in.go(1.0, now, 420, Ease::Burst);
        }
    }

    /// A press on a group's or a call's row opens or folds it. Returns false when the press was elsewhere.
    fn toggle_fold_at(&mut self, column: u16, row: u16, now: Instant) -> bool {
        if self.modal.is_some() {
            return false;
        }
        let Some((_, fold)) = self.fold_rects.iter().find(|(rect, _)| contains(*rect, (column, row))) else { return false };
        let fold = fold.clone();
        self.session.folds.toggle(fold, now);
        true
    }

    fn code_under_pointer(&self) -> Option<usize> {
        self.code_rects.iter().find(|(rect, _)| contains(*rect, self.last_mouse)).map(|(_, index)| *index)
    }

    /// Adds the lines that arrived to the write's block, opening it on the first.
    #[allow(clippy::too_many_arguments)]
    fn stream_code(&mut self, call: &str, tool: &str, path: &str, field: &str, line: u32, text: &str, now: Instant) {
        let index = self.code.iter().position(|block| block.call == call).unwrap_or_else(|| {
            let mut block = CodeBlock::new(call, tool, path);
            block.open.go(1.0, now, 420, Ease::Burst);
            self.code.push(block);
            self.code.len() - 1
        });
        self.code[index].feed(field, line, text);
    }

    /// Starts the countdown to folding a block: its call's result (`call`), or
    /// the end of the turn for a write that never got one.
    fn finish_code(&mut self, call: Option<&str>, now: Instant) {
        for block in self.code.iter_mut().filter(|block| block.finished.is_none() && call.is_none_or(|call| block.call == call)) {
            block.finished = Some(now);
        }
    }

    fn fold_code(&mut self, now: Instant) {
        for block in &mut self.code {
            let due = block.finished.is_some_and(|at| now.saturating_duration_since(at) >= CODE_LINGER);
            if due && block.open.target() > 0.0 {
                block.open.go(0.0, now, 360, Ease::Burst);
            }
        }
        self.code.retain(|block| block.open.target() > 0.0 || block.open.running(now));
    }

    fn close_diff(&mut self, now: Instant) {
        self.diff_in.go(0.0, now, 300, Ease::Burst);
    }

    /// Forgets the diff once its panel has slid out.
    fn drop_closed_diff(&mut self, now: Instant) {
        if self.diff.is_some() && self.diff_in.target() == 0.0 && !self.diff_in.running(now) {
            self.diff = None;
        }
    }

    fn toggle_helpers(&mut self, focus: Option<usize>, now: Instant) {
        if self.diff.is_some() {
            self.diff = None;
            self.diff_in = Tween::settled(0.0);
        }
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
        self.over_text = hit.is_none() && self.modal.is_none() && self.text_view.is_some_and(|view| view.holds(m.column, m.row));
        if matches!(self.phase, Phase::Splash) {
            return;
        }
        match m.kind {
            MouseEventKind::Moved => match (hit, self.modal.as_mut()) {
                (Some(Hit::Choice(row)), Some(Modal::Ask(Some(ask)))) => ask.select_row(row),
                (Some(Hit::ModelRow(row)), Some(Modal::Models(Some(picker)))) => picker.select_row(row),
                (Some(Hit::Verdict(row)), Some(Modal::Decision(decision))) if !decision.waiting => {
                    decision.step_row(row as i32 - decision.row as i32)
                }
                (Some(Hit::Command(row)), None) => self.pick = row,
                _ => {}
            },
            MouseEventKind::Down(MouseButton::Left) => {
                if self.modal.is_none() && self.press_left_bar(hit, m.row) {
                    return;
                }
                if self.toggle_fold_at(m.column, m.row, now) {
                    return;
                }
                if !self.drop_water(hit, m.column, m.row, now) {
                    self.click(hit, now);
                }
            }
            MouseEventKind::Drag(MouseButton::Left) if self.left_drag.is_some() => self.drag_left_bar(m.row),
            MouseEventKind::Drag(MouseButton::Left) => self.pour_to(m.column, m.row),
            MouseEventKind::Up(MouseButton::Left) if self.left_drag.take().is_some() => {}
            MouseEventKind::Up(MouseButton::Left) => self.lift_button(now),
            MouseEventKind::ScrollUp => self.wheel(-1),
            MouseEventKind::ScrollDown => self.wheel(1),
            _ => {}
        }
    }

    /// A press on the conversation's text lets a drop fall there. Returns
    /// false when the press was elsewhere and the water, if any, drained.
    fn drop_water(&mut self, hit: Option<Hit>, column: u16, row: u16, now: Instant) -> bool {
        let view = self.text_view.filter(|view| hit.is_none() && self.modal.is_none() && view.holds(column, row));
        let Some(at) = view.and_then(|view| view.locate(column, row, &self.widths)) else {
            self.drain_water(now);
            return false;
        };
        let presses = match self.last_press {
            Some((then, there, count)) if there == at && now.saturating_duration_since(then) < MULTI_CLICK => count + 1,
            _ => 1,
        };
        self.last_press = Some((now, at, presses));
        let width = self.widths.get(at.line).copied().unwrap_or(0);
        let text = self.plain.get(at.line).map(String::as_str).unwrap_or("");
        self.pour = Some(match presses {
            1 => Pour::begin(at, now),
            2 => {
                let (from, to) = water::word_around(text, at.col);
                Pour::choose(Pos { line: at.line, col: from }, Pos { line: at.line, col: to }, now)
            }
            _ => Pour::choose(Pos { line: at.line, col: 0 }, Pos { line: at.line, col: width.saturating_sub(1) }, now),
        });
        if presses > 1 {
            self.copy_water(now);
        }
        true
    }

    fn pour_to(&mut self, column: u16, row: u16) {
        let Some(view) = self.text_view else { return };
        let Some(pour) = self.pour.as_mut().filter(|pour| pour.pouring()) else { return };
        if let Some(at) = view.locate(column, row, &self.widths) {
            pour.drag(at);
        }
    }

    /// The button went up: the pool settles, and what it covers is copied.
    fn lift_button(&mut self, now: Instant) {
        let Some(pour) = self.pour.as_mut().filter(|pour| pour.pouring()) else { return };
        pour.release();
        if !pour.is_empty() {
            self.copy_water(now);
        }
    }

    fn copy_water(&mut self, now: Instant) {
        let Some(pour) = self.pour.as_ref() else { return };
        let text = water::selected_text(pour, &self.plain);
        if text.is_empty() {
            return;
        }
        self.clipboard = Some(text);
        self.notify(strings().copied.to_string(), false, now);
    }

    fn drain_water(&mut self, now: Instant) {
        if let Some(pour) = self.pour.as_mut() {
            pour.drain(now);
        }
    }

    /// Per tick: a drag held above or below the text scrolls it, and the
    /// water follows the pointer; a dialog drains the pool.
    fn keep_pouring(&mut self, now: Instant) {
        if self.modal.is_some() {
            return self.drain_water(now);
        }
        let Some(view) = self.text_view else { return };
        if !self.pour.as_ref().is_some_and(Pour::pouring) {
            return;
        }
        let (column, row) = self.last_mouse;
        let above = (row as i32) < view.top;
        let below = (row as i32) >= view.top + view.rows as i32;
        if (above || below) && now.saturating_duration_since(self.last_drag_scroll) >= DRAG_SCROLL {
            self.last_drag_scroll = now;
            self.scroll = if above { self.scroll + 1 } else { self.scroll.saturating_sub(1) };
        }
        self.pour_to(column, row);
    }

    /// Lets the water flow, draws its pool for this frame and lights the
    /// letters it covers.
    fn wet_the_text(&mut self, buf: &mut ratatui::buffer::Buffer, now: Instant) {
        self.water = None;
        let Some(view) = self.text_view else { return };
        let Some(pour) = self.pour.as_mut() else { return };
        pour.advance(now, &self.widths);
        let stale = pour.range().is_some_and(|(_, last)| last.line >= self.widths.len());
        if stale || pour.gone(now) {
            self.pour = None;
            return;
        }
        let shape = pour.shape(&view, &self.widths, now);
        for span in &shape.spans {
            for column in span.a.floor() as i32..span.b.ceil() as i32 {
                let Some(cell) = u16::try_from(column).ok().zip(u16::try_from(span.row).ok()).and_then(|at| buf.cell_mut(at)) else {
                    continue;
                };
                let base = if cell.fg == ratatui::style::Color::Reset { crate::theme::FOG } else { cell.fg };
                cell.set_fg(crate::theme::mix(base, ratatui::style::Color::Rgb(255, 255, 255), 0.55 * shape.level));
            }
        }
        self.water = Some(shape);
    }

    fn wheel(&mut self, delta: i32) {
        let card = self.dialog_rect;
        let over_code = self.code_under_pointer();
        let over_left = self.layout.left.is_some_and(|card| contains(card, self.last_mouse)).then(|| view::left_scroll(self)).flatten().filter(|s| s.max > 0);
        let left_at = over_left.map(|s| self.left_offset(s.max));
        match self.modal.as_mut() {
            // A diff to read scrolls; the buttons below it are reached by the arrows.
            Some(Modal::Ask(Some(ask))) if ask.diff.is_some() => {
                let most = view::ask_text_max_scroll(ask, card);
                ask.text_scroll = (ask.text_scroll as i32 + delta * 3).clamp(0, most as i32) as usize;
            }
            Some(Modal::Ask(Some(ask))) => {
                ask.step(delta);
                let rows = view::ask_list_rows(ask, card);
                ask.follow(rows);
            }
            Some(Modal::Models(Some(picker))) => {
                picker.step(delta);
                picker.follow(view::models_list_rows(card));
            }
            Some(Modal::Decision(decision)) if decision.page == Page::Patch => {
                let most = screens::patch_max_scroll(decision, card, strings());
                decision.scroll = (decision.scroll as i32 + delta * 3).clamp(0, most as i32) as usize;
            }
            Some(Modal::Decision(decision)) => decision.step_row(delta),
            Some(_) => {}
            None if self.layout.diff.is_some_and(|panel| contains(panel, self.last_mouse)) => {
                let rows = self.diff_rows();
                if let Some(view) = self.diff.as_mut() {
                    view.scroll_by(delta * 3, rows);
                }
            }
            None if over_code.is_some_and(|index| index < self.code.len()) => {
                let block = &mut self.code[over_code.unwrap_or(0)];
                block.up = (block.up as i32 - delta * 3).clamp(0, block.most_up() as i32) as usize;
            }
            None if over_left.is_some() => {
                let (Some(scroll), Some(at)) = (over_left, left_at) else { return };
                let at = (at as i32 + delta * 3).clamp(0, scroll.max as i32) as usize;
                self.set_left_scroll(at, scroll.max);
            }
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
            Some(Modal::Decision(decision)) if !decision.waiting => {
                let reply = match hit {
                    Some(Hit::Verdict(row)) => {
                        decision.step_row(row as i32 - decision.row as i32);
                        None
                    }
                    Some(Hit::Action(index)) => {
                        decision.action = index.min(decision.actions.len().saturating_sub(1));
                        decision.accept()
                    }
                    _ => None,
                };
                if let Some(reply) = reply {
                    self.answer_decision(reply, now);
                }
            }
            Some(Modal::Ask(None) | Modal::Models(None) | Modal::Decision(_)) => {}
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
            Some(Modal::Browser | Modal::Image(_)) => self.close_modal(now),
            None => match hit {
                Some(Hit::Thumb) => self.open_browser(now),
                Some(Hit::Attachment(id)) => self.open_image(id, now),
                Some(Hit::File(index)) => self.open_diff(index, now),
                Some(Hit::Executor(index)) => {
                    self.arena_row = index;
                    self.open_log(index, now);
                }
                Some(Hit::DiffClose) => self.close_diff(now),
                Some(Hit::Helper(id)) => self.toggle_helpers(Some(id), now),
                Some(Hit::HelpersTitle | Hit::HelpersClose) => self.toggle_helpers(None, now),
                Some(Hit::NewSession) => self.command("new", &[], now),
                Some(Hit::TakeOver) => self.take_over(now),
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

    /// `Kvist | <title>` once the session has one.
    fn window_title(&self) -> String {
        let clean: String = self.session_title.chars().filter(|c| !c.is_control()).collect();
        let clean = clean.trim();
        if clean.is_empty() { "Kvist".to_string() } else { format!("Kvist | {}", clean.chars().take(80).collect::<String>()) }
    }

    fn flush_title(&mut self) -> io::Result<()> {
        let title = self.window_title();
        if self.title_sent.as_ref() == Some(&title) {
            return Ok(());
        }
        term::set_title(&title)?;
        self.title_sent = Some(title);
        Ok(())
    }

    fn flush_pointer(&mut self, out: &mut impl Write) -> io::Result<()> {
        if let Some(text) = self.clipboard.take() {
            out.write_all(&term::copy_to_clipboard(&text))?;
            out.flush()?;
        }
        let shape = (self.hand, self.over_text);
        if self.hand_sent == Some(shape) {
            return Ok(());
        }
        out.write_all(term::pointer_shape(shape.0, shape.1))?;
        out.flush()?;
        self.hand_sent = Some(shape);
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
    restored?;
    // Switching interfaces keeps the terminal busy; only a real exit says goodbye.
    if code != SWITCH_FRONTEND
        && let Ok(core) = core::command(std::env::var("KVIST_CORE_CMD").ok().as_deref())
    {
        crate::farewell::print(&core::session_command_for(&core), workspace);
    }
    Ok(code)
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
        app.flush_title()?;

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

/// The words for a link to the server that is not simply up: the proxy's reason, and when it tries again.
fn remote_notice(event: &Value) -> String {
    let reason = event.get("reason").and_then(Value::as_str).unwrap_or("");
    let host = event.get("host").and_then(Value::as_str).unwrap_or("");
    let first = if reason.is_empty() { host } else { reason };
    let Some(seconds) = event.get("retry_in").and_then(Value::as_f64) else { return first.to_string() };
    let russian = reason.chars().any(|c| ('а'..='я').contains(&c.to_ascii_lowercase()) || c == 'ё');
    let wait = if russian { format!("повтор через {} с", seconds.round()) } else { format!("retrying in {} s", seconds.round()) };
    format!("{first} · {wait}")
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
    fn a_session_another_frontend_drives_locks_the_input_and_opens_no_dialogs() {
        let mut app = app();
        let now = Instant::now();
        app.on_core(&json!({"type": "control", "role": "observer", "notice": "watching"}), now);
        assert!(app.input_locked());
        app.on_core(&json!({"type": "input_request", "request_id": "r", "kind": "confirm", "title": "Run it?"}), now);
        assert!(app.modal.is_none());
        app.on_core(&json!({"type": "control", "role": "driver", "notice": ""}), now);
        assert!(!app.input_locked());
        // The core re-sends what is still open when this frontend takes over.
        app.on_core(&json!({"type": "input_request", "request_id": "r", "kind": "confirm", "title": "Run it?"}), now);
        assert!(matches!(app.modal, Some(Modal::Ask(Some(_)))));
    }

    #[test]
    fn the_core_asking_to_start_again_elsewhere_leaves_the_launcher_a_note_and_ends_this_interface() {
        let mut app = app();
        let now = Instant::now();
        let file = std::env::temp_dir().join(format!("kvist-glass-switch-{}", std::process::id()));
        // SAFETY: no other test of this crate reads or writes this variable.
        unsafe { std::env::set_var("KVIST_SWITCH_FILE", &file) };
        app.on_core(&json!({"type": "command_result", "name": "machines", "ok": true, "message": "Opening work",
            "effects": {"relaunch": {"switch": "@machine\twork\t/srv/app\t", "label": "work"}}}), now);
        unsafe { std::env::remove_var("KVIST_SWITCH_FILE") };
        let written = std::fs::read_to_string(&file).unwrap_or_default();
        std::fs::remove_file(&file).ok();
        assert_eq!(written, "glass\n\n@machine\twork\t/srv/app\t");
        assert!(app.quit);
        assert_eq!(app.exit_code, SWITCH_FRONTEND);
    }

    #[test]
    fn a_core_on_a_server_says_where_it_works_and_this_screens_own_folder_is_then_not_shown() {
        let mut app = app();
        assert!(app.location.is_empty());

        app.on_core(&json!({"type": "hello", "protocol_version": 1, "location": "work:/srv/app", "workspace": "/srv/app"}), Instant::now());

        assert_eq!(app.location, "work:/srv/app");
    }

    #[test]
    fn a_notice_is_a_toast_and_never_a_row_of_the_conversation() {
        let mut app = app();
        let now = Instant::now();
        app.on_core(&json!({"type": "notice", "text": "Connecting to work:/srv/app…", "level": "info"}), now);
        let toast = app.toast.as_ref().expect("a toast");
        assert_eq!((toast.text.as_str(), toast.error), ("Connecting to work:/srv/app…", false));
        app.on_core(&json!({"type": "notice", "text": "Not connected yet", "level": "warn"}), now);
        assert!(app.toast.as_ref().unwrap().error);
        assert!(app.session.items.is_empty());
    }

    #[test]
    fn a_link_that_is_down_says_why_and_when_it_tries_again() {
        let mut app = app();
        let now = Instant::now();
        app.on_core(&json!({"type": "remote_state", "state": "reconnecting", "host": "me@box",
            "reason": "me@box is not answering", "retry_in": 4.0}), now);
        assert!(app.input_locked());
        assert_eq!(app.remote.as_deref(), Some("me@box is not answering · retrying in 4 s"));
        app.on_core(&json!({"type": "remote_state", "state": "connected", "host": "me@box"}), now);
        assert!(!app.input_locked());
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
        app.dialog_rect = layout::dialog(&app.grid, layout::DIALOG_WIDTH, view::ask_height(ask, app.grid.cols));
        app.ask_key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE), now);
        let Some(Modal::Ask(Some(ask))) = &app.modal else { panic!("missing dialog") };
        assert_eq!(ask.text_scroll, 6);
        for _ in 0..20 { app.ask_key(KeyEvent::new(KeyCode::PageDown, KeyModifiers::NONE), now); }
        let Some(Modal::Ask(Some(ask))) = &app.modal else { panic!("missing dialog") };
        assert_eq!(ask.text_scroll, view::ask_text_max_scroll(ask, app.dialog_rect));
    }

    fn mouse_event(kind: MouseEventKind, column: u16, row: u16) -> MouseEvent {
        MouseEvent { kind, column, row, modifiers: KeyModifiers::NONE }
    }

    /// An app that has drawn two lines of text at column 3, rows 5 and 6.
    fn app_with_text() -> App {
        let mut app = app();
        app.phase = Phase::Main { since: Instant::now() };
        app.text_view = Some(TextView { x: 3, top: 5, first: 0, rows: 2 });
        app.plain = vec!["water on glass".to_string(), "holds in a lens".to_string()];
        app.widths = app.plain.iter().map(|line| line.len()).collect();
        app
    }

    #[test]
    fn dragging_over_text_pours_water_and_copies_it_on_release() {
        let mut app = app_with_text();
        let now = Instant::now();
        app.mouse(mouse_event(MouseEventKind::Down(MouseButton::Left), 3 + 6, 5), now);
        app.mouse(mouse_event(MouseEventKind::Drag(MouseButton::Left), 3 + 3, 6), now);
        assert!(app.clipboard.is_none(), "nothing is copied while the button is down");
        app.mouse(mouse_event(MouseEventKind::Up(MouseButton::Left), 3 + 3, 6), now);
        assert_eq!(app.clipboard.as_deref(), Some("on glass\nhold"));
    }

    #[test]
    fn a_press_without_a_drag_copies_nothing() {
        let mut app = app_with_text();
        let now = Instant::now();
        app.mouse(mouse_event(MouseEventKind::Down(MouseButton::Left), 5, 5), now);
        app.mouse(mouse_event(MouseEventKind::Up(MouseButton::Left), 5, 5), now);
        assert!(app.clipboard.is_none());
        assert!(app.pour.is_some(), "the drop still ripples");
    }

    #[test]
    fn a_double_click_takes_a_word_and_a_third_press_the_line() {
        let mut app = app_with_text();
        let now = Instant::now();
        let down = mouse_event(MouseEventKind::Down(MouseButton::Left), 3 + 9, 5);
        app.mouse(down, now);
        app.mouse(down, now);
        assert_eq!(app.clipboard.take().as_deref(), Some("glass"));
        app.mouse(down, now);
        assert_eq!(app.clipboard.as_deref(), Some("water on glass"));
    }

    #[test]
    fn a_press_outside_the_text_lets_the_water_drain() {
        let mut app = app_with_text();
        let now = Instant::now();
        app.mouse(mouse_event(MouseEventKind::Down(MouseButton::Left), 4, 5), now);
        app.mouse(mouse_event(MouseEventKind::Up(MouseButton::Left), 4, 5), now);
        app.mouse(mouse_event(MouseEventKind::Down(MouseButton::Left), 1, 30), now);
        let later = now + Duration::from_secs(2);
        let mut buf = ratatui::buffer::Buffer::empty(Rect::new(0, 0, 120, 36));
        app.wet_the_text(&mut buf, later);
        assert!(app.pour.is_none());
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

    const PATCH: &str = "--- a/kernel/agent/helpers.py\n+++ b/kernel/agent/helpers.py\n@@ -10,3 +10,3 @@\n keep\n-total = price * 2\n+total = price * 3\n keep too";

    /// A wide window with one file the agent changed, drawn once so its rows are clickable.
    fn changed_app() -> (App, Instant) {
        let mut app = main_app();
        app.grid = Grid { cols: 180, rows: 50, cw: 10, ch: 20 };
        let now = Instant::now();
        app.session.apply(
            CoreEvent::FileChanged { path: "kernel/agent/helpers.py".into(), added: 1, removed: 1, patch: Some(PATCH.into()) },
            now,
        );
        app.left_in = Tween::settled(1.0);
        app.input_rise = Tween::settled(1.0);
        draw_once(&mut app, now);
        (app, now)
    }

    fn draw_once(app: &mut App, now: Instant) {
        app.relayout(now);
        let mut buffer = ratatui::buffer::Buffer::empty(app.grid.full());
        app.hits = view::draw(app, &mut buffer, now).hits;
    }

    fn click_on(app: &mut App, hit: impl Fn(&Hit) -> bool, now: Instant) {
        let (rect, _) = *app.hits.iter().find(|(_, h)| hit(h)).expect("a clickable row");
        let target = app.hit_at(rect.x + 1, rect.y);
        app.click(target, now);
    }

    #[test]
    fn clicking_a_changed_file_opens_its_diff_on_the_right_and_a_second_click_closes_it() {
        let (mut app, now) = changed_app();
        click_on(&mut app, |h| matches!(h, Hit::File(0)), now);
        assert_eq!(app.diff.as_ref().map(|view| view.path.as_str()), Some("kernel/agent/helpers.py"));
        draw_once(&mut app, now + Duration::from_secs(1));
        let panel = app.layout.diff.expect("the panel");
        assert!(panel.x > app.layout.input.right());
        let text: String = app.diff.as_ref().unwrap().lines.iter().flatten().map(|(s, _)| s.as_str()).collect();
        assert!(text.contains("price * 3") && text.contains("price * 2"));

        click_on(&mut app, |h| matches!(h, Hit::File(0)), now + Duration::from_secs(1));
        assert_eq!(app.diff_in.target(), 0.0);
        app.drop_closed_diff(now + Duration::from_secs(3));
        assert!(app.diff.is_none());
    }

    /// An app whose changed files do not fit the left card.
    fn crowded_app() -> (App, Instant) {
        let (mut app, now) = changed_app();
        app.grid = Grid { cols: 180, rows: 24, cw: 10, ch: 20 };
        for n in 0..40 {
            app.session.apply(CoreEvent::FileChanged { path: format!("src/f{n}.rs"), added: 1, removed: 0, patch: None }, now);
        }
        draw_once(&mut app, now);
        (app, now)
    }

    #[test]
    fn a_crowded_left_card_keeps_its_rows_inside_and_follows_the_newest_file() {
        let (app, _) = crowded_app();
        let card = app.layout.left.expect("the card");
        let rows: Vec<Rect> = app.hits.iter().filter(|(_, h)| matches!(h, Hit::File(_))).map(|(rect, _)| *rect).collect();
        assert!(rows.len() < 41 && rows.iter().all(|r| r.y > card.y && r.bottom() < card.bottom()), "{rows:?} in {card:?}");
        assert!(app.hits.iter().any(|(_, h)| *h == Hit::File(40)), "the newest file is in view");
        assert!(app.hits.iter().any(|(_, h)| *h == Hit::LeftThumb));
    }

    #[test]
    fn the_wheel_and_the_thumb_scroll_the_left_card_and_the_end_is_followed_again() {
        let (mut app, now) = crowded_app();
        let card = app.layout.left.expect("the card");
        app.hit_at(card.x + 3, card.y + 3);
        app.wheel(-1);
        draw_once(&mut app, now);
        assert!(!app.hits.iter().any(|(_, h)| *h == Hit::File(40)), "scrolled up, the newest is out of view");
        let (thumb, _) = *app.hits.iter().find(|(_, h)| *h == Hit::LeftThumb).expect("a thumb");
        app.mouse(mouse_event(MouseEventKind::Down(MouseButton::Left), thumb.x + 1, thumb.y), now);
        app.mouse(mouse_event(MouseEventKind::Drag(MouseButton::Left), thumb.x + 1, card.bottom()), now);
        app.mouse(mouse_event(MouseEventKind::Up(MouseButton::Left), thumb.x + 1, card.bottom()), now);
        draw_once(&mut app, now);
        assert!(app.left_drag.is_none() && app.left_scroll.is_none(), "dragged to the end the list follows it again");
        assert!(app.hits.iter().any(|(_, h)| *h == Hit::File(40)));
    }

    fn open_screen(id: &str, title: &str) -> CoreEvent {
        CoreEvent::Screen {
            screen_id: id.into(),
            title: title.into(),
            frame_url: format!("http://127.0.0.1:9/{id}/frame"),
            stream_url: format!("http://127.0.0.1:9/{id}/stream"),
            url: format!("http://127.0.0.1:9/{id}/"),
        }
    }

    fn card_title(app: &App) -> Option<&str> {
        app.session.browser.as_ref().map(|browser| browser.title.as_str())
    }

    #[test]
    fn the_card_follows_the_screen_announced_last_and_goes_back_when_it_closes() {
        let (mut app, now) = changed_app();
        app.on_session(open_screen("browser", "Browser"), now);
        app.on_session(open_screen("app_screen", "App screen"), now);
        assert_eq!(card_title(&app), Some("App screen"));
        assert!(app.feed.as_ref().is_some_and(|feed| feed.url.contains("app_screen")));

        // The browser closing while the app screen is on show does not touch the card.
        app.on_session(CoreEvent::ScreenClosed { screen_id: "browser".into() }, now);
        assert_eq!(card_title(&app), Some("App screen"));
        assert!(app.feed.is_some());

        app.on_session(open_screen("browser", "Browser"), now);
        app.on_session(CoreEvent::ScreenClosed { screen_id: "browser".into() }, now);
        assert_eq!(card_title(&app), Some("App screen"), "the app screen is still open and takes the card back");
        assert!(app.feed.as_ref().is_some_and(|feed| feed.url.contains("app_screen")));

        app.on_session(CoreEvent::ScreenClosed { screen_id: "app_screen".into() }, now);
        assert_eq!(card_title(&app), None);
        assert!(app.feed.is_none() && app.screens.is_empty());
    }

    fn piece(call: &str, line: u32, text: &str) -> CoreEvent {
        CoreEvent::CallStream { call: call.into(), tool: "write_file".into(), path: "app/main.py".into(), field: "content".into(), line, text: text.into() }
    }

    fn screen_text(app: &mut App, now: Instant) -> String {
        app.relayout(now);
        let mut buffer = ratatui::buffer::Buffer::empty(app.grid.full());
        let drawn = view::draw(app, &mut buffer, now);
        app.code_rects = drawn.code;
        (0..app.grid.rows).map(|y| (0..app.grid.cols).map(|x| buffer[(x, y)].symbol().to_string()).collect::<String>() + "\n").collect()
    }

    #[test]
    fn a_write_appears_in_the_conversation_as_a_code_block_that_folds_two_seconds_after_its_result() {
        let (mut app, now) = changed_app();
        app.session.items.push(crate::model::Item::User("write it".into()));
        app.on_session(piece("w1", 0, "import os\nx = 1\n"), now);
        let shown = screen_text(&mut app, now + Duration::from_secs(1));
        assert!(shown.contains("\u{e606}") && shown.contains("import os"), "{shown}");
        assert_eq!(app.code_rects.len(), 1);

        app.on_session(piece("w1", 2, "y = 2\n"), now);
        assert!(screen_text(&mut app, now + Duration::from_secs(1)).contains("y = 2"));

        let done = now + Duration::from_secs(2);
        app.on_session(CoreEvent::Result { call: "w1".into(), ok: true, summary: "ok".into(), diff: Some((3, 0)), output: String::new() }, done);
        app.fold_code(done + Duration::from_millis(1900));
        assert_eq!(app.code[0].open.target(), 1.0, "still lingering");
        app.fold_code(done + Duration::from_millis(2100));
        assert_eq!(app.code[0].open.target(), 0.0);
        app.fold_code(done + Duration::from_secs(4));
        assert!(app.code.is_empty());
    }

    #[test]
    fn the_wheel_over_a_code_block_scrolls_it_and_not_the_conversation() {
        let (mut app, now) = changed_app();
        app.session.items.push(crate::model::Item::User("write it".into()));
        let code: String = (1..=40).map(|n| format!("v{n} = {n}\n")).collect();
        app.on_session(piece("w1", 0, &code), now);
        screen_text(&mut app, now + Duration::from_secs(1));
        let (rect, _) = app.code_rects[0];
        app.last_mouse = (rect.x + 2, rect.y + 2);
        app.wheel(-1);
        assert_eq!(app.code[0].up, 3);
        assert_eq!(app.scroll, 0);
        app.last_mouse = (0, 0);
        app.wheel(-1);
        assert_eq!(app.scroll, 3);
    }

    #[test]
    fn escape_closes_the_diff_before_it_stops_a_running_turn() {
        let (mut app, now) = changed_app();
        app.open_diff(0, now);
        app.session.busy = true;
        press(&mut app, KeyCode::Esc);
        assert_eq!(app.diff_in.target(), 0.0);
        assert!(app.stop_armed.is_none());
    }

    #[test]
    fn the_wheel_scrolls_the_diff_only_while_the_pointer_is_over_it() {
        let (mut app, now) = changed_app();
        let long: String = (1..=80).map(|n| format!("+line {n}\n")).collect();
        app.session.apply(
            CoreEvent::FileChanged { path: "kernel/agent/helpers.py".into(), added: 80, removed: 0, patch: Some(format!("@@ -0,0 +1,80 @@\n{long}")) },
            now,
        );
        app.open_diff(0, now);
        draw_once(&mut app, now + Duration::from_secs(1));
        let panel = app.layout.diff.unwrap();
        app.last_mouse = (panel.x + 5, panel.y + 5);
        app.wheel(1);
        assert_eq!(app.diff.as_ref().unwrap().scroll, 3);
        app.last_mouse = (app.layout.input.x + 2, app.layout.input.y);
        app.wheel(-1);
        assert_eq!(app.diff.as_ref().unwrap().scroll, 3);
        assert_eq!(app.scroll, 3);
    }

    #[test]
    fn opening_the_helpers_panel_replaces_the_diff() {
        let (mut app, now) = changed_app();
        app.open_diff(0, now);
        app.toggle_helpers(None, now);
        assert!(app.diff.is_none());
        assert!(app.helpers_in.target() > 0.0);
    }

    #[test]
    fn an_overwrite_approval_shows_the_old_file_beside_the_new_one() {
        let mut app = main_app();
        app.grid = Grid { cols: 180, rows: 50, cw: 10, ch: 20 };
        let now = Instant::now();
        app.on_core(
            &json!({"type": "input_request", "request_id": "w", "kind": "confirm", "title": "File overwrite",
                "subject": {"kind": "overwrite", "path": "kernel/agent/helpers.py", "diff": PATCH}}),
            now,
        );
        app.modal_p = Tween::settled(1.0);
        app.relayout(now);
        assert_eq!(app.dialog_rect.width, layout::DIFF_DIALOG_WIDTH);
        let mut buffer = ratatui::buffer::Buffer::empty(app.grid.full());
        view::draw(&app, &mut buffer, now);
        let row = (0..buffer.area.height)
            .map(|y| (0..buffer.area.width).map(|x| buffer[(x, y)].symbol().to_string()).collect::<String>())
            .find(|line| line.contains("price * 2"))
            .expect("the removed line is drawn");
        assert!(row.find("price * 2") < row.find('│') && row.find('│') < row.find("price * 3"), "{row}");
    }

    #[test]
    fn arrows_scroll_an_overwrites_diff_instead_of_moving_the_buttons() {
        let mut app = main_app();
        let now = Instant::now();
        let rows: String = (1..=60).map(|n| format!("+line {n}\n")).collect();
        app.on_core(
            &json!({"type": "input_request", "request_id": "w", "kind": "confirm", "title": "File overwrite",
                "subject": {"kind": "overwrite", "path": "a.py", "diff": format!("@@ -0,0 +1,60 @@\n{rows}")}}),
            now,
        );
        app.modal_p = Tween::settled(1.0);
        app.relayout(now);
        press(&mut app, KeyCode::Down);
        let Some(Modal::Ask(Some(ask))) = &app.modal else { panic!("missing dialog") };
        assert_eq!(ask.text_scroll, 1);
        assert_eq!(ask.picked(), Some(crate::dialog::Answer::No));
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
    fn ctrl_c_empties_the_input_and_a_second_press_on_the_empty_one_leaves() {
        let mut app = main_app();
        typing(&mut app, "fix the tests");
        press_with(&mut app, KeyCode::Char('c'), KeyModifiers::CONTROL);
        assert_eq!((app.input.as_str(), app.cursor), ("", 0));
        assert!(!app.quit && app.toast.is_none(), "the first press only emptied the field");

        press_with(&mut app, KeyCode::Char('c'), KeyModifiers::CONTROL);
        assert!(!app.quit, "an empty input asks first");
        let toast = app.toast.as_ref().expect("the notice");
        assert_eq!((toast.text.as_str(), toast.error), (strings().quit_hint, false));

        press_with(&mut app, KeyCode::Char('c'), KeyModifiers::CONTROL);
        assert!(app.quit, "the second press leaves");
    }

    #[test]
    fn a_ctrl_c_after_the_notice_has_gone_is_a_first_press_again() {
        let mut app = main_app();
        let now = Instant::now();
        let ctrl_c = |app: &mut App, at: Instant| app.key(KeyEvent::new(KeyCode::Char('c'), KeyModifiers::CONTROL), at);
        ctrl_c(&mut app, now);
        assert!(!app.quit && app.quit_armed.is_some());
        ctrl_c(&mut app, now + EXIT_CONFIRM + Duration::from_millis(1));
        assert!(!app.quit, "a late press is the first one again");
        assert!(app.quit_armed.is_some());
        ctrl_c(&mut app, now + EXIT_CONFIRM + Duration::from_millis(2));
        assert!(app.quit);
    }

    #[test]
    fn ctrl_c_on_the_splash_leaves_at_once() {
        let mut app = app();
        assert!(matches!(app.phase, Phase::Splash));
        press_with(&mut app, KeyCode::Char('c'), KeyModifiers::CONTROL);
        assert!(app.quit);
    }

    #[test]
    fn a_session_the_person_already_typed_into_keeps_its_own_feed() {
        let mut app = app();
        app.session.submit("new request".into(), Instant::now());
        app.show_history(journal(), Instant::now());
        assert_eq!(app.session.items.len(), 1);
    }
    fn png_file(name: &str) -> String {
        let dir = std::env::temp_dir().join(format!("kvist-glass-app-{}", std::process::id()));
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.join(name);
        image::RgbImage::from_pixel(48, 30, image::Rgb([20, 120, 220])).save(&path).unwrap();
        path.to_string_lossy().into_owned()
    }

    #[test]
    fn a_pasted_picture_path_becomes_a_marker_and_a_strip_over_the_input() {
        let mut app = app();
        app.phase = Phase::Main { since: Instant::now() };
        let now = Instant::now();
        app.paste(&png_file("shot.png"), now);
        assert_eq!(app.input, "[Image #1] ");
        assert_eq!(app.attached().len(), 1);
        app.sync_strip(now);
        let later = now + Duration::from_secs(1);
        app.relayout(later);
        assert_eq!(app.layout.attach_rows, layout::STRIP_ROWS);
        let (shown, hidden) = app.strip(later);
        assert_eq!((shown.len(), hidden), (1, 0));
        assert!(shown[0].0.y > app.layout.input.y && shown[0].0.bottom() <= app.layout.input.y + layout::STRIP_ROWS);
    }

    #[test]
    fn plain_text_still_pastes_as_text() {
        let mut app = app();
        app.phase = Phase::Main { since: Instant::now() };
        app.paste("just some words\nand a second line", Instant::now());
        assert_eq!(app.input, "just some words\nand a second line");
        assert!(app.attached().is_empty());
    }

    #[test]
    fn deleting_the_marker_drops_the_picture_and_closes_the_strip() {
        let mut app = app();
        app.phase = Phase::Main { since: Instant::now() };
        let now = Instant::now();
        app.paste(&png_file("gone.png"), now);
        app.sync_strip(now);
        app.key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE), now);
        app.key(KeyEvent::new(KeyCode::Backspace, KeyModifiers::NONE), now);
        assert_eq!(app.input, "");
        app.sync_strip(now);
        assert!(app.attachments.is_empty());
        assert_eq!(app.strip_in.target(), 0.0);
    }

    #[test]
    fn a_click_on_a_thumbnail_opens_the_picture_and_a_click_closes_it() {
        let mut app = app();
        app.phase = Phase::Main { since: Instant::now() };
        let now = Instant::now();
        app.paste(&png_file("open.png"), now);
        app.sync_strip(now);
        app.click(Some(Hit::Attachment(1)), now + Duration::from_secs(1));
        assert!(matches!(app.modal, Some(Modal::Image(1))));
        app.click(None, now + Duration::from_secs(2));
        assert_eq!(app.modal_p.target(), 0.0);
    }

    // -- best-of and council, from the events the core is to send --

    fn feed(app: &mut App, events: &[Value]) {
        let now = Instant::now();
        for event in events {
            app.on_core(event, now);
        }
    }

    fn status(id: &str, state: &str, step: u32, tests: Value, usage: Value) -> Value {
        json!({"type": "bestof_status", "run_id": "r1", "executor_id": id, "style": format!("style-{id}"), "branch": format!("kvist/bestof/r1/{id}"),
            "state": state, "step": step, "tests": tests, "diff": {"files": 2, "added": 30, "removed": 4}, "usage": usage})
    }

    fn started() -> Value {
        json!({"type": "bestof_started", "run_id": "r1", "task": "fix the parser", "count": 3, "base": "abc123", "executors": [
            {"executor_id": "e1", "style": "style-e1", "branch": "kvist/bestof/r1/e1", "session": "s-e1"},
            {"executor_id": "e2", "style": "style-e2", "branch": "kvist/bestof/r1/e2", "session": "s-e2"},
            {"executor_id": "e3", "style": "style-e3", "branch": "kvist/bestof/r1/e3", "session": "s-e3"}]})
    }

    fn verdict() -> Value {
        let row = |id: &str, outcome: &str, tests: &str| {
            json!({"executor_id": id, "style": format!("style-{id}"), "outcome": outcome,
                "tests": {"state": tests, "summary": ""}, "diff": {"files": 1, "added": 5, "removed": 1}, "note": format!("note {id}")})
        };
        json!({"type": "bestof_verdict", "run_id": "r1", "winner": "e2", "reason": "e2 passes every test.",
            "rows": [row("e1", "finalist", "passed"), row("e2", "winner", "passed"), row("e3", "eliminated", "failed")]})
    }

    fn decision_request() -> Value {
        json!({"type": "input_request", "request_id": "q1", "kind": "choice", "title": "Which result?", "body": "text of the verdict",
            "purpose": "bestof_decision", "run_id": "r1",
            "options": [{"value": "accept", "label": "Accept"}, {"value": "diff", "label": "Diff"}, {"value": "other", "label": "Other"}, {"value": "discard", "label": "Discard"}]})
    }

    /// A run that has reached the question: three executors, one of them out, the verdict, the dialog.
    fn decision_app() -> App {
        let mut app = main_app();
        feed(
            &mut app,
            &[
                started(),
                status("e1", "finished", 8, json!({"state": "passed", "summary": "12 passed"}), json!({"input_tokens": 1000, "output_tokens": 100, "cost_usd": 0.25, "cost_approx": false})),
                status("e2", "finished", 6, json!({"state": "passed", "summary": "12 passed"}), json!({"input_tokens": 900, "output_tokens": 90, "cost_usd": 0.125, "cost_approx": false})),
                status("e3", "running", 3, json!({"state": "failed", "summary": "2 failed"}), json!({"input_tokens": 500, "output_tokens": 50, "cost_approx": false})),
                json!({"type": "executor_activity", "run_id": "r1", "executor_id": "e3", "phase": "step", "call_id": "c1", "action_type": "run_shell", "label": "runs pytest"}),
                json!({"type": "bestof_eliminated", "run_id": "r1", "executor_id": "e3", "reason": "tests keep failing"}),
                verdict(),
                decision_request(),
            ],
        );
        app.modal_p = Tween::settled(1.0);
        app.relayout(Instant::now());
        app
    }

    fn last_value(app: &App) -> Value {
        app.sent.last().cloned().unwrap_or(Value::Null)
    }

    #[test]
    fn a_best_of_run_builds_the_arena_and_ends_in_the_decision_dialog() {
        let mut app = decision_app();
        let arena = app.session.arena.as_ref().expect("an arena");
        assert_eq!((arena.run_id.as_str(), arena.task.as_str(), arena.executors.len()), ("r1", "fix the parser", 3));
        assert_eq!(arena.executors[0].state, crate::arena::ExecState::Finished);
        assert_eq!((arena.executors[0].step, arena.executors[0].diff.added), (8, 30));
        assert_eq!(arena.executors[0].tests.summary, "12 passed");
        let out = &arena.executors[2];
        assert_eq!((out.state, out.reason.as_str()), (crate::arena::ExecState::Eliminated, "tests keep failing"));
        assert_eq!(out.last.as_ref().map(|call| call.label.as_str()), Some("runs pytest"));
        assert_eq!(arena.verdict.as_ref().map(|v| v.winner.as_str()), Some("e2"));
        // The arena stands in the conversation's place, and the question is the verdict table.
        assert!(matches!(app.pane(), Some(Pane::Arena(_))));
        let Some(Modal::Decision(decision)) = &app.modal else { panic!("the decision dialog") };
        assert_eq!((decision.request_id.as_str(), decision.row, decision.page), ("q1", 1, Page::Table));
        assert_eq!(decision.actions.len(), 4);

        feed(&mut app, &[json!({"type": "bestof_finished", "run_id": "r1", "outcome": "accepted", "executor_id": "e2", "commit": "abc1234", "message": "Committed"})]);
        let finished = app.session.arena.as_ref().and_then(|a| a.finished.as_ref()).expect("the closing line");
        assert_eq!((finished.id.as_str(), finished.commit.as_str()), ("e2", "abc1234"));
    }

    #[test]
    fn each_decision_sends_exactly_the_reply_the_core_reads() {
        let now = Instant::now();
        // Accept: Enter on the button the dialog opens on.
        let mut app = decision_app();
        press(&mut app, KeyCode::Enter);
        assert_eq!(last_value(&app), json!({"request_id": "q1", "value": "accept"}));
        assert_eq!(app.modal_p.target(), 0.0, "no command is running: the dialog closes");

        // The diff of the row the person is on.
        let mut app = decision_app();
        typing(&mut app, "d");
        assert_eq!(last_value(&app), json!({"request_id": "q1", "value": "diff:e2"}));

        // Another row, then "pick another".
        let mut app = decision_app();
        press(&mut app, KeyCode::Down);
        typing(&mut app, "p");
        assert_eq!(last_value(&app), json!({"request_id": "q1", "value": "pick:e3"}));

        // The buttons by arrows: the fourth is "discard".
        let mut app = decision_app();
        for _ in 0..3 {
            press(&mut app, KeyCode::Right);
        }
        press(&mut app, KeyCode::Enter);
        assert_eq!(last_value(&app), json!({"request_id": "q1", "value": "discard"}));

        // By the mouse: draw once, click the second button ("see the diff") of the row the table opens on.
        let mut app = decision_app();
        draw_once(&mut app, now);
        click_on(&mut app, |h| matches!(h, Hit::Action(1)), now);
        assert_eq!(last_value(&app), json!({"request_id": "q1", "value": "diff:e2"}));

        // Esc is no decision for now.
        let mut app = decision_app();
        press(&mut app, KeyCode::Esc);
        assert_eq!(last_value(&app), json!({"request_id": "q1", "value": null}));
        assert_eq!(app.sent.len(), 1, "exactly one reply");
    }

    #[test]
    fn after_diff_the_core_asks_again_and_the_patch_is_shown_once() {
        let mut app = decision_app();
        app.command = Some("best-of".into());
        typing(&mut app, "d");
        assert!(matches!(&app.modal, Some(Modal::Decision(d)) if d.waiting), "the dialog waits for the next question");
        feed(
            &mut app,
            &[
                json!({"type": "bestof_diff", "run_id": "r1", "executor_id": "e2", "files": ["a.py"],
                    "patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old line\n+new line\n[... 40 characters omitted ...]"}),
                decision_request(),
            ],
        );
        let Some(Modal::Decision(decision)) = &app.modal else { panic!("the dialog") };
        assert!(!decision.waiting);
        assert_eq!(decision.page, Page::Patch);
        assert_eq!(decision.patch.as_ref().map(|p| p.id.as_str()), Some("e2"));
        press(&mut app, KeyCode::Tab);
        assert!(matches!(&app.modal, Some(Modal::Decision(d)) if d.page == Page::Table));
        // The dialog is drawn on either page.
        for _ in 0..2 {
            app.relayout(Instant::now());
            let mut buffer = ratatui::buffer::Buffer::empty(app.grid.full());
            view::draw(&app, &mut buffer, Instant::now());
            press(&mut app, KeyCode::Tab);
        }
    }

    #[test]
    fn a_decision_for_another_run_or_with_no_verdict_is_an_ordinary_list() {
        let mut app = main_app();
        feed(&mut app, &[started(), decision_request()]);
        assert!(matches!(app.modal, Some(Modal::Ask(Some(_)))), "no verdict yet: a plain list");
        let mut app = main_app();
        feed(&mut app, &[started(), verdict(), json!({"type": "input_request", "request_id": "q2", "kind": "choice", "options": [{"value": "x"}]})]);
        assert!(matches!(app.modal, Some(Modal::Ask(Some(_)))), "a question without the purpose is not the decision");
    }

    #[test]
    fn a_best_of_command_is_a_turn_that_ends_and_leaves_the_input_free() {
        let mut app = main_app();
        let now = Instant::now();
        app.start_command("best-of", &["3", "fix", "the", "parser"], now);
        assert_eq!(last_value(&app), json!({"type": "command", "name": "best-of", "args": ["3", "fix", "the", "parser"]}));
        assert!(app.session.busy, "Esc can stop it");
        assert!(matches!(app.session.items.first(), Some(Item::User(text)) if text == "/best-of 3 fix the parser"));

        // Esc twice stops the turn the usual way.
        press(&mut app, KeyCode::Esc);
        press(&mut app, KeyCode::Esc);
        assert_eq!(last_value(&app), json!({"type": "cancel"}));

        feed(&mut app, &[started(), json!({"type": "turn_end"})]);
        assert!(!app.session.busy);
        assert!(app.command.is_some(), "the result has not come");
        feed(&mut app, &[json!({"type": "command_result", "name": "best-of", "ok": true, "message": "Done / Готово"})]);
        assert!(app.command.is_none() && !app.session.busy);
        // Typing and sending work again.
        typing(&mut app, "hello");
        assert_eq!(app.input, "hello");
        assert!(!app.input_locked());
    }

    fn submit_text(app: &mut App, text: &str) {
        app.input = text.to_string();
        app.cursor = text.chars().count();
        app.submit(Instant::now());
    }

    #[test]
    fn help_clear_and_copy_are_answered_here_and_never_reach_the_core() {
        let mut app = main_app();
        submit_text(&mut app, "/help");
        assert!(app.sent.is_empty());
        let shown = app.session.answer_from_end(1).expect("the help is in the conversation");
        assert!(shown.contains("`/resume`") && shown.contains("`/quit`"));

        submit_text(&mut app, "/copy");
        assert!(app.clipboard.as_deref().is_some_and(|text| text.contains("`/help`")));
        submit_text(&mut app, "/copy 2");
        assert!(app.toast.as_ref().is_some_and(|toast| toast.error), "there is no second answer");

        submit_text(&mut app, "/clear");
        assert!(app.session.items.is_empty() && app.sent.is_empty());
    }

    #[test]
    fn exit_and_bye_leave_like_quit() {
        for word in ["/quit", "/exit", "/BYE"] {
            let mut app = main_app();
            submit_text(&mut app, word);
            assert!(app.quit, "{word}");
            assert!(app.sent.is_empty());
        }
    }

    #[test]
    fn website_is_a_request_not_a_command() {
        let mut app = main_app();
        submit_text(&mut app, "/website a shop for tea");
        assert!(app.command.is_none(), "it is not sent as a `command`");
        assert_eq!(app.input, "/website a shop for tea", "it went the way of a request, which waits for the core");
    }

    #[test]
    fn a_resumed_run_is_a_turn_that_ends_with_turn_end_alone() {
        let mut app = main_app();
        app.start_command("resume", &[], Instant::now());
        assert_eq!(last_value(&app), json!({"type": "command", "name": "resume", "args": []}));
        assert!(app.session.busy, "Esc can stop it");
        assert!(app.session.items.is_empty(), "it has no request text of its own");
        feed(&mut app, &[json!({"type": "turn_end"})]);
        assert!(!app.session.busy && app.command.is_none(), "no command_result follows a resumed run");
    }

    #[test]
    fn a_resume_the_core_refuses_does_not_leave_the_turn_running() {
        let mut app = main_app();
        app.start_command("resume", &[], Instant::now());
        feed(&mut app, &[json!({"type": "command_result", "name": "resume", "ok": false, "message": "No saved checkpoint to resume."})]);
        assert!(!app.session.busy && app.command.is_none());
    }

    #[test]
    fn a_best_of_command_that_fails_at_once_does_not_leave_the_turn_running() {
        let mut app = main_app();
        app.start_command("best-of", &["x"], Instant::now());
        assert!(app.session.busy);
        feed(&mut app, &[json!({"type": "command_result", "name": "best-of", "ok": false, "message": "Usage / Использование"})]);
        assert!(!app.session.busy && app.command.is_none());
        assert!(app.toast.as_ref().is_some_and(|toast| toast.error));
    }

    #[test]
    fn arrows_and_enter_choose_an_executor_and_esc_gives_the_conversation_back() {
        let mut app = main_app();
        feed(&mut app, &[started(), json!({"type": "turn_end"})]);
        assert_eq!(app.arena_row, 0);
        press(&mut app, KeyCode::Down);
        press(&mut app, KeyCode::Down);
        press(&mut app, KeyCode::Down);
        assert_eq!(app.arena_row, 2, "the last row holds");
        press(&mut app, KeyCode::Up);
        assert_eq!(app.arena_row, 1);
        // A typed character is the person's own input; the arrows are the arena's only while the input is empty.
        typing(&mut app, "x");
        press(&mut app, KeyCode::Down);
        assert_eq!(app.arena_row, 1);
        press(&mut app, KeyCode::Backspace);
        press(&mut app, KeyCode::Esc);
        assert!(app.pane().is_none() && app.session.arena.is_none());
    }

    #[test]
    fn a_click_on_an_executors_row_opens_its_log_and_esc_goes_back() {
        let mut app = main_app();
        app.grid = Grid { cols: 120, rows: 40, cw: 10, ch: 20 };
        let now = Instant::now();
        feed(&mut app, &[started(), status("e2", "running", 2, json!({"state": "running", "summary": ""}), json!({"input_tokens": 1, "output_tokens": 1, "cost_approx": false}))]);
        app.input_rise = Tween::settled(1.0);
        draw_once(&mut app, now);
        click_on(&mut app, |h| matches!(h, Hit::Executor(1)), now);
        let log = app.log.as_ref().expect("the log of the second executor");
        assert_eq!(log.id, "e2");
        assert!(matches!(log.state, LogState::Failed(_)), "this test has no core to read the journal from");
        assert!(matches!(app.pane(), Some(Pane::Log(_))));
        // Esc is "back", even while a turn runs; it does not stop the turn.
        app.session.busy = true;
        press(&mut app, KeyCode::Esc);
        assert!(app.log.is_none() && matches!(app.pane(), Some(Pane::Arena(_))));
        assert!(app.stop_armed.is_none());
    }

    #[test]
    fn a_council_fills_its_cards_marks_the_chosen_one_and_lists_the_dissent() {
        let mut app = main_app();
        feed(
            &mut app,
            &[
                json!({"type": "council_started", "council_id": "c1", "question": "Which queue?", "members": [
                    {"executor_id": "m1", "angle": "simplicity", "session": "s1"}, {"executor_id": "m2", "angle": "security", "session": "s2"},
                    {"executor_id": "m3", "angle": "speed", "session": "s3"}, {"executor_id": "m4", "angle": "maintenance", "session": "s4"}]}),
                json!({"type": "council_card", "council_id": "c1", "executor_id": "m1", "angle": "simplicity", "decision": "Use a list", "argument": "It is enough"}),
                json!({"type": "council_card", "council_id": "c1", "executor_id": "m2", "angle": "security", "decision": "Use a bounded queue", "argument": "No unbounded growth"}),
                json!({"type": "council_objection", "council_id": "c1", "executor_id": "m2", "angle": "security", "risks": [{"target": "m1", "risk": "grows without limit"}]}),
            ],
        );
        let council = app.session.council.as_ref().expect("a council");
        assert_eq!(council.cards.len(), 4);
        assert_eq!(council.cards[0].decision.as_deref(), Some("Use a list"));
        assert_eq!(council.cards[2].decision, None, "the third card has not arrived");
        assert_eq!(council.cards[1].risks.len(), 1);
        assert!(council.cards.iter().all(|card| !council.chosen(card)));

        feed(
            &mut app,
            &[json!({"type": "council_verdict", "council_id": "c1", "chosen": "m2", "decision": "Use a bounded queue", "reason": "Safe and small.",
                "dissent": [{"executor_id": "m1", "angle": "simplicity", "why": "a list is enough"}]})],
        );
        let council = app.session.council.as_ref().unwrap();
        assert_eq!(council.cards.iter().map(|card| council.chosen(card)).collect::<Vec<_>>(), [false, true, false, false]);
        assert!(matches!(app.pane(), Some(Pane::Council(_))));
        // The screen draws without a panic at a narrow and a wide window.
        for cols in [70, 120] {
            app.grid = Grid { cols, rows: 40, cw: 10, ch: 20 };
            app.relayout(Instant::now());
            let mut buffer = ratatui::buffer::Buffer::empty(app.grid.full());
            view::draw(&app, &mut buffer, Instant::now());
        }
        press(&mut app, KeyCode::Esc);
        assert!(app.pane().is_none());
    }

    #[test]
    fn the_status_line_adds_up_the_main_stream_and_the_executors_and_names_the_tool_mode() {
        let mut app = main_app();
        feed(
            &mut app,
            &[
                json!({"type": "tool_mode", "mode": "native", "reason": "probe_ok"}),
                json!({"type": "usage", "input_tokens": 2000, "output_tokens": 100, "cost_usd": 0.5, "cost_approx": false}),
            ],
        );
        assert_eq!(app.session.tool_mode.as_deref(), Some("native"));
        assert_eq!(app.session.total_spend().label(false).as_deref(), Some("$0.50"));
        // A run's executors count by their latest snapshot, which replaces the one before.
        let usage = |cost: f64, approx: bool| json!({"input_tokens": 10, "output_tokens": 1, "cost_usd": cost, "cost_approx": approx});
        feed(&mut app, &[started(), status("e1", "running", 1, json!({"state": "unknown", "summary": ""}), usage(0.25, false))]);
        feed(&mut app, &[status("e1", "running", 2, json!({"state": "unknown", "summary": ""}), usage(0.5, false))]);
        assert_eq!(app.session.total_spend().label(false).as_deref(), Some("$1.00"));
        // One approximate price makes the sum approximate, and so does a call with no price at all.
        feed(&mut app, &[status("e2", "running", 1, json!({"state": "unknown", "summary": ""}), usage(0.25, true))]);
        assert_eq!(app.session.total_spend().label(false).as_deref(), Some("≈$1.25"));
        feed(&mut app, &[status("e3", "running", 1, json!({"state": "unknown", "summary": ""}), json!({"input_tokens": 7, "output_tokens": 1, "cost_approx": false}))]);
        assert_eq!(app.session.total_spend().label(false).as_deref(), Some("≈$1.25"));
        assert!(view::status_info(&app).iter().any(|(text, _)| text.contains('$')));
        // The cost stays in the total when the run is dismissed.
        press(&mut app, KeyCode::Esc);
        assert!(app.session.arena.is_none());
        assert_eq!(app.session.total_spend().label(false).as_deref(), Some("≈$1.25"));
    }

    #[test]
    fn without_any_price_the_status_line_counts_tokens_and_what_the_cache_did_not_serve() {
        let mut app = main_app();
        feed(
            &mut app,
            &[
                json!({"type": "usage", "input_tokens": 10000, "output_tokens": 500, "cache_read_tokens": 8000, "cost_approx": false}),
                json!({"type": "usage", "input_tokens": 5000, "output_tokens": 500}),
            ],
        );
        // The second call did not say what the cache served: the uncached part is an upper bound.
        assert_eq!(app.session.total_spend().label(false).as_deref(), Some("total 16.0k tok · ≈7.0k uncached"));
        assert_eq!(app.session.total_spend().label(true).as_deref(), Some("всего 16,0 тыс. ток · ≈7,0 тыс. некэш."));
        assert!(view::status_info(&app).iter().any(|(text, _)| text.contains("16")));
        feed(&mut app, &[json!({"type": "usage", "input_tokens": 1000, "output_tokens": 0, "cache_read_tokens": 1000})]);
        assert_eq!(app.session.total_spend().label(false).as_deref(), Some("total 17.0k tok · ≈7.0k uncached"));
        // Nobody said: no uncached part at all.
        let mut silent = main_app();
        feed(&mut silent, &[json!({"type": "usage", "input_tokens": 10000, "output_tokens": 500}), json!({"type": "usage", "input_tokens": 5000, "output_tokens": 500})]);
        assert_eq!(silent.session.total_spend().label(false).as_deref(), Some("total 16.0k tok"));
        // Nothing reported: nothing shown.
        assert!(view::status_info(&main_app()).is_empty());
    }

    #[test]
    fn the_status_line_shows_the_context_window_and_the_cached_share_instead_of_the_summed_spend() {
        let mut app = main_app();
        feed(&mut app, &[json!({"type": "usage", "input_tokens": 20000, "output_tokens": 500, "cache_read_tokens": 17000,
            "context_tokens": 27300, "context_limit": 400000})]);
        let line: String = view::status_info(&app).iter().map(|(text, _)| text.as_str()).collect();
        assert!(line.contains("27.3k / 400.0k · 85%"), "{line}");
        assert!(!line.contains("total"), "{line}");
        feed(&mut app, &[json!({"type": "usage", "input_tokens": 10, "output_tokens": 1, "cost_usd": 0.5, "cost_approx": false,
            "context_tokens": 27300, "context_limit": 400000})]);
        let line: String = view::status_info(&app).iter().map(|(text, _)| text.as_str()).collect();
        assert!(line.find("$0.50").is_some_and(|money| money < line.find("context").unwrap_or(0)), "{line}");
        // The cache's lifetime counts from the end of the call.
        let window = app.session.window.clone().expect("a window");
        assert_eq!(window.cache_left(window.at).as_secs(), 300);
        assert!(window.cache_left(window.at + Duration::from_secs(400)).is_zero());
        // A provider that does not report the cache gets no share and no tip.
        let mut silent = main_app();
        feed(&mut silent, &[json!({"type": "usage", "input_tokens": 100, "output_tokens": 5, "context_tokens": 100, "context_limit": 1000})]);
        assert_eq!(silent.session.window.map(|w| w.cached), Some(None));
    }

    #[test]
    fn the_arena_and_the_decision_dialog_are_drawn_on_the_screen() {
        let mut app = decision_app();
        app.grid = Grid { cols: 130, rows: 44, cw: 10, ch: 20 };
        app.input_rise = Tween::settled(1.0);
        app.input_pos = Tween::settled(1.0);
        let now = Instant::now();
        let shown = screen_text(&mut app, now + Duration::from_secs(2));
        for part in ["style-e1", "style-e2", "tests keep failing", "note e2", "e2 passes every test."] {
            assert!(shown.contains(part), "{part} on screen");
        }
        app.modal = None;
        let arena_only = screen_text(&mut app, now + Duration::from_secs(2));
        assert!(arena_only.contains("style-e3") && !arena_only.contains("note e2"));
    }

    #[test]
    fn a_session_switched_to_does_not_bring_back_a_finished_run_as_a_screen() {
        let mut app = app();
        let history = History {
            events: vec![
                json!({"type": "user", "text": "/best-of 2 fix"}),
                started(),
                verdict(),
                json!({"type": "bestof_finished", "run_id": "r1", "outcome": "discarded", "message": "Thrown away"}),
                json!({"type": "turn_end"}),
            ],
            has_more: false,
        };
        app.show_history(history, Instant::now());
        assert!(app.session.arena.is_none() && app.pane().is_none());
    }
}
