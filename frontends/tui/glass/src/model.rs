// What the interface knows about the session, built from the core's events
// (docs/UI_API.md §7) after protocol.rs has turned them into `CoreEvent`s.

use std::collections::HashMap;
use std::time::Instant;

use crate::arena::{Arena, ArenaDiff, Council, CouncilVerdict, Finished, Member, Risk, Spend, Started, Status, Usage, Verdict};
use crate::thought::Thought;

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
    /// A request from a session's journal; the live stream never carries one.
    User(String),
    Thinking,
    /// A piece of the model's thinking (`reasoning_chunk`); `append` tells whether it goes after what arrived.
    ThoughtText { text: String, append: bool },
    /// The thinking ended, after this many seconds (`reasoning_commit`).
    ThinkingDone { seconds: f32 },
    /// The cumulative text of a streamed answer (a snapshot, not a delta).
    Stream { id: String, text: String },
    Say(String),
    Step { call: String, kind: ToolKind, label: String },
    /// A call the model has finished writing while it writes the next (`call_begin`); its `Step` follows later.
    CallBegin { call: String, kind: ToolKind, label: String },
    Result { call: String, ok: bool, summary: String, diff: Option<(u32, u32)> },
    /// Whole new lines of a write's text while the model is still typing it
    /// (`call_stream`); `line` is where in `field` they begin.
    CallStream { call: String, tool: String, path: String, field: String, line: u32, text: String },
    /// A successful write; `patch` is its unified diff when the core sent one.
    FileChanged { path: String, added: u32, removed: u32, patch: Option<String> },
    /// One of the agent's virtual screens (the browser's, the app screen's); `screen_id` names its skill.
    Screen { screen_id: String, title: String, frame_url: String, stream_url: String, url: String },
    ScreenClosed { screen_id: String },
    /// A picture is being drawn in the background; the card waits for it.
    ImageDrawing { title: String },
    /// The picture could not be made.
    ImageFailed { error: String },
    /// A picture saved on disk for the person; shown in the browser's card.
    Image { title: String, path: String },
    Helper { id: String, task: String, state: HelperState, summary: String },
    /// A helper's call started (`helper_activity`, phase `step`).
    HelperStep { id: String, call: String, kind: ToolKind, label: String },
    /// A helper's call ended; `output` is its last lines.
    HelperResult { id: String, call: String, ok: bool, summary: String, output: String },
    SessionEnded { notice: String },
    Error(String),
    TurnEnd,
    /// One model call's tokens and price (`usage`).
    Spent(Usage),
    /// How the model's calls travel: `native` or `text` (`tool_mode`).
    ToolMode(String),
    BestofStarted(Started),
    BestofStatus(Status),
    /// An executor's call started (`executor_activity`, phase `step`).
    ExecutorStep { run_id: String, id: String, call: String, kind: ToolKind, label: String },
    /// An executor's call ended (`executor_activity`, phase `result`).
    ExecutorResult { run_id: String, id: String, call: String, ok: bool, summary: String },
    BestofEliminated { run_id: String, id: String, reason: String },
    BestofVerdict(Verdict),
    BestofDiff(ArenaDiff),
    BestofFinished(Finished),
    CouncilStarted { council_id: String, question: String, members: Vec<Member> },
    CouncilCard { council_id: String, id: String, angle: String, decision: String, argument: String },
    CouncilObjection { council_id: String, id: String, angle: String, risks: Vec<Risk> },
    CouncilVerdict(CouncilVerdict),
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
    /// The model's thinking, if the provider passes it on.
    Thought(Thought),
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
    /// The unified diff of each of this session's writes to the file, oldest first.
    pub patches: Vec<String>,
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
    /// The best-of run on screen, from its start until the person dismisses it.
    pub arena: Option<Arena>,
    pub council: Option<Council>,
    /// Tokens and money of the main stream and of the runs already dismissed.
    pub spend: Spend,
    pub tool_mode: Option<String>,
    pub busy: bool,
    pub thinking_since: Option<Instant>,
    /// Set once the session is closed: the notice and when it arrived.
    pub closed: Option<(String, Instant)>,
    /// Stream id -> index of its item in `items`.
    streams: HashMap<String, usize>,
    /// Calls shown from `call_begin` that no `Step` has confirmed yet.
    early: Vec<String>,
}

impl Session {
    pub fn submit(&mut self, text: String, now: Instant) {
        self.items.push(Item::User(text));
        self.begin_turn(now);
    }

    fn begin_turn(&mut self, now: Instant) {
        self.busy = true;
        self.thinking_since = Some(now);
        self.clear_screens();
    }

    /// Takes the arena and the council off the screen. What the executors
    /// spent stays in the total.
    pub fn clear_screens(&mut self) {
        if let Some(arena) = self.arena.take() {
            self.spend.merge(&arena.spend());
        }
        self.council = None;
    }

