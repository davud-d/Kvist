// What the core reports about a best-of run and a council (docs/UI_API.md,
// the `bestof_*`, `executor_activity` and `council_*` events), the money and
// tokens spent, and the state those events build up. protocol.rs fills these
// types from the JSON; model.rs hands each event to the arena or the council.

use crate::model::{CallState, ToolKind};

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum ExecState {
    Waiting,
    Running,
    Finished,
    Failed,
    Stopped,
    Eliminated,
    Lost,
}

impl ExecState {
    /// `None` for a word this interface does not know, so the row keeps its old state.
    pub fn parse(word: &str) -> Option<ExecState> {
        Some(match word {
            "waiting" => ExecState::Waiting,
            "running" => ExecState::Running,
            "finished" => ExecState::Finished,
            "failed" => ExecState::Failed,
            "stopped" => ExecState::Stopped,
            "eliminated" => ExecState::Eliminated,
            "lost" => ExecState::Lost,
            _ => return None,
        })
    }

    /// Out of the race: the row is shown muted.
    pub fn out(self) -> bool {
        matches!(self, ExecState::Eliminated | ExecState::Failed | ExecState::Stopped | ExecState::Lost)
    }
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub enum TestState {
    #[default]
    Unknown,
    Running,
    Passed,
    Failed,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Tests {
    pub state: TestState,
    pub summary: String,
}

#[derive(Clone, Copy, Debug, Default, PartialEq)]
pub struct DiffStat {
    pub files: u32,
    pub added: u32,
    pub removed: u32,
}

/// Tokens and money of one agent: the core's `usage` of one call, or the
/// running total of an executor in `bestof_status`.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Usage {
    pub input: u64,
    pub output: u64,
    pub cache_read: Option<u64>,
    /// `None` when the model has no price (a subscription, a gateway): unknown, never zero.
    pub cost: Option<f64>,
    pub approx: bool,
}

/// Several usages added up. Whether the sum is money or only tokens depends
/// on whether any of them was priced.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Spend {
    pub tokens: u64,
    /// Input the cache did not serve; for a call that did not say, all its input (an upper bound).
    pub uncached: u64,
    /// Calls that said how much the cache served, and calls that did not.
    pub cache_known: u32,
    pub cache_unknown: u32,
    pub cost: f64,
    pub priced: u32,
    pub unpriced: u32,
    pub approx: bool,
}

impl Spend {
    pub fn add(&mut self, usage: &Usage) {
        let spent = usage.input + usage.output;
        if spent == 0 && usage.cost.is_none() {
            return;
        }
        self.tokens += spent;
        match usage.cache_read {
            Some(served) => {
                self.uncached += usage.input.saturating_sub(served);
                self.cache_known += 1;
            }
            None => {
                self.uncached += usage.input;
                self.cache_unknown += 1;
            }
        }
        match usage.cost {
            Some(cost) => {
                self.cost += cost;
                self.priced += 1;
                self.approx |= usage.approx;
            }
            None => self.unpriced += 1,
        }
    }

    pub fn merge(&mut self, other: &Spend) {
        self.tokens += other.tokens;
        self.uncached += other.uncached;
        self.cache_known += other.cache_known;
        self.cache_unknown += other.cache_unknown;
        self.cost += other.cost;
        self.priced += other.priced;
        self.unpriced += other.unpriced;
        self.approx |= other.approx;
    }

    /// "≈$0.42" when anything was priced (≈ when a price is approximate or
    /// some calls have none), else "total 12.3k tok · 4.5k uncached"; `None`
    /// before the first call. The uncached part is an upper bound ("≈") when
    /// some call did not say what the cache served, and left out when none did.
    pub fn label(&self, russian: bool) -> Option<String> {
        if self.tokens == 0 && self.priced == 0 {
            return None;
        }
        if self.priced > 0 {
            let sign = if self.approx || self.unpriced > 0 { "≈" } else { "" };
            return Some(format!("{sign}${}", money(self.cost)));
        }
        let tokens = count(self.tokens, russian);
        let mut label = if russian { format!("всего {tokens} ток") } else { format!("total {tokens} tok") };
        if self.cache_known > 0 {
            let sign = if self.cache_unknown > 0 { "≈" } else { "" };
            let uncached = count(self.uncached, russian);
            label.push_str(&if russian { format!(" · {sign}{uncached} некэш.") } else { format!(" · {sign}{uncached} uncached") });
        }
        Some(label)
    }
}

