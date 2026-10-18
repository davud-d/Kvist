// What the interface knows about the session, built from the core's events
// (docs/UI_API.md §7) after protocol.rs has turned them into `CoreEvent`s.

use std::collections::HashMap;
use std::time::Instant;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ToolKind {
    Read,
    Search,
    Write,
    Shell,
    Web,
    Other,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum HelperState {
    Running,
    Done,
    Failed,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CoreEvent {
    /// A turn began that the person did not type (`wake`).
    TurnStarted,
    Thinking,
    ThinkingDone,
    /// The cumulative text of a streamed answer (a snapshot, not a delta).
    Stream { id: String, text: String },
    Say(String),
    Step { call: String, kind: ToolKind, label: String },
    Result { call: String, ok: bool, summary: String, diff: Option<(u32, u32)> },
    FileChanged { path: String, added: u32, removed: u32 },
    Screen { title: String, frame_url: String },
    ScreenClosed,
    Helper { id: String, task: String, state: HelperState, summary: String },
    /// A helper's call started (`helper_activity`, phase `step`).
    HelperStep { id: String, call: String, kind: ToolKind, label: String },
    /// A helper's call ended; `output` is its last lines.
    HelperResult { id: String, call: String, ok: bool, summary: String, output: String },
    SessionEnded { notice: String },
    Error(String),
    TurnEnd,
}

#[derive(Clone, Debug, PartialEq)]
pub enum CallState {
    Running,
    Done { ok: bool, summary: String, diff: Option<(u32, u32)> },
}

#[derive(Clone, Debug)]
pub enum Item {
    User(String),
    /// The agent's words; `at` drives the typewriter reveal.
    Agent { text: String, at: Instant },
    Tool { call: String, kind: ToolKind, label: String, state: CallState },
    Error(String),
}

#[derive(Clone, Debug)]
pub struct FileChange {
    pub path: String,
    pub added: u32,
    pub removed: u32,
    /// When the counts last changed: the row glows and counts up from here.
    pub touched: Instant,
    pub previous: (u32, u32),
}

#[derive(Clone, Debug)]
pub struct HelperCall {
    pub call: String,
    pub kind: ToolKind,
    pub label: String,
    pub state: CallState,
    pub output: String,
}

#[derive(Clone, Debug)]
pub struct Helper {
    pub id: String,
    pub task: String,
    pub state: HelperState,
    pub summary: String,
    pub calls: Vec<HelperCall>,
}

#[derive(Clone, Debug)]
pub struct Browser {
    pub title: String,
}

#[derive(Default)]
pub struct Session {
    pub ready: bool,
    pub items: Vec<Item>,
    pub files: Vec<FileChange>,
    pub helpers: Vec<Helper>,
    pub browser: Option<Browser>,
    pub busy: bool,
    pub thinking_since: Option<Instant>,
    /// Set once the session is closed: the notice and when it arrived.
    pub closed: Option<(String, Instant)>,
    /// Stream id -> index of its item in `items`.
    streams: HashMap<String, usize>,
}

impl Session {
    pub fn submit(&mut self, text: String, now: Instant) {
        self.items.push(Item::User(text));
        self.begin_turn(now);
    }

    fn begin_turn(&mut self, now: Instant) {
        self.busy = true;
        self.thinking_since = Some(now);
    }

    pub fn apply(&mut self, event: CoreEvent, now: Instant) {
        match event {
            CoreEvent::TurnStarted => self.begin_turn(now),
            CoreEvent::Thinking => self.thinking_since = Some(now),
            CoreEvent::ThinkingDone => {}
            CoreEvent::Stream { id, text } => self.stream(id, text, now),
            CoreEvent::Say(text) => self.say(text, now),
            CoreEvent::Step { call, kind, label } => {
                self.items.push(Item::Tool { call, kind, label, state: CallState::Running });
                self.thinking_since = None;
            }
            CoreEvent::Result { call, ok, summary, diff } => self.finish_call(&call, CallState::Done { ok, summary, diff }, now),
            CoreEvent::FileChanged { path, added, removed } => self.change_file(path, added, removed, now),
            // The frames themselves are fetched by the interface (screen.rs).
            CoreEvent::Screen { title, .. } => self.browser = Some(Browser { title }),
            CoreEvent::ScreenClosed => self.browser = None,
            CoreEvent::Helper { id, task, state, summary } => self.update_helper(id, task, state, summary),
            CoreEvent::HelperStep { id, call, kind, label } => {
                let helper = self.helper_mut(&id);
                helper.calls.push(HelperCall { call, kind, label, state: CallState::Running, output: String::new() });
            }
            CoreEvent::HelperResult { id, call, ok, summary, output } => {
                let helper = self.helper_mut(&id);
                if let Some(entry) = helper.calls.iter_mut().rev().find(|c| c.call == call) {
                    entry.state = CallState::Done { ok, summary, diff: None };
                    entry.output = output;
                }
            }
            CoreEvent::SessionEnded { notice } => self.closed = Some((notice, now)),
            CoreEvent::Error(text) => self.items.push(Item::Error(text)),
            CoreEvent::TurnEnd => {
                self.busy = false;
                self.thinking_since = None;
            }
        }
    }

    fn stream(&mut self, id: String, text: String, now: Instant) {
        self.thinking_since = None;
        if let Some(&index) = self.streams.get(&id)
            && let Some(Item::Agent { text: shown, .. }) = self.items.get_mut(index)
        {
            *shown = text;
            return;
        }
        self.streams.insert(id, self.items.len());
        self.items.push(Item::Agent { text, at: now });
    }

    /// The agent's words; a final answer that repeats the reply just streamed
    /// is not shown twice.
    fn say(&mut self, text: String, now: Instant) {
        self.thinking_since = None;
        if text.trim().is_empty() {
            return;
        }
        let repeat = self.items.iter().rev().find_map(|item| match item {
            Item::Agent { text: last, .. } => Some(last.trim() == text.trim()),
            Item::User(_) => Some(false),
            _ => None,
        });
        if repeat != Some(true) {
            self.items.push(Item::Agent { text, at: now });
        }
    }

    fn finish_call(&mut self, call: &str, done: CallState, now: Instant) {
        let found = self.items.iter_mut().rev().find_map(|item| match item {
            Item::Tool { call: c, state, .. } if c == call => Some(state),
            _ => None,
        });
        if let Some(state) = found {
            *state = done;
        }
        // The model thinks again until its next call or words arrive.
        self.thinking_since = Some(now);
    }

    fn change_file(&mut self, path: String, added: u32, removed: u32, now: Instant) {
        if let Some(file) = self.files.iter_mut().find(|f| f.path == path) {
            file.previous = (file.added, file.removed);
            file.added += added;
            file.removed += removed;
            file.touched = now;
            return;
        }
        self.files.push(FileChange { path, added, removed, touched: now, previous: (0, 0) });
    }

    fn update_helper(&mut self, id: String, task: String, state: HelperState, summary: String) {
        let helper = self.helper_mut(&id);
        helper.state = state;
        if !task.is_empty() {
            helper.task = task;
        }
        if !summary.is_empty() {
            helper.summary = summary;
        }
    }

    /// The helper with this id; an activity event may arrive before the
    /// helper's own notice, so an unknown id starts a running helper.
    fn helper_mut(&mut self, id: &str) -> &mut Helper {
        let index = match self.helpers.iter().position(|h| h.id == id) {
            Some(index) => index,
            None => {
                self.helpers.push(Helper {
                    id: id.to_string(),
                    task: String::new(),
                    state: HelperState::Running,
                    summary: String::new(),
                    calls: Vec::new(),
                });
                self.helpers.len() - 1
            }
        };
        &mut self.helpers[index]
    }

    pub fn totals(&self) -> (u32, u32) {
        self.files.iter().fold((0, 0), |(a, r), f| (a + f.added, r + f.removed))
    }

    /// A fresh session on the same connection: the core is still ready.
    pub fn fresh() -> Self {
        Session { ready: true, ..Session::default() }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_second_write_to_a_file_adds_to_its_counts() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::FileChanged { path: "a.py".into(), added: 3, removed: 1 }, now);
        session.apply(CoreEvent::FileChanged { path: "a.py".into(), added: 2, removed: 0 }, now);
        assert_eq!(session.files.len(), 1);
        assert_eq!((session.files[0].added, session.files[0].removed), (5, 1));
        assert_eq!(session.files[0].previous, (3, 1));
    }

    #[test]
    fn a_result_finishes_its_own_call() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::Step { call: "c1".into(), kind: ToolKind::Read, label: "a".into() }, now);
        session.apply(CoreEvent::Step { call: "c2".into(), kind: ToolKind::Read, label: "b".into() }, now);
        session.apply(CoreEvent::Result { call: "c1".into(), ok: true, summary: "ok".into(), diff: None }, now);
        let done: Vec<bool> = session
            .items
            .iter()
            .map(|item| matches!(item, Item::Tool { state: CallState::Done { .. }, .. }))
            .collect();
        assert_eq!(done, vec![true, false]);
    }

    #[test]
    fn stream_snapshots_replace_the_text_of_one_message() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::Stream { id: "s".into(), text: "При".into() }, now);
        session.apply(CoreEvent::Stream { id: "s".into(), text: "Привет".into() }, now);
        assert_eq!(session.items.len(), 1);
        assert!(matches!(&session.items[0], Item::Agent { text, .. } if text == "Привет"));
    }

    #[test]
    fn a_final_answer_that_repeats_the_stream_is_shown_once() {
        let now = Instant::now();
        let mut session = Session::default();
        session.submit("q".into(), now);
        session.apply(CoreEvent::Stream { id: "s".into(), text: "Готово.".into() }, now);
        session.apply(CoreEvent::Say("Готово.".into()), now);
        assert_eq!(session.items.len(), 2);
        session.apply(CoreEvent::Say("Ещё одно.".into()), now);
        assert_eq!(session.items.len(), 3);
    }

    #[test]
    fn helper_updates_keep_the_task_they_started_with() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::Helper { id: "h1".into(), task: "tests".into(), state: HelperState::Running, summary: String::new() }, now);
        session.apply(CoreEvent::Helper { id: "h1".into(), task: String::new(), state: HelperState::Done, summary: "48 passed".into() }, now);
        assert_eq!(session.helpers.len(), 1);
        assert_eq!(session.helpers[0].task, "tests");
        assert_eq!(session.helpers[0].summary, "48 passed");
        assert_eq!(session.helpers[0].state, HelperState::Done);
    }

    #[test]
    fn a_helpers_calls_run_and_finish_in_place() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::HelperStep { id: "h1".into(), call: "a".into(), kind: ToolKind::Shell, label: "pytest".into() }, now);
        session.apply(CoreEvent::HelperResult { id: "h1".into(), call: "a".into(), ok: true, summary: "exit 0".into(), output: "48 passed".into() }, now);
        let call = &session.helpers[0].calls[0];
        assert_eq!(session.helpers[0].state, HelperState::Running);
        assert!(matches!(&call.state, CallState::Done { ok: true, .. }));
        assert_eq!(call.output, "48 passed");
    }

    #[test]
    fn an_ended_session_keeps_its_notice_and_a_fresh_one_is_open() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::SessionEnded { notice: "closed".into() }, now);
        assert_eq!(session.closed.as_ref().map(|(n, _)| n.as_str()), Some("closed"));
        let fresh = Session::fresh();
        assert!(fresh.ready && fresh.closed.is_none() && fresh.items.is_empty());
    }
}