    /// Everything spent in this session: the main stream, and the executors of a run still on screen.
    pub fn total_spend(&self) -> Spend {
        let mut total = self.spend.clone();
        if let Some(arena) = &self.arena {
            total.merge(&arena.spend());
        }
        total
    }

    pub fn apply(&mut self, event: CoreEvent, now: Instant) {
        match event {
            CoreEvent::TurnStarted => self.begin_turn(now),
            CoreEvent::User(text) => self.items.push(Item::User(text)),
            CoreEvent::Thinking => {
                self.thinking_since = Some(now);
                self.open_thought(now);
            }
            CoreEvent::ThoughtText { text, append } => self.open_thought(now).push(&text, append),
            CoreEvent::ThinkingDone { seconds } => self.end_thought(now, seconds),
            CoreEvent::Stream { id, text } => self.stream(id, text, now),
            CoreEvent::Say(text) => self.say(text, now),
            CoreEvent::CallBegin { call, kind, label } => {
                let known = self.items.iter().any(|item| matches!(item, Item::Tool { call: c, .. } if *c == call));
                if !known {
                    self.early.push(call.clone());
                    self.items.push(Item::Tool { call, kind, label, state: CallState::Running });
                }
            }
            CoreEvent::Step { call, kind, label } if self.early.contains(&call) => {
                self.early.retain(|c| *c != call);
                for item in self.items.iter_mut() {
                    if let Item::Tool { call: c, kind: k, label: l, .. } = item
                        && *c == call
                    {
                        *k = kind;
                        *l = label;
                        break;
                    }
                }
                self.thinking_since = None;
            }
            CoreEvent::Step { call, kind, label } => {
                self.items.push(Item::Tool { call, kind, label, state: CallState::Running });
                self.thinking_since = None;
            }
            CoreEvent::Result { call, ok, summary, diff } => self.finish_call(&call, CallState::Done { ok, summary, diff }, now),
            CoreEvent::FileChanged { path, added, removed, patch } => self.change_file(path, added, removed, patch, now),
            // Only the side panel shows it (app.rs); the transcript waits for the result.
            CoreEvent::CallStream { .. } => {}
            // The frames themselves are fetched by the interface (screen.rs).
            CoreEvent::Screen { title, .. } | CoreEvent::Image { title, .. } | CoreEvent::ImageDrawing { title } => {
                self.browser = Some(Browser { title })
            }
            CoreEvent::ScreenClosed { .. } | CoreEvent::ImageFailed { .. } => self.browser = None,
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
                self.early.clear();
                self.thinking_since = None;
                // A turn stopped mid-thought never gets its `reasoning_commit`.
                if let Some(thought) = self.active_thought() {
                    let seconds = now.saturating_duration_since(thought.started()).as_secs_f32();
                    thought.finish(now, seconds);
                }
            }
            CoreEvent::Spent(usage) => self.spend.add(&usage),
            CoreEvent::ToolMode(mode) => self.tool_mode = Some(mode),
            other => self.apply_run(other),
        }
    }

    /// The best-of and council events. Those of a run other than the one on screen are not news.
    fn apply_run(&mut self, event: CoreEvent) {
        match event {
            CoreEvent::BestofStarted(started) => {
                self.clear_screens();
                self.arena = Some(Arena::new(started));
            }
            CoreEvent::BestofStatus(status) => {
                if let Some(arena) = self.arena.as_mut().filter(|a| a.run_id == status.run_id) {
                    arena.status(status);
                }
            }
            CoreEvent::ExecutorStep { run_id, id, call, kind, label } => {
                if let Some(arena) = self.arena.as_mut().filter(|a| a.run_id == run_id) {
                    arena.call_started(&id, call, kind, label);
                }
            }
            CoreEvent::ExecutorResult { run_id, id, call, ok, summary } => {
                if let Some(arena) = self.arena.as_mut().filter(|a| a.run_id == run_id) {
                    arena.call_finished(&id, &call, ok, summary);
                }
            }
            CoreEvent::BestofEliminated { run_id, id, reason } => {
                if let Some(arena) = self.arena.as_mut().filter(|a| a.run_id == run_id) {
                    arena.eliminate(&id, reason);
                }
            }
            CoreEvent::BestofVerdict(verdict) => {
                if let Some(arena) = self.arena.as_mut().filter(|a| a.run_id == verdict.run_id) {
                    arena.verdict = Some(verdict);
                }
            }
            CoreEvent::BestofDiff(diff) => {
                if let Some(arena) = self.arena.as_mut().filter(|a| a.run_id == diff.run_id) {
                    arena.diff = Some(diff);
                    arena.diff_fresh = true;
                }
            }
            CoreEvent::BestofFinished(finished) => {
                if let Some(arena) = self.arena.as_mut().filter(|a| a.run_id == finished.run_id) {
                    arena.finished = Some(finished);
                }
            }
            CoreEvent::CouncilStarted { council_id, question, members } => {
                self.clear_screens();
                self.council = Some(Council::new(council_id, question, members));
            }
            CoreEvent::CouncilCard { council_id, id, angle, decision, argument } => {
                if let Some(council) = self.council.as_mut().filter(|c| c.id == council_id) {
                    council.card(&id, &angle, decision, argument);
                }
            }
            CoreEvent::CouncilObjection { council_id, id, angle, risks } => {
                if let Some(council) = self.council.as_mut().filter(|c| c.id == council_id) {
                    council.objection(&id, &angle, risks);
                }
            }
            CoreEvent::CouncilVerdict(verdict) => {
                if let Some(council) = self.council.as_mut().filter(|c| c.id == verdict.council_id) {
                    council.verdict = Some(verdict);
                }
            }
            _ => {}
        }
    }

    /// A word from this interface itself (`/help`), set in the conversation like the agent's own.
    pub fn note(&mut self, text: String, now: Instant) {
        self.items.push(Item::Agent { text, at: now });
    }

    /// `/clear`: the conversation on screen starts over; files, helpers and spending stay.
    pub fn clear_view(&mut self) {
        self.items.clear();
        self.streams.clear();
        self.early.clear();
    }

    /// The text of the agent's `from_end`-th latest answer (1 = the last).
    pub fn answer_from_end(&self, from_end: usize) -> Option<&str> {
        self.items
            .iter()
            .rev()
            .filter_map(|item| match item {
                Item::Agent { text, .. } if !text.trim().is_empty() => Some(text.as_str()),
                _ => None,
            })
            .nth(from_end.saturating_sub(1))
    }

    /// The thought being written: the last item, while it has not ended.
    fn active_thought(&mut self) -> Option<&mut Thought> {
        match self.items.last_mut() {
            Some(Item::Thought(thought)) if thought.active() => Some(thought),
            _ => None,
        }
    }

    /// The thought being written; a new one starts when the last item is anything else.
    fn open_thought(&mut self, now: Instant) -> &mut Thought {
        if self.active_thought().is_none() {
            self.items.push(Item::Thought(Thought::new(now)));
        }
        match self.items.last_mut() {
            Some(Item::Thought(thought)) => thought,
            _ => unreachable!("a thought was just pushed"),
        }
    }

    fn end_thought(&mut self, now: Instant, seconds: f32) {
        let Some(thought) = self.active_thought() else { return };
        thought.finish(now, seconds);
        // With words shown, the folded line stands for the wait; without, the "thinking" row goes on.
        if thought.has_text() {
            self.thinking_since = None;
        }
    }

    /// Moves what changes by itself: the thinking window's reveal.
    pub fn tick(&mut self, now: Instant) {
        for item in self.items.iter_mut().rev().take(3) {
            if let Item::Thought(thought) = item {
                thought.tick(now);
            }
        }
    }

    /// True while the last thoughts still draw or fold.
    pub fn thinking_moves(&self, now: Instant) -> bool {
        self.items.iter().rev().take(3).any(|item| matches!(item, Item::Thought(t) if t.active() || t.folding(now)))
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
        if repeat == Some(true) {
            return;
        }
        // The words introduce calls that may already be on screen from `call_begin`.
        let first_early = self
            .items
            .iter()
            .position(|item| matches!(item, Item::Tool { call, .. } if self.early.contains(call)));
        match first_early {
            Some(at) => self.items.insert(at, Item::Agent { text, at: now }),
            None => self.items.push(Item::Agent { text, at: now }),
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

    fn change_file(&mut self, path: String, added: u32, removed: u32, patch: Option<String>, now: Instant) {
        if let Some(file) = self.files.iter_mut().find(|f| f.path == path) {
            file.previous = (file.added, file.removed);
            file.added += added;
            file.removed += removed;
            file.touched = now;
            file.patches.extend(patch);
            return;
        }
        let patches = patch.into_iter().collect();
        self.files.push(FileChange { path, added, removed, touched: now, previous: (0, 0), patches });
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

    /// Ends everything a replayed journal left running: a journal cut off by a
    /// crash has calls and helpers with no result, and nothing is running now.
    pub fn settle(&mut self) {
        let stopped = |state: &mut CallState| {
            if *state == CallState::Running {
                *state = CallState::Done { ok: false, summary: String::new(), diff: None };
            }
        };
        for item in &mut self.items {
            if let Item::Tool { state, .. } = item {
                stopped(state);
            }
        }
        for helper in &mut self.helpers {
            if helper.state == HelperState::Running {
                helper.state = HelperState::Failed;
            }
            for call in &mut helper.calls {
                stopped(&mut call.state);
            }
        }
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
        session.apply(CoreEvent::FileChanged { path: "a.py".into(), added: 3, removed: 1, patch: Some("one".into()) }, now);
        session.apply(CoreEvent::FileChanged { path: "a.py".into(), added: 2, removed: 0, patch: Some("two".into()) }, now);
        assert_eq!(session.files.len(), 1);
        assert_eq!((session.files[0].added, session.files[0].removed), (5, 1));
        assert_eq!(session.files[0].previous, (3, 1));
        assert_eq!(session.files[0].patches, ["one", "two"]);
    }

    fn thought_events(session: &mut Session, now: Instant) {
        session.submit("hi".into(), now);
        session.apply(CoreEvent::Thinking, now);
        session.apply(CoreEvent::ThoughtText { text: "a ".into(), append: true }, now);
        session.apply(CoreEvent::ThoughtText { text: "b".into(), append: true }, now);
    }

    #[test]
    fn thinking_chunks_gather_in_one_thought_that_ends_with_the_commit() {
        let now = Instant::now();
        let mut session = Session::default();
        thought_events(&mut session, now);
        assert!(matches!(&session.items[..], [Item::User(_), Item::Thought(t)] if t.active() && t.has_text()));
        session.apply(CoreEvent::ThinkingDone { seconds: 1.5 }, now);
        assert!(matches!(&session.items[1], Item::Thought(t) if !t.active()));
        assert!(session.thinking_since.is_none(), "the folded line stands for the wait");
    }

    #[test]
    fn a_thought_the_provider_hides_leaves_the_thinking_row_running() {
        let now = Instant::now();
        let mut session = Session::default();
        session.submit("hi".into(), now);
        session.apply(CoreEvent::Thinking, now);
        session.apply(CoreEvent::ThinkingDone { seconds: 4.0 }, now);
        assert!(matches!(&session.items[1], Item::Thought(t) if t.hidden()));
        assert!(session.thinking_since.is_some());
    }

    #[test]
    fn a_turn_stopped_mid_thought_closes_it() {
        let now = Instant::now();
        let mut session = Session::default();
        thought_events(&mut session, now);
        session.apply(CoreEvent::TurnEnd, now);
        assert!(matches!(&session.items[1], Item::Thought(t) if !t.active()));
    }

    #[test]
    fn thinking_after_a_call_starts_a_new_thought() {
        let now = Instant::now();
        let mut session = Session::default();
        thought_events(&mut session, now);
        session.apply(CoreEvent::ThinkingDone { seconds: 1.0 }, now);
        session.apply(CoreEvent::Step { call: "c1".into(), kind: ToolKind::Read, label: "read".into() }, now);
        session.apply(CoreEvent::Thinking, now);
        assert_eq!(session.items.iter().filter(|item| matches!(item, Item::Thought(_))).count(), 2);
    }

    #[test]
    fn a_write_the_core_sent_no_patch_for_still_counts() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::FileChanged { path: "a.py".into(), added: 1, removed: 0, patch: None }, now);
        assert_eq!(session.files[0].added, 1);
        assert!(session.files[0].patches.is_empty());
    }

    #[test]
    fn a_call_shown_early_is_filled_in_by_its_step_and_the_words_stay_above() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::CallBegin { call: "c1".into(), kind: ToolKind::Read, label: "read".into() }, now);
        session.apply(CoreEvent::CallBegin { call: "c2".into(), kind: ToolKind::Shell, label: "run".into() }, now);
        session.apply(CoreEvent::CallBegin { call: "c1".into(), kind: ToolKind::Read, label: "again".into() }, now);
        session.apply(CoreEvent::Say("Looking.".into()), now);
        session.apply(CoreEvent::Step { call: "c1".into(), kind: ToolKind::Read, label: "reads a.py".into() }, now);
        assert!(matches!(&session.items[..], [Item::Agent { .. }, Item::Tool { label, .. }, Item::Tool { .. }] if label == "reads a.py"));
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
    fn settling_ends_calls_and_helpers_a_cut_journal_left_running() {
        let now = Instant::now();
        let mut session = Session::default();
        session.apply(CoreEvent::Step { call: "c1".into(), kind: ToolKind::Read, label: "a".into() }, now);
        session.apply(CoreEvent::HelperStep { id: "h1".into(), call: "x".into(), kind: ToolKind::Shell, label: "t".into() }, now);
        session.settle();
        assert!(matches!(&session.items[0], Item::Tool { state: CallState::Done { ok: false, .. }, .. }));
        assert_eq!(session.helpers[0].state, HelperState::Failed);
        assert!(matches!(session.helpers[0].calls[0].state, CallState::Done { ok: false, .. }));
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