/// Enough digits for a small amount to show: a price of a tenth of a cent is not "$0.00".
fn money(cost: f64) -> String {
    let digits = if cost >= 0.1 { 2 } else if cost >= 0.01 { 3 } else { 4 };
    format!("{cost:.digits$}")
}

/// 950, 12.3k, 1.4M (in Russian: 12,3 тыс., 1,4 млн).
pub fn count(n: u64, russian: bool) -> String {
    let (value, unit) = match n {
        0..1_000 => return n.to_string(),
        1_000..1_000_000 => (n as f64 / 1e3, if russian { " тыс." } else { "k" }),
        _ => (n as f64 / 1e6, if russian { " млн" } else { "M" }),
    };
    let text = format!("{value:.1}");
    format!("{}{unit}", if russian { text.replace('.', ",") } else { text })
}

#[derive(Clone, Debug, PartialEq)]
pub struct ExecutorInfo {
    pub id: String,
    pub style: String,
    pub branch: String,
    pub session: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Started {
    pub run_id: String,
    pub task: String,
    pub count: u32,
    pub base: String,
    pub executors: Vec<ExecutorInfo>,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Status {
    pub run_id: String,
    pub id: String,
    pub style: String,
    pub branch: String,
    pub state: Option<ExecState>,
    pub step: u32,
    pub tests: Tests,
    pub diff: DiffStat,
    pub usage: Option<Usage>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Outcome {
    Winner,
    Finalist,
    Eliminated,
    Failed,
}

impl Outcome {
    pub fn parse(word: &str) -> Option<Outcome> {
        Some(match word {
            "winner" => Outcome::Winner,
            "finalist" => Outcome::Finalist,
            "eliminated" => Outcome::Eliminated,
            "failed" => Outcome::Failed,
            _ => return None,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct VerdictRow {
    pub id: String,
    pub style: String,
    pub outcome: Option<Outcome>,
    pub tests: Tests,
    pub diff: DiffStat,
    pub note: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Verdict {
    pub run_id: String,
    pub rows: Vec<VerdictRow>,
    pub winner: String,
    pub reason: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct ArenaDiff {
    pub run_id: String,
    pub id: String,
    pub patch: String,
    pub files: Vec<String>,
}

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Finish {
    Accepted,
    Discarded,
    Cancelled,
    Failed,
    Pending,
}

impl Finish {
    pub fn parse(word: &str) -> Option<Finish> {
        Some(match word {
            "accepted" => Finish::Accepted,
            "discarded" => Finish::Discarded,
            "cancelled" => Finish::Cancelled,
            "failed" => Finish::Failed,
            "pending" => Finish::Pending,
            _ => return None,
        })
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Finished {
    pub run_id: String,
    pub outcome: Option<Finish>,
    pub id: String,
    pub commit: String,
    pub message: String,
}

/// The executor's latest call, for its row.
#[derive(Clone, Debug, PartialEq)]
pub struct ExecCall {
    pub call: String,
    pub kind: ToolKind,
    pub label: String,
    pub state: CallState,
}

#[derive(Clone, Debug)]
pub struct Executor {
    pub id: String,
    pub style: String,
    pub branch: String,
    pub session: String,
    pub state: ExecState,
    pub step: u32,
    pub tests: Tests,
    pub diff: DiffStat,
    pub usage: Option<Usage>,
    /// Why it is out, in the core's words.
    pub reason: String,
    pub last: Option<ExecCall>,
}

impl Executor {
    fn new(id: &str) -> Executor {
        Executor {
            id: id.to_string(),
            style: String::new(),
            branch: String::new(),
            session: String::new(),
            state: ExecState::Waiting,
            step: 0,
            tests: Tests::default(),
            diff: DiffStat::default(),
            usage: None,
            reason: String::new(),
            last: None,
        }
    }
}

/// One best-of run, from `bestof_started` until the person has dismissed it.
#[derive(Clone, Debug)]
pub struct Arena {
    pub run_id: String,
    pub task: String,
    pub count: u32,
    pub base: String,
    pub executors: Vec<Executor>,
    pub verdict: Option<Verdict>,
    /// The patch the person asked to see.
    pub diff: Option<ArenaDiff>,
    /// The patch arrived and no decision dialog has shown it yet.
    pub diff_fresh: bool,
    pub finished: Option<Finished>,
}

impl Arena {
    pub fn new(started: Started) -> Arena {
        let executors = started
            .executors
            .into_iter()
            .map(|info| Executor { style: info.style, branch: info.branch, session: info.session, ..Executor::new(&info.id) })
            .collect();
        Arena {
            run_id: started.run_id,
            task: started.task,
            count: started.count,
            base: started.base,
            executors,
            verdict: None,
            diff: None,
            diff_fresh: false,
            finished: None,
        }
    }

    /// The executor with this id; a status may arrive for one `bestof_started` did not list.
    fn executor_mut(&mut self, id: &str) -> &mut Executor {
        let at = match self.executors.iter().position(|e| e.id == id) {
            Some(at) => at,
            None => {
                self.executors.push(Executor::new(id));
                self.executors.len() - 1
            }
        };
        &mut self.executors[at]
    }

    /// A snapshot replaces what the row showed before. A word for the state
    /// that is not known leaves the old one, and an empty style or branch
    /// does not erase the name the row has.
    pub fn status(&mut self, status: Status) {
        let executor = self.executor_mut(&status.id);
        if !status.style.is_empty() {
            executor.style = status.style;
        }
        if !status.branch.is_empty() {
            executor.branch = status.branch;
        }
        if let Some(state) = status.state {
            executor.state = state;
        }
        executor.step = status.step;
        executor.tests = status.tests;
        executor.diff = status.diff;
        executor.usage = status.usage;
    }

    pub fn call_started(&mut self, id: &str, call: String, kind: ToolKind, label: String) {
        self.executor_mut(id).last = Some(ExecCall { call, kind, label, state: CallState::Running });
    }

    /// The result of the latest call; a result of an older one is no news for the row.
    pub fn call_finished(&mut self, id: &str, call: &str, ok: bool, summary: String) {
        if let Some(last) = self.executor_mut(id).last.as_mut().filter(|last| last.call == call) {
            last.state = CallState::Done { ok, summary, diff: None };
        }
    }

    pub fn eliminate(&mut self, id: &str, reason: String) {
        let executor = self.executor_mut(id);
        executor.state = ExecState::Eliminated;
        executor.reason = reason;
    }

    /// What every executor has spent so far, by its latest snapshot.
    pub fn spend(&self) -> Spend {
        let mut spend = Spend::default();
        for usage in self.executors.iter().filter_map(|e| e.usage.as_ref()) {
            spend.add(usage);
        }
        spend
    }
}

#[derive(Clone, Debug, PartialEq)]
pub struct Member {
    pub id: String,
    pub angle: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Risk {
    pub target: String,
    pub risk: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Dissent {
    /// The angle of the member who disagrees, or its id when the core named no angle.
    pub who: String,
    pub why: String,
}

#[derive(Clone, Debug, PartialEq)]
pub struct CouncilVerdict {
    pub council_id: String,
    pub chosen: String,
    pub decision: String,
    pub reason: String,
    pub dissent: Vec<Dissent>,
}

#[derive(Clone, Debug)]
pub struct Card {
    pub id: String,
    pub angle: String,
    /// `None` until the member's card arrives.
    pub decision: Option<String>,
    pub argument: String,
    pub risks: Vec<Risk>,
}

#[derive(Clone, Debug)]
pub struct Council {
    pub id: String,
    pub question: String,
    pub cards: Vec<Card>,
    pub verdict: Option<CouncilVerdict>,
}

impl Council {
    pub fn new(id: String, question: String, members: Vec<Member>) -> Council {
        let cards = members
            .into_iter()
            .map(|member| Card { id: member.id, angle: member.angle, decision: None, argument: String::new(), risks: Vec::new() })
            .collect();
        Council { id, question, cards, verdict: None }
    }

    fn card_mut(&mut self, id: &str, angle: &str) -> &mut Card {
        let at = match self.cards.iter().position(|c| c.id == id) {
            Some(at) => at,
            None => {
                self.cards.push(Card { id: id.to_string(), angle: angle.to_string(), decision: None, argument: String::new(), risks: Vec::new() });
                self.cards.len() - 1
            }
        };
        let card = &mut self.cards[at];
        if card.angle.is_empty() {
            card.angle = angle.to_string();
        }
        card
    }

    pub fn card(&mut self, id: &str, angle: &str, decision: String, argument: String) {
        let card = self.card_mut(id, angle);
        card.decision = Some(decision);
        card.argument = argument;
    }

    pub fn objection(&mut self, id: &str, angle: &str, risks: Vec<Risk>) {
        self.card_mut(id, angle).risks = risks;
    }

    /// Whether `card` is the one the verdict chose: `chosen` names an
    /// executor, or its angle.
    pub fn chosen(&self, card: &Card) -> bool {
        self.verdict.as_ref().is_some_and(|v| !v.chosen.is_empty() && (v.chosen == card.id || v.chosen.eq_ignore_ascii_case(&card.angle)))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn usage(input: u64, output: u64, cache: Option<u64>, cost: Option<f64>, approx: bool) -> Usage {
        Usage { input, output, cache_read: cache, cost, approx }
    }

    #[test]
    fn priced_calls_add_up_to_money_and_a_missing_price_makes_it_approximate() {
        let mut spend = Spend::default();
        spend.add(&usage(1000, 100, None, Some(0.25), false));
        spend.add(&usage(500, 50, None, Some(0.0125), false));
        assert_eq!(spend.label(false).as_deref(), Some("$0.26"));
        spend.add(&usage(400, 40, None, None, false));
        assert_eq!(spend.label(false).as_deref(), Some("≈$0.26"));
        let mut small = Spend::default();
        small.add(&usage(5, 1, None, Some(0.0034), false));
        assert_eq!(small.label(false).as_deref(), Some("$0.0034"), "a tenth of a cent is not $0.00");
    }

    #[test]
    fn an_approximate_price_marks_the_whole_sum() {
        let mut spend = Spend::default();
        spend.add(&usage(10, 1, None, Some(1.5), false));
        spend.add(&usage(10, 1, None, Some(0.5), true));
        assert_eq!(spend.label(true).as_deref(), Some("≈$2.00"));
    }

    #[test]
    fn without_any_price_it_is_tokens_and_the_uncached_part_when_every_call_said_what_the_cache_served() {
        let mut spend = Spend::default();
        spend.add(&usage(10_000, 2_000, Some(6_000), None, false));
        spend.add(&usage(3_000, 300, Some(0), None, false));
        assert_eq!((spend.tokens, spend.uncached), (15_300, 7_000));
        assert_eq!(spend.label(false).as_deref(), Some("total 15.3k tok · 7.0k uncached"));
        assert_eq!(spend.label(true).as_deref(), Some("всего 15,3 тыс. ток · 7,0 тыс. некэш."));
    }

    #[test]
    fn a_call_that_did_not_say_what_the_cache_served_makes_the_uncached_part_an_upper_bound() {
        let mut spend = Spend::default();
        spend.add(&usage(10_000, 2_000, Some(6_000), None, false));
        spend.add(&usage(3_000, 300, None, None, false));
        assert_eq!(spend.uncached, 7_000, "all the input of the call that did not say");
        assert_eq!(spend.label(false).as_deref(), Some("total 15.3k tok · ≈7.0k uncached"));
        assert_eq!(spend.label(true).as_deref(), Some("всего 15,3 тыс. ток · ≈7,0 тыс. некэш."));
    }

    #[test]
    fn when_no_call_said_what_the_cache_served_the_uncached_part_is_left_out() {
        let mut spend = Spend::default();
        spend.add(&usage(10_000, 2_000, None, None, false));
        spend.add(&usage(3_000, 300, None, None, false));
        assert_eq!(spend.label(false).as_deref(), Some("total 15.3k tok"));
        assert_eq!(spend.label(true).as_deref(), Some("всего 15,3 тыс. ток"));
    }

    #[test]
    fn merged_spends_keep_what_is_known_about_the_cache() {
        let (mut known, mut unknown) = (Spend::default(), Spend::default());
        known.add(&usage(1_000, 0, Some(400), None, false));
        unknown.add(&usage(1_000, 0, None, None, false));
        known.merge(&unknown);
        assert_eq!((known.cache_known, known.cache_unknown, known.uncached), (1, 1, 1_600));
        assert_eq!(known.label(false).as_deref(), Some("total 2.0k tok · ≈1.6k uncached"));
    }

    #[test]
    fn nothing_spent_says_nothing() {
        let mut spend = Spend::default();
        assert_eq!(spend.label(false), None);
        spend.add(&usage(0, 0, None, None, false));
        assert_eq!(spend.label(false), None);
    }

    #[test]
    fn counts_read_short() {
        assert_eq!(count(950, false), "950");
        assert_eq!(count(12_345, false), "12.3k");
        assert_eq!(count(1_400_000, true), "1,4 млн");
    }

    #[test]
    fn a_snapshot_replaces_the_row_and_keeps_names_it_was_not_told_again() {
        let started = Started {
            run_id: "r".into(),
            task: "t".into(),
            count: 1,
            base: "abc".into(),
            executors: vec![ExecutorInfo { id: "e1".into(), style: "senior-dev".into(), branch: "kvist/bestof/r/e1".into(), session: "s1".into() }],
        };
        let mut arena = Arena::new(started);
        let status = |state, step| Status {
            run_id: "r".into(),
            id: "e1".into(),
            style: String::new(),
            branch: String::new(),
            state,
            step,
            tests: Tests { state: TestState::Passed, summary: "12 passed".into() },
            diff: DiffStat { files: 2, added: 5, removed: 1 },
            usage: None,
        };
        arena.status(status(Some(ExecState::Running), 3));
        arena.status(status(Some(ExecState::Finished), 7));
        arena.status(status(None, 8));
        let row = &arena.executors[0];
        assert_eq!((row.style.as_str(), row.state, row.step), ("senior-dev", ExecState::Finished, 8));
        assert_eq!(row.branch, "kvist/bestof/r/e1");
    }

    #[test]
    fn the_latest_call_is_finished_by_its_own_result_only() {
        let mut arena = Arena::new(Started { run_id: "r".into(), task: String::new(), count: 0, base: String::new(), executors: Vec::new() });
        arena.call_started("e1", "c1".into(), ToolKind::Shell, "pytest".into());
        arena.call_finished("e1", "other", true, "x".into());
        assert_eq!(arena.executors[0].last.as_ref().map(|c| &c.state), Some(&CallState::Running));
        arena.call_finished("e1", "c1", true, "exit 0".into());
        assert!(matches!(&arena.executors[0].last, Some(ExecCall { state: CallState::Done { ok: true, .. }, .. })));
    }

    #[test]
    fn the_council_matches_the_chosen_card_by_executor_or_angle() {
        let mut council = Council::new("c".into(), "q".into(), vec![Member { id: "m1".into(), angle: "simplicity".into() }, Member { id: "m2".into(), angle: "security".into() }]);
        council.verdict = Some(CouncilVerdict { council_id: "c".into(), chosen: "m2".into(), decision: String::new(), reason: String::new(), dissent: Vec::new() });
        assert_eq!(council.cards.iter().map(|c| council.chosen(c)).collect::<Vec<_>>(), [false, true]);
        council.verdict.as_mut().unwrap().chosen = "Simplicity".into();
        assert_eq!(council.cards.iter().map(|c| council.chosen(c)).collect::<Vec<_>>(), [true, false]);
    }
}
