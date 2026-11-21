// A response's calls as one group: it stays in view for a moment after the model's next request begins, then folds
// into a single line that a click opens again; a call opens to show what the model sent and what came back.

use std::collections::HashMap;
use std::ops::Range;
use std::time::{Duration, Instant};

use ratatui::style::Style;

use crate::anim::Ease;
use crate::app::App;
use crate::model::{CallState, Item};
use crate::strings::strings;
use crate::text::{self, Line};
use crate::theme::{self, DUSK, MIST, MOON, ROSEHIP};
use crate::view;

/// How long a finished group stays open once something follows it.
const HOLD: Duration = Duration::from_millis(2000);
/// How long a group or a call takes to fold or open.
const FOLD: Duration = Duration::from_millis(320);
/// The most rows of a call's input and of its output shown before "… N more".
const INPUT_ROWS: usize = 8;
const OUTPUT_ROWS: usize = 14;

/// What a click on a row opens or folds: a group (by its first call) or one call.
#[derive(Clone, Debug, PartialEq, Eq, Hash)]
pub enum Fold {
    Group(String),
    Call(String),
}

/// A tween between two openness values; interrupted half-way, the next one starts from where this one stood.
#[derive(Clone, Copy, Debug)]
struct Flip {
    from: f32,
    to: f32,
    at: Instant,
}

impl Flip {
    fn value(&self, now: Instant) -> f32 {
        let t = (now.saturating_duration_since(self.at).as_secs_f32() / FOLD.as_secs_f32()).min(1.0);
        self.from + (self.to - self.from) * Ease::OutCubic.apply(t)
    }

    fn running(&self, now: Instant) -> bool {
        now.saturating_duration_since(self.at) < FOLD
    }
}

#[derive(Debug, Default)]
pub struct Folds {
    /// When each group (by its first call) was passed by something later, so its hold began.
    settled: HashMap<String, Instant>,
    /// What the person opened or folded by hand; it overrides the group's own folding.
    flips: HashMap<Fold, Flip>,
}

impl Folds {
    /// Starts the hold of every group that is finished and followed by something (or by the end of the turn).
    pub fn settle(&mut self, items: &[Item], busy: bool, now: Instant) {
        for run in runs(items) {
            let Some(Item::Tool { call, .. }) = items.get(run.start) else { continue };
            if self.settled.contains_key(call) {
                continue;
            }
            let done = items[run.clone()].iter().all(|item| matches!(item, Item::Tool { state: CallState::Done { .. }, .. }));
            if done && (run.end < items.len() || !busy) {
                self.settled.insert(call.clone(), now);
            }
        }
    }

    /// 1.0 when the row's details are fully shown, 0.0 when folded.
    pub fn openness(&self, fold: &Fold, now: Instant) -> f32 {
        if let Some(flip) = self.flips.get(fold) {
            return flip.value(now);
        }
        match fold {
            Fold::Call(_) => 0.0,
            Fold::Group(first) => match self.settled.get(first) {
                None => 1.0,
                Some(at) => {
                    let t = (now.saturating_duration_since(*at).as_secs_f32() - HOLD.as_secs_f32()) / FOLD.as_secs_f32();
                    1.0 - Ease::OutCubic.apply(t.clamp(0.0, 1.0))
                }
            },
        }
    }

    pub fn toggle(&mut self, fold: Fold, now: Instant) {
        let from = self.openness(&fold, now);
        let to = if from > 0.5 { 0.0 } else { 1.0 };
        self.flips.insert(fold, Flip { from, to, at: now });
    }

    /// True while a hold counts down or something folds, so the screen keeps redrawing.
    pub fn moving(&self, now: Instant) -> bool {
        self.settled.values().any(|at| now.saturating_duration_since(*at) < HOLD + FOLD) || self.flips.values().any(|flip| flip.running(now))
    }
}

