// The modules of the voice screen: what Kvist is doing with her hands while the orb
// shows what is on her mind. Each tool she uses comes out of the ball as a small glass
// card (a browser, a terminal, a search), stays beside it while it works, shows how it
// ended, and goes back in. So the screen says "she is opening a page" instead of only
// "working".
//
// This file is the part without a terminal: which calls make which module, where the
// modules sit round the ball, and when each one comes and goes. Drawing is in voice.rs.
//
// A response of the model may hold dozens of calls of one kind (twelve files read at
// once), so the calls of one response that do the same kind of thing share a module,
// which counts them.

use std::collections::VecDeque;
use std::time::{Duration, Instant};

use ratatui::layout::Rect;

use crate::anim::{Ease, Tween};
use crate::layout::Grid;
use crate::orb::BALL;

/// How long a module takes to unfold out of the ball, and to fold back in. Both go by
/// `Ease::Fold`: a slow start, a fast middle, a slow end (its shape is in anim.rs).
const OPEN_MS: u64 = 780;
const CLOSE_MS: u64 = 620;
/// How long a module that has finished stays, so that its result can be read; the
/// model's thoughts, which only say that it has thought, go sooner.
const LINGER: Duration = Duration::from_millis(2600);
const LINGER_THOUGHTS: Duration = Duration::from_millis(900);
/// A module's card: two lines of text with a blank row above and below.
pub const HEIGHT: u16 = 4;
const WIDTH: u16 = 34;
const MIN_WIDTH: u16 = 22;
/// Three cards are taller and have places of their own beside the ball: the model's thoughts and
/// the terminal on the right (one after the other), the file being written on the left. Each is a
/// header, then up to `TALL_LINES` lines, between a blank row above and below.
pub const TALL_LINES: u16 = 7;
pub const TALL_HEIGHT: u16 = TALL_LINES + 3;
const TALL_WIDTH: u16 = 42;
/// Which tall card: an index into the rooms and places below.
pub const TALL_THOUGHTS: usize = 0;
pub const TALL_EDITS: usize = 1;
pub const TALL_SHELL: usize = 2;
pub const TALL_COUNT: usize = 3;
/// Their places in the list of places (the tool cards take 0.., these the ones after the last).
pub const TALL_SLOT: usize = 6;
/// Rows from one module's top to the next one's on the same side.
const PITCH: u16 = HEIGHT + 1;
/// How far from the ball's centre a card's near edge is, in ball radii: the middle
/// card stands off further than the ones above and below, so the row follows the ball.
const NEAR_MIDDLE: f32 = 1.46;
const NEAR_OUTER: f32 = 1.26;

// A module does not just slide out of the ball. By its way out (0 inside the ball, 1 in its
// place) it comes out as a thin line, widens as it travels, and when it is there opens up and
// down to its full height like a leaf unfolding; its words appear last. Going back is the
// same in reverse: the words go, it folds into a line, and the line draws into the ball.
const TRAVEL_END: f32 = 0.70;
const WIDEN: (f32, f32) = (0.0, 0.60);
const OPEN: (f32, f32) = (0.45, 0.90);
const TEXT_FROM: f32 = 0.88;

/// How much of itself a module shows at a point of its way out; each is 0..1.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct Fold {
    /// How far along the line from the ball's centre to its place the card is.
    pub travel: f32,
    pub width: f32,
    /// 0 is the line it comes out as, 1 the full card.
    pub height: f32,
    pub text: f32,
}

pub fn fold(out: f32) -> Fold {
    let out = out.clamp(0.0, 1.0);
    let part = |(from, to): (f32, f32)| smooth((out - from) / (to - from));
    Fold { travel: part((0.0, TRAVEL_END)), width: part(WIDEN), height: part(OPEN), text: ((out - TEXT_FROM) / (1.0 - TEXT_FROM)).clamp(0.0, 1.0) }
}