/// The maximal runs of calls that came from one model response. A call whose step has not arrived has no
/// response named yet and joins the run before it.
pub fn runs(items: &[Item]) -> Vec<Range<usize>> {
    let mut out = Vec::new();
    let mut start: Option<usize> = None;
    let mut batch = "";
    for (index, item) in items.iter().enumerate() {
        let Item::Tool { batch: own, .. } = item else {
            if let Some(first) = start.take() {
                out.push(first..index);
            }
            continue;
        };
        let new_response = !own.is_empty() && !batch.is_empty() && own != batch;
        if new_response && let Some(first) = start.replace(index) {
            out.push(first..index);
        }
        if start.is_none() {
            start = Some(index);
        }
        if !own.is_empty() || new_response {
            batch = own;
        }
    }
    if let Some(first) = start {
        out.push(first..items.len());
    }
    out
}

/// A group's lines, the rows that open or fold something (line, what), and its code blocks (line, count, index).
pub struct Rendered {
    pub lines: Vec<Line>,
    pub folds: Vec<(usize, Fold)>,
    pub blocks: Vec<(usize, usize, usize)>,
}

pub fn render(app: &App, run: &[Item], width: usize, now: Instant, ms: u128) -> Rendered {
    let folds = &app.session.folds;
    let grouped = run.len() > 1;
    let Some(Item::Tool { call: first, .. }) = run.first() else {
        return Rendered { lines: Vec::new(), folds: Vec::new(), blocks: Vec::new() };
    };
    let indent = if grouped { 2 } else { 0 };
    let mut body: Vec<Line> = Vec::new();
    let mut marks: Vec<(usize, Fold)> = Vec::new();
    let mut blocks: Vec<(usize, usize, usize)> = Vec::new();
    for item in run {
        let Item::Tool { call, kind, label, state, input, output, .. } = item else { continue };
        marks.push((body.len(), Fold::Call(call.clone())));
        body.push(indented(view::tool_line(*kind, label, state, width.saturating_sub(indent), ms), indent));
        let open = folds.openness(&Fold::Call(call.clone()), now);
        if open > 0.0 {
            body.extend(details(input, output, state, width, indent, open));
        }
        view::push_blocks(app, |block| block.call == *call, width, now, &mut body, &mut blocks);
    }
    if !grouped {
        return Rendered { lines: body, folds: marks, blocks };
    }
    let open = folds.openness(&Fold::Group(first.clone()), now);
    let keep = (body.len() as f32 * open).ceil() as usize;
    body.truncate(keep);
    marks.retain(|(line, _)| *line < keep);
    blocks.retain(|(start, ..)| *start < keep);
    for (start, count, _) in blocks.iter_mut() {
        *count = (*count).min(keep - *start);
    }
    let mut lines = vec![header(run, open)];
    lines.extend(body);
    let marks = std::iter::once((0, Fold::Group(first.clone()))).chain(marks.into_iter().map(|(line, fold)| (line + 1, fold))).collect();
    let blocks = blocks.into_iter().map(|(start, count, index)| (start + 1, count, index)).collect();
    Rendered { lines, folds: marks, blocks }
}

fn indented(mut line: Line, by: usize) -> Line {
    if by > 0 {
        line.insert(0, (" ".repeat(by), Style::default()));
    }
    line
}

/// "▾ 4 calls · 1 failed": the arrow turns as the group opens.
fn header(run: &[Item], open: f32) -> Line {
    let s = strings();
    let failed = run.iter().filter(|item| matches!(item, Item::Tool { state: CallState::Done { ok: false, .. }, .. })).count();
    let mut text = if s.ru { format!("Вызовов: {}", run.len()) } else { format!("{} calls", run.len()) };
    if failed > 0 {
        text.push_str(&if s.ru { format!(" · ошибок: {failed}") } else { format!(" · {failed} failed") });
    }
    let arrow = if open > 0.5 { "▾" } else { "▸" };
    let color = if failed > 0 && open <= 0.0 { ROSEHIP } else { DUSK };
    vec![(format!("{arrow} {text}"), Style::default().fg(color))]
}

/// What the model sent and what came back, in a quiet frame under the call; `open` reveals it from the top.
fn details(input: &str, output: &str, state: &CallState, width: usize, indent: usize, open: f32) -> Vec<Line> {
    let s = strings();
    let inner = width.saturating_sub(indent + 4).max(8);
    let failed = matches!(state, CallState::Done { ok: false, .. });
    let mut rows: Vec<Line> = Vec::new();
    let mut section = |title: &str, body: &str, color, cap: usize| {
        if body.trim().is_empty() {
            return;
        }
        rows.push(framed(vec![(title.to_string(), Style::default().fg(DUSK))], indent));
        let wrapped: Vec<Line> = body.trim_end().split('\n').flat_map(|row| text::wrap(&vec![(row.to_string(), Style::default().fg(color))], inner)).collect();
        let hidden = wrapped.len().saturating_sub(cap);
        rows.extend(wrapped.into_iter().take(cap).map(|row| framed(row, indent)));
        if hidden > 0 {
            rows.push(framed(vec![(format!("… {} {hidden}", s.call_more), Style::default().fg(DUSK))], indent));
        }
    };
    section(s.call_input, input, MOON, INPUT_ROWS);
    section(s.call_output, output, if failed { ROSEHIP } else { MIST }, OUTPUT_ROWS);
    let shown = (rows.len() as f32 * open).ceil() as usize;
    rows.truncate(shown);
    rows
}

fn framed(line: Line, indent: usize) -> Line {
    let mut row: Line = vec![(format!("{}  ▏ ", " ".repeat(indent)), Style::default().fg(theme::INK_SOFT))];
    row.extend(line);
    row
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::model::ToolKind;

    fn tool(call: &str, batch: &str, done: bool) -> Item {
        let state = if done { CallState::Done { ok: true, summary: String::new(), diff: None } } else { CallState::Running };
        Item::Tool { call: call.into(), kind: ToolKind::Shell, label: call.into(), state, batch: batch.into(), input: String::new(), output: String::new() }
    }

    #[test]
    fn calls_of_one_response_form_a_run_and_a_new_response_starts_another() {
        let items = [tool("a", "b1", true), tool("b", "b1", true), tool("c", "b2", true)];
        assert_eq!(runs(&items), vec![0..2, 2..3]);
    }

    #[test]
    fn a_call_without_its_step_yet_joins_the_run_before_it() {
        let items = [tool("a", "b1", true), tool("b", "", false)];
        assert_eq!(runs(&items), vec![0..2]);
    }

    #[test]
    fn a_group_holds_two_seconds_after_something_follows_then_folds() {
        let t0 = Instant::now();
        let mut folds = Folds::default();
        let mut items = vec![tool("a", "b1", true), tool("b", "b1", true)];
        folds.settle(&items, true, t0);
        assert_eq!(folds.openness(&Fold::Group("a".into()), t0 + Duration::from_secs(9)), 1.0, "the last group of a running turn stays open");
        items.push(Item::Error("next".into()));
        folds.settle(&items, true, t0);
        assert_eq!(folds.openness(&Fold::Group("a".into()), t0 + Duration::from_millis(1900)), 1.0);
        assert!(folds.openness(&Fold::Group("a".into()), t0 + Duration::from_millis(2150)) < 1.0);
        assert_eq!(folds.openness(&Fold::Group("a".into()), t0 + Duration::from_millis(2400)), 0.0);
    }

    #[test]
    fn a_group_with_a_running_call_never_folds() {
        let t0 = Instant::now();
        let mut folds = Folds::default();
        let items = [tool("a", "b1", true), tool("b", "b1", false), Item::Error("x".into())];
        folds.settle(&items, true, t0);
        assert_eq!(folds.openness(&Fold::Group("a".into()), t0 + Duration::from_secs(9)), 1.0);
    }

    #[test]
    fn a_click_opens_a_folded_group_and_folds_an_open_one() {
        let t0 = Instant::now();
        let mut folds = Folds::default();
        let group = Fold::Group("a".into());
        folds.toggle(group.clone(), t0);
        assert_eq!(folds.openness(&group, t0 + Duration::from_secs(1)), 0.0);
        folds.toggle(group.clone(), t0 + Duration::from_secs(1));
        assert_eq!(folds.openness(&group, t0 + Duration::from_secs(2)), 1.0);
    }
}