/// Eases in and out, so the joins between the phases of a fold are not corners.
fn smooth(x: f32) -> f32 {
    let x = x.clamp(0.0, 1.0);
    x * x * (3.0 - 2.0 * x)
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Kind {
    Files,
    Search,
    WebSearch,
    Page,
    Edit,
    Shell,
    Git,
    Helper,
    Skill,
    Memory,
    Picture,
    Plan,
    Timer,
    /// What the model thinks, when its provider lets the thinking be seen: not a call, so no
    /// action names it (the screen opens it from the model's thinking, see voice.rs).
    Thoughts,
    Other,
}

/// The module a call belongs to; `None` for a call that is not work to show (a question
/// to the person is a dialog, and the words that end a turn are spoken).
pub fn kind(action_type: &str) -> Option<Kind> {
    Some(match action_type {
        "read_file" | "read_multiple_files" | "list_dir" | "list_tree" | "find_files" => Kind::Files,
        "search_code" => Kind::Search,
        "web_search" => Kind::WebSearch,
        "fetch_url" => Kind::Page,
        "write_file" | "append_file" | "replace_in_file" => Kind::Edit,
        "run_shell" | "job_output" | "job_stop" => Kind::Shell,
        "git_status" | "git_diff" => Kind::Git,
        "delegate_task" | "helper" => Kind::Helper,
        "skill_call" | "skills" => Kind::Skill,
        "memory" | "sessions" => Kind::Memory,
        "generate_image" => Kind::Picture,
        "offer_plan" | "propose_plan" | "edit_plan" | "cancel_plan" => Kind::Plan,
        "wait" | "schedule" => Kind::Timer,
        "ask_user" | "final_answer" | "report_to_user" | "refuse" | "end_session" => return None,
        _ => Kind::Other,
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Status {
    Running,
    Done,
    Failed,
}

#[derive(Clone, Debug)]
pub struct Module {
    /// The model response and the kind of work: calls with the same key share the module.
    key: String,
    pub kind: Kind,
    /// A name that says more than the kind does (a skill's own name); empty for the kind's.
    pub title: String,
    /// What the newest call does, or how the last one ended.
    pub detail: String,
    /// Each call with its outcome once it has one.
    calls: Vec<(String, Option<bool>)>,
    /// Which place round the ball it has.
    pub slot: usize,
    /// 0 inside the ball, 1 in its place.
    pub out: Tween,
    ended: Option<Instant>,
    /// On its way back into the ball: new calls no longer join it.
    leaving: bool,
    /// When it came out of the ball.
    pub since: Instant,
    /// What a write puts in the file, as a diff (the card of an edit shows it).
    pub preview: Option<Preview>,
    /// The command a terminal card shows, and what it printed.
    pub shell: Option<Shell>,
}

/// A command and its output, for the terminal card.
#[derive(Clone, Debug, PartialEq)]
pub struct Shell {
    /// The call it belongs to: a response may run several commands, and the card shows the newest.
    pub call: String,
    pub command: String,
    /// What it printed, once it has ended.
    pub output: Option<String>,
    pub exit: Option<i64>,
    /// When the output came, so that it can be shown line by line, as a terminal would.
    pub got: Option<Instant>,
}

/// A write's diff for the card, with how many lines it adds and removes.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Preview {
    pub path: String,
    /// A unified diff.
    pub patch: String,
    pub added: u32,
    pub removed: u32,
}

impl Module {
    pub fn status(&self) -> Status {
        if self.calls.iter().any(|(_, outcome)| outcome.is_none()) {
            return Status::Running;
        }
        if self.calls.iter().any(|(_, outcome)| *outcome == Some(false)) { Status::Failed } else { Status::Done }
    }

    /// Calls finished and calls in all.
    pub fn progress(&self) -> (usize, usize) {
        (self.calls.iter().filter(|(_, outcome)| outcome.is_some()).count(), self.calls.len())
    }

    #[cfg(test)]
    pub fn leaving(&self) -> bool {
        self.leaving
    }

    /// Which tall card this module is, if it is one.
    pub fn tall(&self) -> Option<usize> {
        match self.kind {
            Kind::Thoughts => Some(TALL_THOUGHTS),
            // These two are tall only when there is something to show in them: a write's diff, a command.
            Kind::Edit if self.preview.is_some() => Some(TALL_EDITS),
            Kind::Shell if self.shell.is_some() => Some(TALL_SHELL),
            _ => None,
        }
    }

    fn linger(&self) -> Duration {
        if self.kind == Kind::Thoughts { LINGER_THOUGHTS } else { LINGER }
    }

    fn leave(&mut self, now: Instant) {
        self.leaving = true;
        self.out.go(0.0, now, CLOSE_MS, Ease::Fold);
    }
}

#[derive(Default)]
pub struct Modules {
    shown: Vec<Module>,
    /// Modules that found every place taken; they come out as places free up.
    waiting: VecDeque<Module>,
    /// For each tall card: whether the window has room for it, and which of the places it covers.
    tall_room: [Option<Vec<usize>>; TALL_COUNT],
}

impl Modules {
    pub fn shown(&self) -> &[Module] {
        &self.shown
    }

    fn find(&mut self, by: impl Fn(&Module) -> bool) -> Option<&mut Module> {
        self.shown.iter_mut().chain(self.waiting.iter_mut()).find(|module| by(module))
    }

    /// A call starts: `batch` names the model response it came from, `call` the call itself.
    pub fn begin(&mut self, batch: &str, call: &str, kind: Kind, title: &str, detail: &str) {
        if let Some(module) = self.find(|module| module.calls.iter().any(|(id, _)| id == call)) {
            // Announced while the model was still writing, now really starting: the same call.
            module.detail = detail.to_string();
            return;
        }
        let group = if batch.is_empty() { call } else { batch };
        let key = format!("{group}/{kind:?}/{title}");
        if let Some(module) = self.find(|module| module.key == key && !module.leaving) {
            module.calls.push((call.to_string(), None));
            module.detail = detail.to_string();
            module.ended = None;
            return;
        }
        self.waiting.push_back(Module {
            key,
            kind,
            title: title.to_string(),
            detail: detail.to_string(),
            calls: vec![(call.to_string(), None)],
            slot: 0,
            out: Tween::settled(0.0),
            ended: None,
            leaving: false,
            since: Instant::now(),
            preview: None,
            shell: None,
        });
    }

    /// What a call that is still going on says now (the model's thought as it grows).
    pub fn set_detail(&mut self, call: &str, detail: &str) {
        if let Some(module) = self.find(|module| module.calls.iter().any(|(id, _)| id == call)) {
            module.detail = detail.to_string();
        }
    }

    /// Whether this call has a module, shown or waiting.
    pub fn has(&self, call: &str) -> bool {
        self.shown.iter().chain(self.waiting.iter()).any(|module| module.calls.iter().any(|(id, _)| id == call))
    }

    /// A call ended; `summary` is the core's one line about how.
    pub fn end(&mut self, call: &str, ok: bool, summary: &str, now: Instant) {
        let Some(module) = self.find(|module| module.calls.iter().any(|(id, _)| id == call)) else { return };
        for (id, outcome) in &mut module.calls {
            if id == call {
                *outcome = Some(ok);
            }
        }
        if !summary.is_empty() {
            module.detail = summary.to_string();
        }
        if module.status() != Status::Running {
            module.ended = Some(now);
        }
    }

    /// The turn is over: a call that never reported an outcome is not running any more.
    pub fn finish_all(&mut self, now: Instant) {
        for module in self.shown.iter_mut().chain(self.waiting.iter_mut()) {
            for (_, outcome) in &mut module.calls {
                outcome.get_or_insert(true);
            }
            module.ended.get_or_insert(now);
        }
    }

    /// Another session: none of this belongs to it.
    pub fn clear(&mut self) {
        self.shown.clear();
        self.waiting.clear();
    }

    /// Where a tall card would stand: the places it covers, or `None` without room for it.
    pub fn set_tall_room(&mut self, tall: usize, covered: Option<Vec<usize>>) {
        self.tall_room[tall] = covered;
    }

    /// The command a terminal card shows.
    pub fn set_shell(&mut self, call: &str, command: &str) {
        let shell = Shell { call: call.to_string(), command: command.to_string(), output: None, exit: None, got: None };
        if let Some(module) = self.find(|module| module.calls.iter().any(|(id, _)| id == call)) {
            module.shell = Some(shell);
        }
    }

    /// Whether `call` is the command a terminal card shows.
    pub fn is_shell_call(&self, call: &str) -> bool {
        self.shown.iter().chain(self.waiting.iter()).any(|module| module.shell.as_ref().is_some_and(|shell| shell.call == call))
    }

    /// The command of `call` has ended: what it printed, and how it ended. Ignored when the card
    /// has gone on to a newer command.
    pub fn finish_shell(&mut self, call: &str, output: &str, exit: Option<i64>, now: Instant) {
        let Some(module) = self.find(|module| module.calls.iter().any(|(id, _)| id == call)) else { return };
        if let Some(shell) = module.shell.as_mut().filter(|shell| shell.call == call) {
            shell.output = Some(output.to_string());
            shell.exit = exit;
            shell.got = Some(now);
        }
    }

    /// What a write will put in the file (from its arguments), or has put (from its result).
    pub fn set_preview(&mut self, call: &str, preview: Preview) {
        if let Some(module) = self.find(|module| module.calls.iter().any(|(id, _)| id == call)) {
            module.preview = Some(preview);
        }
    }

    /// Per frame: modules that are back in the ball are gone, finished ones start to leave,
    /// and waiting ones take the places that are free. `places` is how many the window has.
    pub fn tick(&mut self, places: usize, now: Instant) {
        self.shown.retain(|module| !module.leaving || module.out.running(now));
        let crowded = !self.waiting.is_empty();
        for module in &mut self.shown {
            let stayed = module.ended.is_some_and(|at| now.saturating_duration_since(at) >= module.linger());
            let settled = !module.out.running(now);
            // With others waiting for a place, a finished module leaves as soon as it has arrived.
            if !module.leaving && module.ended.is_some() && settled && (stayed || crowded) {
                module.leave(now);
            }
        }
        // A module that finished before it got a place has nothing left to show.
        self.waiting.retain(|module| module.ended.is_none_or(|at| now.saturating_duration_since(at) < module.linger()));
        // While a tall card is up, the places it covers are not for tools: finished cards in
        // them give way, and new ones take other places.
        let present = |tall: usize| {
            self.shown.iter().chain(self.waiting.iter()).any(|module| module.tall() == Some(tall) && !module.leaving)
        };
        let mut covered: Vec<usize> = Vec::new();
        for tall in 0..TALL_COUNT {
            if let (Some(room), true) = (&self.tall_room[tall], present(tall)) {
                covered.extend(room);
            }
        }
        for module in &mut self.shown {
            let tool = module.slot < TALL_SLOT;
            if !module.leaving && tool && module.ended.is_some() && covered.contains(&module.slot) {
                module.leave(now);
            }
        }
        let mut next = 0;
        while next < self.waiting.len() {
            let free = |slot: &usize| self.shown.iter().all(|module| module.slot != *slot);
            let module = &self.waiting[next];
            let tall_room = module.tall().filter(|tall| self.tall_room[*tall].is_some());
            let slot = match (module.kind, tall_room) {
                (_, Some(tall)) => Some(TALL_SLOT + tall).filter(free),
                // The thoughts have no card without room for it; a write has a plain one.
                (Kind::Thoughts, None) => None,
                _ => (0..places).find(|slot| free(slot) && !covered.contains(slot)),
            };
            let Some(slot) = slot else {
                next += 1;
                continue;
            };
            let Some(mut module) = self.waiting.remove(next) else { break };
            module.slot = slot;
            module.since = now;
            module.out.go(1.0, now, OPEN_MS, Ease::Fold);
            self.shown.push(module);
        }
    }
}

/// Where a tall card stands: beside the ball, level with its middle, the thoughts on the right
/// and the edits on the left. `None` when the window has no room there.
pub fn tall_place(grid: Grid, orb: Rect, tall: usize) -> Option<Rect> {
    let (cw, ch) = (grid.cw as f32, grid.ch as f32);
    let centre_x = (orb.x as f32 + orb.width as f32 / 2.0) * cw;
    let radius = orb.height as f32 * ch / 2.0 * BALL;
    let right = ((centre_x + radius * NEAR_MIDDLE) / cw).ceil() as u16;
    let left_edge = ((centre_x - radius * NEAR_MIDDLE) / cw).floor() as u16;
    let top = (orb.y + orb.height / 2).checked_sub(TALL_HEIGHT / 2)?;
    if top + TALL_HEIGHT + 1 > grid.rows {
        return None;
    }
    // The thoughts and the terminal share the right of the ball; they are not up together.
    if tall != TALL_EDITS {
        let room = grid.cols.saturating_sub(right + 2);
        return (room >= MIN_WIDTH).then(|| Rect::new(right, top, room.min(TALL_WIDTH), TALL_HEIGHT));
    }
    let room = left_edge.saturating_sub(2);
    (room >= MIN_WIDTH).then(|| {
        let width = room.min(TALL_WIDTH);
        Rect::new(left_edge - width, top, width, TALL_HEIGHT)
    })
}

/// Where the modules sit round the ball, in cells, in the order they are taken: beside
/// the ball on the right and on the left, then above those, then below. Empty when the
/// window has no room beside the ball. `orb` is the orb's picture in cells.
pub fn places(grid: Grid, orb: Rect) -> Vec<Rect> {
    let (cw, ch) = (grid.cw as f32, grid.ch as f32);
    let centre_x = (orb.x as f32 + orb.width as f32 / 2.0) * cw;
    let radius = orb.height as f32 * ch / 2.0 * BALL;
    let middle = orb.y + orb.height / 2;
    let Some(top) = middle.checked_sub(HEIGHT / 2) else { return Vec::new() };
    // The cards above and below must stay inside the orb's rows: the card under the orb owns the rest.
    let stacked = top >= orb.y + PITCH && top + PITCH + HEIGHT <= orb.y + orb.height;
    let mut found = Vec::new();
    let rows: &[(i32, f32)] = if stacked {
        &[(0, NEAR_MIDDLE), (-(PITCH as i32), NEAR_OUTER), (PITCH as i32, NEAR_OUTER)]
    } else {
        &[(0, NEAR_MIDDLE)]
    };
    for &(shift, near) in rows {
        let y = (top as i32 + shift) as u16;
        let right = ((centre_x + radius * near) / cw).ceil() as u16;
        let left_edge = ((centre_x - radius * near) / cw).floor() as u16;
        let room = grid.cols.saturating_sub(right + 2).min(left_edge.saturating_sub(2));
        if room < MIN_WIDTH {
            continue;
        }
        let width = room.min(WIDTH);
        found.push(Rect::new(right, y, width, HEIGHT));
        found.push(Rect::new(left_edge - width, y, width, HEIGHT));
    }
    found
}

/// Whether a place is to the right of the ball (its twig then meets its left edge).
pub fn on_the_right(place: Rect, orb: Rect) -> bool {
    place.x >= orb.x + orb.width / 2
}

#[cfg(test)]
mod tests {
    use super::*;

    fn grid(cols: u16, rows: u16) -> Grid {
        Grid { cols, rows, cw: 9, ch: 20 }
    }

    fn after(start: Instant, ms: u64) -> Instant {
        start + Duration::from_millis(ms)
    }

    #[test]
    fn every_action_the_core_names_has_a_module_or_is_left_to_speech() {
        assert_eq!(kind("run_shell"), Some(Kind::Shell));
        assert_eq!(kind("fetch_url"), Some(Kind::Page));
        assert_eq!(kind("web_search"), Some(Kind::WebSearch));
        assert_eq!(kind("skill_call"), Some(Kind::Skill));
        assert_eq!(kind("replace_in_file"), Some(Kind::Edit));
        assert_eq!(kind("something_new"), Some(Kind::Other));
        for spoken in ["final_answer", "report_to_user", "refuse", "end_session", "ask_user"] {
            assert_eq!(kind(spoken), None, "{spoken}");
        }
    }

    #[test]
    fn a_module_comes_out_works_shows_its_result_and_goes_back() {
        let start = Instant::now();
        let mut modules = Modules::default();
        modules.begin("b1", "c1", Kind::Page, "", "opens youtube.com");
        modules.tick(6, start);
        let module = &modules.shown()[0];
        assert_eq!((module.slot, module.status(), module.detail.as_str()), (0, Status::Running, "opens youtube.com"));
        assert_eq!(module.out.value(start), 0.0, "it starts inside the ball");
        assert_eq!(module.out.value(after(start, OPEN_MS)), 1.0);

        // However long it works, it stays.
        modules.tick(6, after(start, 60_000));
        assert_eq!(modules.shown().len(), 1);

        modules.end("c1", true, "Fetched 2000 characters", after(start, 60_000));
        modules.tick(6, after(start, 60_100));
        let module = &modules.shown()[0];
        assert_eq!((module.status(), module.detail.as_str()), (Status::Done, "Fetched 2000 characters"));
        assert_eq!(module.out.target(), 1.0, "the result stays to be read");

        let leaves = after(start, 60_000 + LINGER.as_millis() as u64);
        modules.tick(6, leaves);
        assert_eq!(modules.shown()[0].out.target(), 0.0);
        modules.tick(6, after(leaves, CLOSE_MS + 1));
        assert!(modules.shown().is_empty());
    }

    #[test]
    fn calls_of_one_response_that_do_the_same_kind_of_thing_share_a_module() {
        let start = Instant::now();
        let mut modules = Modules::default();
        for call in ["c1", "c2", "c3"] {
            modules.begin("b1", call, Kind::Files, "", &format!("reads {call}.rs"));
        }
        modules.begin("b1", "c4", Kind::Search, "", "search \"orb\"");
        modules.tick(6, start);
        assert_eq!(modules.shown().len(), 2);
        let files = &modules.shown()[0];
        assert_eq!((files.progress(), files.detail.as_str()), ((0, 3), "reads c3.rs"));

        modules.end("c1", true, "120 lines", start);
        modules.end("c2", false, "No such file", start);
        assert_eq!(modules.shown()[0].status(), Status::Running, "one is still being read");
        modules.end("c3", true, "80 lines", start);
        let files = &modules.shown()[0];
        assert_eq!((files.progress(), files.status()), ((3, 3), Status::Failed));

        // The next response reads files again: that is new work, with its own module.
        modules.begin("b2", "c5", Kind::Files, "", "reads d.rs");
        modules.tick(6, start);
        assert_eq!(modules.shown().len(), 3);
    }

    #[test]
    fn a_call_announced_early_is_the_same_call_when_it_starts() {
        let mut modules = Modules::default();
        modules.begin("", "c1", Kind::Shell, "", "");
        modules.begin("b1", "c1", Kind::Shell, "", "runs the tests");
        modules.tick(6, Instant::now());
        assert_eq!(modules.shown().len(), 1);
        assert_eq!((modules.shown()[0].progress(), modules.shown()[0].detail.as_str()), ((0, 1), "runs the tests"));
    }

    #[test]
    fn skills_are_told_apart_by_their_names() {
        let mut modules = Modules::default();
        modules.begin("b1", "c1", Kind::Skill, "browser", "opens youtube.com");
        modules.begin("b1", "c2", Kind::Skill, "calendar", "adds an interview");
        modules.tick(6, Instant::now());
        assert_eq!(modules.shown().len(), 2);
    }

    #[test]
    fn a_module_without_a_place_waits_for_one() {
        let start = Instant::now();
        let mut modules = Modules::default();
        modules.begin("b1", "c1", Kind::Shell, "", "one");
        modules.begin("b1", "c2", Kind::Page, "", "two");
        modules.begin("b1", "c3", Kind::Search, "", "three");
        modules.tick(2, start);
        assert_eq!(modules.shown().len(), 2, "two places, the third waits");

        // The first finishes; with another waiting it does not linger once it has arrived.
        modules.end("c1", true, "ok", after(start, 100));
        modules.tick(2, after(start, 200));
        assert_eq!(modules.shown()[0].out.target(), 1.0, "it is still on its way out");
        modules.tick(2, after(start, OPEN_MS + 10));
        assert_eq!(modules.shown()[0].out.target(), 0.0);
        modules.tick(2, after(start, OPEN_MS + CLOSE_MS + 20));
        let places: Vec<(usize, &str)> = modules.shown().iter().map(|m| (m.slot, m.detail.as_str())).collect();
        assert_eq!(places, [(1, "two"), (0, "three")]);
    }

    #[test]
    fn the_end_of_the_turn_ends_whatever_still_ran() {
        let start = Instant::now();
        let mut modules = Modules::default();
        modules.begin("b1", "c1", Kind::Shell, "", "a server that never answers");
        modules.tick(6, start);
        modules.finish_all(after(start, 1000));
        assert_eq!(modules.shown()[0].status(), Status::Done);
        modules.tick(6, after(start, 1000 + LINGER.as_millis() as u64 + CLOSE_MS + 50));
        modules.tick(6, after(start, 1000 + LINGER.as_millis() as u64 + 2 * CLOSE_MS + 100));
        assert!(modules.shown().is_empty());
        modules.begin("b2", "c2", Kind::Shell, "", "x");
        modules.clear();
        modules.tick(6, start);
        assert!(modules.shown().is_empty());
    }

    #[test]
    fn a_card_comes_out_as_a_line_widens_on_the_way_and_opens_once_it_is_there() {
        let nothing = Fold { travel: 0.0, width: 0.0, height: 0.0, text: 0.0 };
        let all = Fold { travel: 1.0, width: 1.0, height: 1.0, text: 1.0 };
        assert_eq!((fold(0.0), fold(1.0)), (nothing, all));
        // Wide before tall: half way out it is a broad thin line.
        let half = fold(0.5);
        assert!(half.width > 0.9 && half.height < 0.1, "{half:?}");
        // It has arrived before it is open, and its words wait for the last of the opening.
        let arrived = fold(TRAVEL_END);
        assert_eq!(arrived.travel, 1.0);
        assert!(arrived.height < 0.6);
        let nearly = fold(0.85);
        assert!(nearly.height > 0.9 && nearly.text == 0.0, "{nearly:?}");
        // Nothing runs backwards on the way out, so the way back (the same reversed) does not either.
        let mut before = fold(0.0);
        for step in 1..=100 {
            let now = fold(step as f32 / 100.0);
            assert!(now.travel >= before.travel && now.width >= before.width && now.height >= before.height && now.text >= before.text);
            before = now;
        }
    }

    #[test]
    fn the_thoughts_open_with_the_thinking_and_close_soon_after_it_ends() {
        let start = Instant::now();
        let mut modules = Modules::default();
        modules.set_tall_room(TALL_THOUGHTS, Some(vec![0, 2, 4]));
        modules.begin("", "thoughts:1", Kind::Thoughts, "", "");
        assert!(modules.has("thoughts:1") && !modules.has("thoughts:2"));
        modules.tick(6, start);
        modules.set_detail("thoughts:1", "the person wants the video about glaciers");
        assert_eq!(modules.shown()[0].detail, "the person wants the video about glaciers");

        // However long it thinks, the card stays; it says what the thought was when it ends.
        modules.tick(6, after(start, 30_000));
        assert_eq!(modules.shown()[0].out.target(), 1.0);
        let ended = after(start, 30_000);
        modules.end("thoughts:1", true, "", ended);
        assert_eq!(modules.shown()[0].detail, "the person wants the video about glaciers");

        // It goes sooner than a tool's card does, which has a result to be read.
        assert!(LINGER_THOUGHTS < LINGER);
        modules.tick(6, after(ended, LINGER_THOUGHTS.as_millis() as u64 - 100));
        assert_eq!(modules.shown()[0].out.target(), 1.0);
        modules.tick(6, after(ended, LINGER_THOUGHTS.as_millis() as u64 + 10));
        assert_eq!(modules.shown()[0].out.target(), 0.0);

        // The next thought is a card of its own, that opens again once the old one has folded away.
        modules.begin("", "thoughts:2", Kind::Thoughts, "", "");
        modules.tick(6, after(ended, LINGER_THOUGHTS.as_millis() as u64 + 20));
        assert_eq!(modules.shown().len(), 1, "the place is still the old card's");
        modules.tick(6, after(ended, LINGER_THOUGHTS.as_millis() as u64 + CLOSE_MS + 50));
        modules.tick(6, after(ended, LINGER_THOUGHTS.as_millis() as u64 + CLOSE_MS + 60));
        assert_eq!((modules.shown().len(), modules.shown()[0].out.target()), (1, 1.0));
        assert!(modules.has("thoughts:2") && !modules.has("thoughts:1"));
    }

    #[test]
    fn the_thoughts_card_has_the_right_of_the_ball_and_tools_keep_out_of_its_way() {
        let start = Instant::now();
        let mut modules = Modules::default();
        modules.set_tall_room(TALL_THOUGHTS, Some(vec![0, 2, 4]));
        // A tool that has finished stands where the thoughts will be.
        modules.begin("b1", "c1", Kind::Page, "", "opens a page");
        modules.tick(6, start);
        modules.end("c1", true, "ok", start);
        modules.begin("", "thoughts:1", Kind::Thoughts, "", "so");
        modules.tick(6, after(start, 100));
        assert_eq!(modules.shown().iter().find(|m| m.kind == Kind::Page).unwrap().out.target(), 0.0, "it gives way");
        let thoughts = modules.shown().iter().find(|m| m.kind == Kind::Thoughts).unwrap();
        assert_eq!(thoughts.slot, TALL_SLOT + TALL_THOUGHTS);

        // A new tool does not take the places the card covers (right middle, right above and below).
        modules.begin("b2", "c2", Kind::Shell, "", "runs");
        modules.begin("b2", "c3", Kind::Search, "", "searches");
        modules.begin("b2", "c4", Kind::Files, "", "reads");
        modules.tick(6, after(start, 200));
        let mut taken: Vec<usize> = modules.shown().iter().filter(|m| m.kind != Kind::Thoughts && !m.leaving).map(|m| m.slot).collect();
        taken.sort();
        assert_eq!(taken, [1, 3, 5], "the left side");

        // Without room for it, no card.
        let mut cramped = Modules::default();
        cramped.set_tall_room(TALL_THOUGHTS, None);
        cramped.begin("", "thoughts:1", Kind::Thoughts, "", "so");
        cramped.tick(6, start);
        assert!(cramped.shown().is_empty());
    }

    #[test]
    fn the_tall_cards_stand_beside_the_ball_the_thoughts_on_the_right_and_the_edits_on_the_left() {
        let wide = grid(213, 54);
        let orb = Rect::new(68, 6, 77, 34);
        let thoughts = tall_place(wide, orb, TALL_THOUGHTS).unwrap();
        let edits = tall_place(wide, orb, TALL_EDITS).unwrap();
        assert_eq!((thoughts.width, thoughts.height), (TALL_WIDTH, TALL_HEIGHT));
        assert_eq!((edits.width, edits.height), (TALL_WIDTH, TALL_HEIGHT));
        assert!(on_the_right(thoughts, orb) && !on_the_right(edits, orb));
        let middle = orb.y + orb.height / 2;
        for card in [thoughts, edits] {
            assert!(card.y < middle && card.bottom() > middle, "level with the ball's middle");
        }
        assert!(TALL_HEIGHT > HEIGHT);
        // The ball far to the right leaves no room on that side for the thoughts, and the left one still has it.
        let right_ball = Rect::new(130, 6, 77, 34);
        assert!(tall_place(wide, right_ball, TALL_THOUGHTS).is_none());
        assert!(tall_place(wide, right_ball, TALL_EDITS).is_some());
        // No room on either side, or none up and down.
        assert!(tall_place(grid(60, 24), Rect::new(14, 1, 32, 15), TALL_EDITS).is_none());
        assert!(tall_place(grid(213, 10), Rect::new(68, 1, 77, 8), TALL_THOUGHTS).is_none());
    }

    #[test]
    fn a_write_gets_the_tall_card_on_the_left_when_there_is_room_and_a_plain_one_when_there_is_not() {
        let start = Instant::now();
        let mut modules = Modules::default();
        modules.set_tall_room(TALL_EDITS, Some(vec![1, 3, 5]));
        modules.begin("b1", "c1", Kind::Edit, "", "writes a.py");
        modules.set_preview("c1", Preview { path: "a.py".into(), patch: "@@ -0,0 +1 @@\n+y\n".into(), added: 1, removed: 0 });
        modules.begin("b1", "c2", Kind::Edit, "", "writes b.py");
        modules.begin("b1", "c3", Kind::Shell, "", "runs the tests");
        modules.tick(6, start);
        let edit = modules.shown().iter().find(|m| m.kind == Kind::Edit).unwrap();
        assert_eq!((edit.slot, edit.progress()), (TALL_SLOT + TALL_EDITS, (0, 2)), "two writes of a response share a card");
        modules.set_preview("c2", Preview { path: "b.py".into(), patch: "@@ -0,0 +1 @@\n+x\n".into(), added: 1, removed: 0 });
        assert_eq!(modules.shown().iter().find(|m| m.kind == Kind::Edit).unwrap().preview.as_ref().unwrap().path, "b.py");
        let shell = modules.shown().iter().find(|m| m.kind == Kind::Shell).unwrap();
        assert!(![1, 3, 5].contains(&shell.slot), "a tool keeps out of the places the card covers");

        let mut cramped = Modules::default();
        cramped.begin("b1", "c1", Kind::Edit, "", "writes a.py");
        cramped.set_preview("c1", Preview { path: "a.py".into(), patch: "@@ -0,0 +1 @@\n+y\n".into(), added: 1, removed: 0 });
        cramped.tick(6, start);
        assert!(cramped.shown()[0].slot < TALL_SLOT, "without room it is an ordinary card");
    }

    #[test]
    fn a_command_gets_the_terminal_card_and_its_output_is_kept_for_the_call_that_made_it() {
        let start = Instant::now();
        let mut modules = Modules::default();
        modules.set_tall_room(TALL_SHELL, Some(vec![0, 2, 4]));
        modules.begin("b1", "c1", Kind::Shell, "", "runs the tests");
        modules.set_shell("c1", "pytest -q");
        modules.begin("b1", "c2", Kind::Shell, "", "lists the files");
        modules.set_shell("c2", "ls");
        modules.tick(6, start);
        assert_eq!(modules.shown().len(), 1, "two commands of a response share the card");
        assert_eq!(modules.shown()[0].slot, TALL_SLOT + TALL_SHELL);
        // The first command ends after the card has gone on to the second: its output is not the card's.
        modules.finish_shell("c1", "3 passed", Some(0), start);
        assert_eq!(modules.shown()[0].shell.as_ref().unwrap().output, None);
        modules.finish_shell("c2", "a.py\nb.py", Some(0), start);
        let shell = modules.shown()[0].shell.as_ref().unwrap();
        assert_eq!((shell.command.as_str(), shell.output.as_deref(), shell.exit), ("ls", Some("a.py\nb.py"), Some(0)));

        // A background job's card has no command to show: an ordinary card.
        let mut jobs = Modules::default();
        jobs.set_tall_room(TALL_SHELL, Some(vec![0, 2, 4]));
        jobs.begin("b1", "c1", Kind::Shell, "", "reads a job");
        jobs.tick(6, start);
        assert!(jobs.shown()[0].slot < TALL_SLOT);
    }

    #[test]
    fn a_wide_window_has_six_places_round_the_ball_and_none_touches_it() {
        let grid = grid(213, 54);
        let orb = Rect::new(68, 6, 77, 34);
        let found = places(grid, orb);
        assert_eq!(found.len(), 6);
        let centre = (orb.x as f32 + orb.width as f32 / 2.0) * 9.0;
        let radius = orb.height as f32 * 20.0 / 2.0 * BALL;
        for place in &found {
            assert_eq!((place.width, place.height), (WIDTH, HEIGHT));
            assert!(place.y >= orb.y && place.bottom() <= orb.bottom(), "{place:?} stays in the orb's rows");
            let near = if on_the_right(*place, orb) { place.x as f32 * 9.0 - centre } else { centre - place.right() as f32 * 9.0 };
            assert!(near >= radius * 1.2, "{place:?} keeps clear of the ball");
        }
        assert!(on_the_right(found[0], orb) && !on_the_right(found[1], orb), "right first, then left");
        assert_eq!(found[0].y, found[1].y);
        assert!(found[2].y < found[0].y && found[4].y > found[0].y, "then above, then below");
        // No two share a cell.
        for a in 0..found.len() {
            for b in a + 1..found.len() {
                assert!(!found[a].intersects(found[b]), "{:?} and {:?}", found[a], found[b]);
            }
        }
    }

    #[test]
    fn a_small_window_keeps_one_place_each_side_and_a_narrow_one_none() {
        // Eleven rows of orb cannot hold three cards one above another.
        let small = places(grid(100, 18), Rect::new(38, 1, 25, 11));
        assert_eq!(small.len(), 2);
        assert!(small[0].width >= MIN_WIDTH && small[0].right() <= 99);
        assert!(places(grid(60, 24), Rect::new(14, 1, 32, 15)).is_empty());
    }
}
