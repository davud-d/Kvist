// The best-of arena, an executor's log, the council and the decision dialog,
// as lines of text. The arena, the log and the council take the place of the
// conversation; view.rs puts their lines on the glass and records the rows
// that can be clicked.

use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};

use crate::arena::{self, Arena, ArenaDiff, Card, Council, DiffStat, ExecState, Executor, Finish, Finished, Outcome, TestState, Tests, Usage};
use crate::decision::{Action, Decision, Page};
use crate::diff;
use crate::execlog::{ExecLog, LogState};
use crate::markdown;
use crate::model::Item;
use crate::strings::Strings;
use crate::text::{self, Line, Painter};
use crate::theme::{self, DUSK, FOG, HOVER, INK, INK_SOFT, MIST, MOON, MOSS, ROSEHIP};
use crate::view::{self, Hit};

/// A screen's lines, and where the rows that can be chosen are in them:
/// (first line, how many lines, the executor's index).
pub struct Sheet {
    pub lines: Vec<Line>,
    pub rows: Vec<(usize, usize, usize)>,
}

fn st(color: Color) -> Style {
    Style::default().fg(color)
}

fn span(text: impl Into<String>, color: Color) -> (String, Style) {
    (text.into(), st(color))
}

fn bold(text: impl Into<String>, color: Color) -> (String, Style) {
    (text.into(), st(color).add_modifier(Modifier::BOLD))
}

/// `s` cut or padded with spaces to exactly `w` columns.
fn cell(s: &str, w: usize) -> String {
    let cut = text::fit(s, w);
    let pad = w.saturating_sub(text::width(&cut));
    format!("{cut}{}", " ".repeat(pad))
}

/// `s` cut, then padded on the left to `w` columns.
fn right(s: &str, w: usize) -> String {
    let cut = text::fit(s, w);
    let pad = w.saturating_sub(text::width(&cut));
    format!("{}{cut}", " ".repeat(pad))
}

fn pad(mut line: Line, w: usize) -> Line {
    let missing = w.saturating_sub(text::line_width(&line));
    line.push((" ".repeat(missing), Style::default()));
    line
}

fn gap(n: usize) -> (String, Style) {
    (" ".repeat(n), Style::default())
}

fn wrapped(text: &str, color: Color, width: usize, most: usize) -> Vec<Line> {
    text::wrap(&vec![span(text, color)], width).into_iter().filter(|line| !line.is_empty()).take(most).collect()
}

/// The icon, its colour and the word for an executor's state.
fn state_look(state: ExecState, s: &Strings, ms: u128) -> (&'static str, Color, &'static str) {
    match state {
        ExecState::Waiting => ("○", DUSK, s.state_waiting),
        ExecState::Running => (theme::spinner(ms), MOON, s.state_running),
        ExecState::Finished => (theme::I_CHECK, MOSS, s.state_finished),
        ExecState::Failed => (theme::I_ERROR, ROSEHIP, s.state_failed),
        ExecState::Stopped => (theme::I_ERROR, DUSK, s.state_stopped),
        ExecState::Eliminated => (theme::I_CLOSE, DUSK, s.state_eliminated),
        ExecState::Lost => (theme::I_ERROR, DUSK, s.state_lost),
    }
}

/// The icon, its colour and the words of a test run: the core's summary when it sent one.
fn tests_look(tests: &Tests, s: &Strings, ms: u128) -> (&'static str, Color, String) {
    let (icon, color, word) = match tests.state {
        TestState::Passed => (theme::I_CHECK, MOSS, s.tests_passed),
        TestState::Failed => (theme::I_ERROR, ROSEHIP, s.tests_failed),
        TestState::Running => (theme::spinner(ms), MOON, s.tests_running),
        TestState::Unknown => ("·", DUSK, s.tests_unknown),
    };
    let words = if tests.summary.trim().is_empty() { word.to_string() } else { tests.summary.replace('\n', " ") };
    (icon, color, words)
}

/// "+12 −3 · 2 files" (without the files when `room` is short), or a dash when nothing changed.
fn diff_look(diff: DiffStat, room: usize, s: &Strings) -> Line {
    if diff == DiffStat::default() {
        return vec![span("—", DUSK)];
    }
    let mut line = view::counts(diff.added, diff.removed);
    let word = if diff.files == 1 && !s.ru { "file" } else { s.files_word };
    let files = format!(" · {} {word}", diff.files);
    if diff.files > 0 && text::line_width(&line) + text::width(&files) <= room {
        line.push(span(files, DUSK));
    }
    line
}

/// An executor's money, short: "≈$0.12", or its tokens when it has no price.
fn spend_look(usage: Option<&Usage>, s: &Strings) -> String {
    let Some(usage) = usage else { return String::new() };
    let mut spend = arena::Spend::default();
    spend.add(usage);
    match usage.cost {
        Some(_) => spend.label(s.ru).unwrap_or_default(),
        None if spend.tokens > 0 => format!("{} {}", arena::count(spend.tokens, s.ru), if s.ru { "ток" } else { "tok" }),
        None => String::new(),
    }
}

/// Widths of the arena's columns for a screen `width` columns wide.
struct Cols {
    style: usize,
    state: usize,
    /// 0 drops the column: the window is narrow.
    step: usize,
    tests: usize,
    diff: usize,
    spend: usize,
}

/// The marker, the state's icon and a space ahead of the style.
const LEAD: usize = 4;
const GAP: usize = 2;

impl Cols {
    fn for_width(width: usize) -> Cols {
        let with = |step: usize, style: usize, state: usize, diff: usize, spend: usize| {
            let columns = if step > 0 { 6 } else { 5 };
            let fixed = LEAD + style + state + step + diff + spend + GAP * (columns - 1);
            Cols { style, state, step, tests: width.saturating_sub(fixed).max(6), diff, spend }
        };
        let full = with(8, 14, 10, 17, 9);
        if full.tests >= 12 {
            return full;
        }
        let no_step = with(0, 14, 10, 17, 9);
        if no_step.tests >= 8 { no_step } else { with(0, 10, 8, 12, 7) }
    }
}

fn arena_header(arena: &Arena, width: usize, s: &Strings, lines: &mut Vec<Line>) {
    let mut title = vec![bold(s.bestof_title, MOON)];
    if arena.count > 0 {
        title.push(span(format!(" · {}", arena.count), MIST));
    }
    if !arena.base.is_empty() {
        title.push(span(format!("   {} {}", s.base_word, arena.base), DUSK));
    }
    lines.push(title);
    lines.extend(wrapped(s.bestof_blurb, MIST, width, 2));
    if !arena.task.trim().is_empty() {
        let task = arena.task.split_whitespace().collect::<Vec<_>>().join(" ");
        lines.push(vec![span(format!("{}: ", s.task_word), DUSK), span(text::fit(&task, width.saturating_sub(text::width(s.task_word) + 2)), FOG)]);
    }
    lines.push(Vec::new());
}

fn column_titles(cols: &Cols, s: &Strings) -> Line {
    let mut line = vec![gap(LEAD), span(cell(s.col_style, cols.style), DUSK), gap(GAP), gap(cols.state), gap(GAP)];
    if cols.step > 0 {
        line.extend([gap(cols.step), gap(GAP)]);
    }
    line.extend([span(cell(s.col_tests, cols.tests), DUSK), gap(GAP), span(cell(s.col_diff, cols.diff), DUSK)]);
    line
}

/// One executor: its numbers on the first line, then the branch with its
/// latest call; an executor that is out gives its reason instead of the call.
fn executor_lines(e: &Executor, cols: &Cols, width: usize, selected: bool, s: &Strings, ms: u128) -> Vec<Line> {
    let muted = e.state.out();
    let (icon, icon_color, word) = state_look(e.state, s, ms);
    let dim = |color: Color| if muted { DUSK } else { color };
    let mut first: Line = vec![
        span(if selected { "▎ " } else { "  " }, MOON),
        span(icon, icon_color),
        span(" ", FOG),
        bold(cell(&e.style, cols.style), dim(FOG)),
        gap(GAP),
        span(cell(word, cols.state), dim(icon_color)),
        gap(GAP),
    ];
    if cols.step > 0 {
        let step = if e.step > 0 { format!("{} {}", s.step_word, e.step) } else { String::new() };
        first.extend([span(cell(&step, cols.step), DUSK), gap(GAP)]);
    }
    let (tests_icon, tests_color, tests_words) = tests_look(&e.tests, s, ms);
    first.extend([span(tests_icon, dim(tests_color)), span(" ", FOG), span(cell(&tests_words, cols.tests.saturating_sub(2)), dim(MIST)), gap(GAP)]);
    let mut changes = diff_look(e.diff, cols.diff, s);
    if muted {
        changes = vec![span(changes.iter().map(|(t, _)| t.as_str()).collect::<String>(), DUSK)];
    }
    first.extend(pad(changes, cols.diff));
    let money = spend_look(e.usage.as_ref(), s);
    if !money.is_empty() {
        first.extend([gap(GAP), span(right(&money, cols.spend), dim(MIST))]);
    }

    let mut second: Line = vec![gap(LEAD), span(text::fit(&e.branch, 34), DUSK)];
    let mut lines = vec![first];
    let room = width.saturating_sub(LEAD + 34 + GAP + 4);
    if muted && !e.reason.trim().is_empty() {
        lines.push(second);
        for reason in wrapped(&e.reason, ROSEHIP, width.saturating_sub(LEAD), 2) {
            lines.push([vec![gap(LEAD)], reason].concat());
        }
        return lines;
    }
    if let Some(call) = &e.last {
        second.push(gap(GAP));
        second.extend(view::tool_line(call.kind, &call.label, &call.state, room, ms));
    }
    lines.push(second);
    lines
}

fn finished_lines(finished: &Finished, width: usize, s: &Strings) -> Vec<Line> {
    let (word, color) = match finished.outcome {
        Some(Finish::Accepted) => (s.finish_accepted, MOSS),
        Some(Finish::Discarded) => (s.finish_discarded, MIST),
        Some(Finish::Cancelled) => (s.finish_cancelled, MIST),
        Some(Finish::Failed) => (s.finish_failed, ROSEHIP),
        Some(Finish::Pending) => (s.finish_pending, MOON),
        None => ("", MIST),
    };
    let mut words = word.to_string();
    for part in [finished.message.as_str(), finished.commit.as_str()].into_iter().filter(|p| !p.trim().is_empty()) {
        words.push_str(if words.is_empty() { "" } else { " · " });
        words.push_str(part);
    }
    wrapped(&words, color, width, 3)
}

pub fn arena_sheet(arena: &Arena, selected: usize, running: bool, width: usize, s: &Strings, ms: u128) -> Sheet {
    let mut lines = Vec::new();
    let mut rows = Vec::new();
    arena_header(arena, width, s, &mut lines);
    let cols = Cols::for_width(width);
    lines.push(column_titles(&cols, s));
    for (index, executor) in arena.executors.iter().enumerate() {
        let shown = executor_lines(executor, &cols, width, index == selected, s, ms);
        rows.push((lines.len(), shown.len(), index));
        lines.extend(shown);
    }
    lines.push(Vec::new());
    if let Some(finished) = &arena.finished {
        lines.extend(finished_lines(finished, width, s));
    }
    let mut keys = vec![span("↑↓", MIST), span(format!(" {}", s.key_choose), DUSK), span("    enter", MIST), span(format!(" {}", s.key_log), DUSK)];
    keys.extend([span("    esc", MIST), span(if running { " ×2 stop".to_string() } else { format!(" {}", s.key_close) }, DUSK)]);
    lines.push(keys);
    Sheet { lines, rows }
}

/// The lines of an executor's journal, as the conversation shows its own.
fn item_lines(items: &[Item], width: usize, ms: u128) -> Vec<Line> {
    let mut lines: Vec<Line> = Vec::new();
    let mut after_tool = false;
    for item in items {
        let is_tool = matches!(item, Item::Tool { .. });
        if !lines.is_empty() && (!is_tool || !after_tool) {
            lines.push(Vec::new());
        }
        after_tool = is_tool;
        match item {
            Item::User(words) => {
                for raw in words.split('\n') {
                    for line in text::wrap(&vec![span(raw, FOG)], width.saturating_sub(2)) {
                        lines.push([vec![span("▎ ", MOON)], line].concat());
                    }
                }
            }
            Item::Agent { text: words, .. } => lines.extend(markdown::render_with_math(words, width, None).0),
            Item::Tool { kind, label, state, .. } => lines.push(view::tool_line(*kind, label, state, width, ms)),
            Item::Error(message) => {
                for line in text::wrap(&vec![span(message.as_str(), ROSEHIP)], width.saturating_sub(2)) {
                    lines.push([vec![span(format!("{} ", theme::I_ERROR), ROSEHIP)], line].concat());
                }
            }
        }
    }
    lines
}

pub fn log_sheet(log: &ExecLog, width: usize, s: &Strings, ms: u128) -> Sheet {
    let mut lines = vec![vec![bold(log.style.as_str(), MOON), span(format!("   {}", log.id), DUSK), span(format!("   {}", s.log_readonly), DUSK)], Vec::new()];
    let mut older = false;
    match &log.state {
        LogState::Loading => lines.push(vec![span(theme::spinner(ms), MOON), span(format!(" {}", s.log_loading), MIST)]),
        LogState::Failed(why) => lines.extend(wrapped(if why.is_empty() { s.log_empty } else { why }, ROSEHIP, width, 4)),
        LogState::Ready { older: more } => {
            older = *more;
            let body = item_lines(&log.session.items, width, ms);
            if body.is_empty() {
                lines.push(vec![span(s.log_empty, DUSK)]);
            }
            lines.extend(body);
        }
    }
    lines.push(Vec::new());
    let mut keys = vec![span("esc", MIST), span(format!(" {}", s.key_back), DUSK), span("    ↑↓", MIST), span(format!(" {}", s.key_scroll), DUSK)];
    keys.extend([span("    ctrl+r", MIST), span(format!(" {}", s.key_refresh), DUSK)]);
    if older {
        keys.push(span(format!("    {}", s.earlier_not_shown), DUSK));
    }
    lines.push(keys);
    Sheet { lines, rows: Vec::new() }
}

/// An angle's name and its one-line explanation; an angle the core invents is shown as it came.
fn angle_look(angle: &str, s: &Strings) -> (String, &'static str) {
    match angle.to_lowercase().as_str() {
        "simplicity" => (s.angle_simplicity.to_string(), s.angle_simplicity_blurb),
        "security" => (s.angle_security.to_string(), s.angle_security_blurb),
        "speed" => (s.angle_speed.to_string(), s.angle_speed_blurb),
        "maintenance" => (s.angle_maintenance.to_string(), s.angle_maintenance_blurb),
        _ => (angle.to_string(), ""),
    }
}

/// One member's card, `w` columns wide, every line padded to that width.
fn card_lines(card: &Card, council: &Council, w: usize, s: &Strings, ms: u128) -> Vec<Line> {
    let chosen = council.chosen(card);
    let muted = council.verdict.is_some() && !chosen;
    let (name, blurb) = angle_look(&card.angle, s);
    let bar = span("▎ ", if chosen { MOON } else { DUSK });
    let inner = w.saturating_sub(2);
    let mut body: Vec<Line> = Vec::new();
    let mut head = vec![bold(name, if chosen { MOON } else if muted { DUSK } else { FOG })];
    if chosen {
        head.push(span(format!(" · {}", s.council_chosen), MOON));
    }
    body.push(head);
    if !blurb.is_empty() {
        body.push(vec![span(text::fit(blurb, inner), DUSK)]);
    }
    match &card.decision {
        None => body.push(vec![span(format!("{} {}", theme::spinner(ms), s.council_thinking), DUSK)]),
        Some(decision) => {
            body.extend(wrapped(decision, if muted { MIST } else { FOG }, inner, 3));
            body.extend(wrapped(&card.argument, if muted { DUSK } else { MIST }, inner, 3));
        }
    }
    for risk in card.risks.iter().take(3) {
        let target = council.cards.iter().find(|c| c.id == risk.target).map_or(risk.target.clone(), |c| angle_look(&c.angle, s).0);
        let line = format!("→ {target}: {}", risk.risk.replace('\n', " "));
        body.push(vec![span(text::fit(&line, inner), if muted { DUSK } else { MIST })]);
    }
    body.into_iter().map(|line| pad([vec![bar.clone()], line].concat(), w)).collect()
}

pub fn council_sheet(council: &Council, width: usize, s: &Strings, ms: u128) -> Sheet {
    let mut lines = vec![vec![bold(s.council_title, MOON)]];
    lines.extend(wrapped(s.council_blurb, MIST, width, 2));
    if !council.question.trim().is_empty() {
        lines.extend(wrapped(&council.question, FOG, width, 3));
    }
    lines.push(Vec::new());
    let columns = if width >= 84 { 2 } else { 1 };
    let gutter = 3;
    let card_w = (width.saturating_sub(gutter * (columns - 1))) / columns;
    for group in council.cards.chunks(columns) {
        let cards: Vec<Vec<Line>> = group.iter().map(|card| card_lines(card, council, card_w, s, ms)).collect();
        let tallest = cards.iter().map(Vec::len).max().unwrap_or(0);
        for row in 0..tallest {
            let mut line: Line = Vec::new();
            for (i, card) in cards.iter().enumerate() {
                if i > 0 {
                    line.push(gap(gutter));
                }
                line.extend(card.get(row).cloned().unwrap_or_else(|| vec![gap(card_w)]));
            }
            lines.push(line);
        }
        lines.push(Vec::new());
    }
    if let Some(verdict) = &council.verdict {
        let chosen = council.cards.iter().find(|c| council.chosen(c)).map(|c| angle_look(&c.angle, s).0).unwrap_or_else(|| verdict.chosen.clone());
        lines.push(vec![bold(format!("{chosen}: "), MOON), span(verdict.decision.replace('\n', " "), FOG)]);
        if !verdict.reason.trim().is_empty() {
            lines.extend(wrapped(&format!("{}: {}", s.council_reason, verdict.reason), MIST, width, 4));
        }
        if !verdict.dissent.is_empty() {
            lines.push(vec![span(format!("{}:", s.council_dissent), DUSK)]);
            for dissent in &verdict.dissent {
                let who = angle_look(&dissent.who, s).0;
                for (i, line) in wrapped(&format!("{who}: {}", dissent.why), MIST, width.saturating_sub(2), 3).into_iter().enumerate() {
                    lines.push([vec![gap(if i == 0 { 2 } else { 4 })], line].concat());
                }
            }
        }
        lines.push(Vec::new());
    }
    lines.push(vec![span("esc", MIST), span(format!(" {}", s.key_close), DUSK), span("    PgUp/PgDn", MIST), span(format!(" {}", s.key_scroll), DUSK)]);
    Sheet { lines, rows: Vec::new() }
}

/// The patch of an executor, a file at a time: the core sends one unified
/// diff that names its files with `diff --git` lines.
pub fn patch_lines(diff: &ArenaDiff, width: usize, cut: &str) -> Vec<Line> {
    let mut lines = Vec::new();
    for (path, body) in split_files(&diff.patch, &diff.files) {
        if !lines.is_empty() {
            lines.push(Vec::new());
        }
        let (icon, color) = theme::file_icon(&path);
        lines.push(vec![span(icon, color), span(format!(" {path}"), MOON)]);
        lines.extend(diff::inline(&diff::Patch::parse(&body, diff::language(&path)), width, cut));
    }
    lines
}

fn split_files(patch: &str, files: &[String]) -> Vec<(String, String)> {
    let mut sections: Vec<(String, String)> = Vec::new();
    for line in patch.lines() {
        if let Some(rest) = line.strip_prefix("diff --git ") {
            sections.push((rest.rsplit_once(" b/").map_or(rest, |(_, path)| path).to_string(), String::new()));
            continue;
        }
        if sections.is_empty() {
            sections.push((files.first().cloned().unwrap_or_default(), String::new()));
        }
        if let Some((_, body)) = sections.last_mut() {
            // The core cuts a long patch with "[... N characters omitted ...]"; diff.rs knows a cut by this spelling.
            let cut = line.starts_with("[...") && line.contains("omitted");
            body.push_str(if cut { "\\ patch truncated" } else { line });
            body.push('\n');
        }
    }
    sections
}

/// Columns of the decision dialog.
pub const DECISION_WIDTH: u16 = 110;
/// Most rows of the verdict table shown.
const MAX_TABLE_ROWS: usize = 8;
/// A dialog that can show a patch keeps this height whichever page is open.
const PATCH_HEIGHT: u16 = 30;

fn decision_inner(card_width: u16) -> usize {
    card_width.saturating_sub(6) as usize
}

/// Rows the dialog needs: padding, title, the table, the winner and why,
/// the buttons and the keys.
pub fn decision_height(decision: &Decision, card_width: u16) -> u16 {
    let reason = wrapped(&decision.verdict.reason, MIST, decision_inner(card_width), 3).len() as u16;
    let rows = decision.verdict.rows.len().clamp(1, MAX_TABLE_ROWS) as u16;
    // padding, title, gap, column titles, rows, gap, winner, why, gap, buttons, gap, keys, padding
    let table = 1 + 1 + 1 + 1 + rows + 1 + 1 + reason + 1 + 1 + 1 + 1 + 1;
    if decision.patch.is_some() { table.max(PATCH_HEIGHT) } else { table }
}

/// Rows of patch the dialog in `card` shows.
pub fn patch_rows(card: Rect) -> usize {
    // padding, title, gap, file line, gap, buttons, gap, keys, padding
    card.height.saturating_sub(9) as usize
}

pub fn patch_max_scroll(decision: &Decision, card: Rect, s: &Strings) -> usize {
    let Some(patch) = &decision.patch else { return 0 };
    patch_lines(patch, decision_inner(card.width), s.diff_cut).len().saturating_sub(patch_rows(card))
}

fn outcome_look(outcome: Option<Outcome>, s: &Strings) -> (&'static str, Style) {
    match outcome {
        Some(Outcome::Winner) => (s.outcome_winner, st(MOON).add_modifier(Modifier::BOLD)),
        Some(Outcome::Finalist) => (s.outcome_finalist, st(FOG)),
        Some(Outcome::Eliminated) => (s.outcome_eliminated, st(DUSK)),
        Some(Outcome::Failed) => (s.outcome_failed, st(ROSEHIP)),
        None => ("—", st(DUSK)),
    }
}

fn action_label(action: Action, s: &Strings) -> String {
    let (label, key) = match action {
        Action::Accept => (s.action_accept, 'a'),
        Action::Diff => (s.action_diff, 'd'),
        Action::Other => (s.action_other, 'p'),
        Action::Discard => (s.action_discard, 'x'),
    };
    format!("  {label} ({key})  ")
}

/// The decision dialog: the verdict table, who won and why, and the four
/// actions; or, once the person asked, the executor's patch in the table's place.
#[allow(clippy::too_many_arguments)]
pub fn draw_decision(p: &mut Painter, card: Rect, decision: &Decision, hover: Option<Hit>, open: f32, s: &Strings, ms: u128, hits: &mut Vec<(Rect, Hit)>) {
    if open < 0.05 {
        return;
    }
    let dy = ((1.0 - open) * 1.4).round() as i32;
    p.clear(Rect::new(card.x, (card.y as i32 + dy).max(0) as u16, card.width, card.height));
    let x = card.x as i32 + 3;
    let right_edge = card.right() as i32 - 3;
    let top = card.y as i32 + 1 + dy;
    let width = (right_edge - x).max(8) as usize;
    p.text(x, top, s.decision_title, Style::default().fg(FOG).add_modifier(Modifier::BOLD));
    p.text(right_edge - 3, top, "esc", st(DUSK));
    if decision.waiting {
        p.line(x, top + 2, &vec![span(theme::spinner(ms), MOON), span(format!(" {}", s.waiting), MIST)]);
        return;
    }
    match (decision.page, &decision.patch) {
        (Page::Patch, Some(patch)) => draw_patch(p, card, decision, patch, (x, top + 2), width, s),
        _ => draw_table(p, card, decision, hover, x, top + 2, width, s, ms, hits),
    }
    let buttons_y = card.bottom() as i32 - 4 + dy;
    let mut bx = x;
    for (index, action) in decision.actions.iter().enumerate() {
        let picked = index == decision.action;
        let hovered = hover == Some(Hit::Action(index));
        let bg = if picked { MOON } else if hovered { HOVER } else { INK_SOFT };
        let end = p.text(bx, buttons_y, &action_label(*action, s), Style::default().fg(if picked { INK } else { FOG }).bg(bg).add_modifier(Modifier::BOLD));
        hits.push((Rect::new(bx.max(0) as u16, buttons_y.max(0) as u16, (end - bx) as u16, 1), Hit::Action(index)));
        bx = end + 2;
    }
    let mut keys = vec![span("↑↓", MIST), span(format!(" {}", if decision.page == Page::Patch { s.key_scroll } else { s.key_row }), DUSK)];
    keys.extend([span("    ←→", MIST), span(format!(" {}", s.key_action), DUSK), span("    enter", MIST), span(format!(" {}", s.key_do), DUSK)]);
    if decision.patch.is_some() {
        keys.extend([span("    tab", MIST), span(format!(" {}", if decision.page == Page::Patch { s.key_table } else { s.key_patch }), DUSK)]);
    }
    keys.push(span("    esc", MIST));
    p.line(x, card.bottom() as i32 - 2 + dy, &keys);
}

/// The table, the winner and why.
#[allow(clippy::too_many_arguments)]
fn draw_table(p: &mut Painter, card: Rect, decision: &Decision, hover: Option<Hit>, x: i32, top: i32, width: usize, s: &Strings, ms: u128, hits: &mut Vec<(Rect, Hit)>) {
    let (style_w, result_w, diff_w) = (14, 11, 17);
    let rest = width.saturating_sub(2 + style_w + result_w + diff_w + 3 * GAP);
    let note_w = if rest >= 30 { rest * 2 / 5 } else { 0 };
    let tests_w = rest.saturating_sub(note_w + if note_w > 0 { GAP } else { 0 }).clamp(6, 40);
    let mut titles = vec![gap(2), span(cell(s.col_style, style_w), DUSK), gap(GAP), span(cell(s.col_result, result_w), DUSK), gap(GAP), span(cell(s.col_tests, tests_w), DUSK), gap(GAP), span(cell(s.col_diff, diff_w), DUSK)];
    if note_w > 0 {
        titles.extend([gap(GAP), span(s.col_note, DUSK)]);
    }
    p.line(x, top, &titles);
    let mut y = top + 1;
    for (index, row) in decision.verdict.rows.iter().enumerate().take(MAX_TABLE_ROWS) {        let picked = index == decision.row;
        if picked || hover == Some(Hit::Verdict(index)) {
            p.fill(card.x as i32 + 1, y, card.width as i32 - 2, HOVER);
        }
        let (word, style) = outcome_look(row.outcome, s);
        let muted = matches!(row.outcome, Some(Outcome::Eliminated | Outcome::Failed));
        let (tests_icon, tests_color, tests_words) = tests_look(&row.tests, s, ms);
        let mut line = vec![
            span(if picked { "▎ " } else { "  " }, MOON),
            bold(cell(&row.style, style_w), if muted { DUSK } else { FOG }),
            gap(GAP),
            (cell(word, result_w), style),
            gap(GAP),
            span(tests_icon, tests_color),
            span(" ", FOG),
            span(cell(&tests_words, tests_w.saturating_sub(2)), MIST),
            gap(GAP),
        ];
        line.extend(pad(diff_look(row.diff, diff_w, s), diff_w));
        if note_w > 0 {
            line.extend([gap(GAP), span(text::fit(&row.note.replace('\n', " "), note_w), DUSK)]);
        }
        p.line(x, y, &line);
        hits.push((Rect::new(card.x + 1, y.max(0) as u16, card.width - 2, 1), Hit::Verdict(index)));
        y += 1;
    }
    y += 1;
    let winner_row = decision.verdict.rows.iter().find(|r| !decision.verdict.winner.is_empty() && r.id == decision.verdict.winner);
    let mut winner_line = vec![span(format!("{}: ", s.decision_winner), DUSK)];
    match winner_row {
        None => winner_line.push(bold(s.decision_no_winner, ROSEHIP)),
        Some(row) => {
            winner_line.push(bold(&row.style, MOON));
            if row.tests.state == TestState::Failed {
                winner_line.push(span(format!(" · {}", s.decision_winner_failing), ROSEHIP));
            }
        }
    }
    p.line(x, y, &winner_line);
    y += 1;
    for line in wrapped(&decision.verdict.reason, MIST, width, 3) {
        p.line(x, y, &line);
        y += 1;
    }
}

fn draw_patch(p: &mut Painter, card: Rect, decision: &Decision, patch: &ArenaDiff, (x, top): (i32, i32), width: usize, s: &Strings) {
    let who = decision.verdict.rows.iter().find(|r| r.id == patch.id).map_or(patch.id.as_str(), |r| r.style.as_str());
    p.line(x, top, &vec![span(format!("{}: ", s.decision_patch_of), DUSK), bold(who, MOON)]);
    let lines = patch_lines(patch, width, s.diff_cut);
    if lines.is_empty() {
        p.text(x, top + 2, s.nothing_found, st(DUSK));
        return;
    }
    let rows = patch_rows(card);
    let scroll = decision.scroll.min(lines.len().saturating_sub(rows));
    for (i, line) in lines.iter().skip(scroll).take(rows).enumerate() {
        p.line(x, top + 2 + i as i32, line);
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::arena::{CouncilVerdict, Dissent, ExecutorInfo, Member, Started, Status};
    use crate::strings::{EN, RU};

    fn plain(line: &Line) -> String {
        line.iter().map(|(s, _)| s.as_str()).collect()
    }

    fn all(sheet: &Sheet) -> String {
        sheet.lines.iter().map(plain).collect::<Vec<_>>().join("\n")
    }

    fn arena() -> Arena {
        let mut arena = Arena::new(Started {
            run_id: "r1".into(),
            task: "fix the parser".into(),
            count: 2,
            base: "abc123".into(),
            executors: ["e1", "e2"]
                .iter()
                .map(|id| ExecutorInfo { id: id.to_string(), style: format!("style-{id}"), branch: format!("kvist/bestof/r1/{id}"), session: format!("s-{id}") })
                .collect(),
        });
        let status = |id: &str, state, usage: Option<Usage>| Status {
            run_id: "r1".into(),
            id: id.into(),
            style: String::new(),
            branch: String::new(),
            state: Some(state),
            step: 7,
            tests: Tests { state: TestState::Passed, summary: "12 passed".into() },
            diff: DiffStat { files: 2, added: 30, removed: 4 },
            usage,
        };
        arena.status(status("e1", ExecState::Running, Some(Usage { input: 1000, output: 100, cache_read: None, cost: Some(0.25), approx: true })));
        arena.status(status("e2", ExecState::Running, Some(Usage { input: 12_000, output: 300, cache_read: None, cost: None, approx: false })));
        arena.call_started("e1", "c1".into(), crate::model::ToolKind::Shell, "runs pytest".into());
        arena.eliminate("e2", "its tests keep failing".into());
        arena
    }

    #[test]
    fn an_arena_row_shows_style_state_step_tests_changes_spend_and_the_latest_call() {
        let sheet = arena_sheet(&arena(), 0, true, 110, &EN, 0);
        let text = all(&sheet);
        let first = plain(&sheet.lines[sheet.rows[0].0]);
        for part in ["style-e1", "working", "step 7", "12 passed", "+30 −4", "2 files", "≈$0.25"] {
            assert!(first.contains(part), "{part} in {first}");
        }
        assert!(text.contains("kvist/bestof/r1/e1") && text.contains("runs pytest"));
        assert!(text.contains("best-of") && text.contains("fix the parser"));
        assert_eq!(sheet.rows.iter().map(|r| r.2).collect::<Vec<_>>(), [0, 1]);
        assert!(sheet.rows.iter().all(|(first, count, _)| first + count <= sheet.lines.len()));
    }

    #[test]
    fn an_executor_that_is_out_says_why_and_a_priceless_one_shows_its_tokens() {
        let sheet = arena_sheet(&arena(), 0, true, 110, &EN, 0);
        let text = all(&sheet);
        assert!(text.contains("its tests keep failing"));
        let second = plain(&sheet.lines[sheet.rows[1].0]);
        assert!(second.contains("out") && second.contains("12.3k tok"), "{second}");
        // The muted row is drawn in the muted colour.
        assert!(sheet.lines[sheet.rows[1].0].iter().any(|(s, style)| s.contains("style-e2") && style.fg == Some(DUSK)));
    }

    #[test]
    fn the_arena_speaks_both_languages_and_never_says_judge() {
        for (strings, banned) in [(&EN, "judge"), (&RU, "судь")] {
            let text = all(&arena_sheet(&arena(), 0, false, 100, strings, 0)).to_lowercase();
            assert!(!text.contains(banned));
            assert!(text.contains("best-of"));
        }
        let russian = all(&arena_sheet(&arena(), 0, false, 100, &RU, 0));
        assert!(russian.contains("Несколько агентов") && russian.contains("шаг 7") && russian.contains("выбыл"));
    }

    #[test]
    fn a_narrow_window_drops_the_step_column_and_keeps_every_row_in_width() {
        for width in [60, 80, 100] {
            let sheet = arena_sheet(&arena(), 1, false, width, &EN, 0);
            for line in &sheet.lines {
                assert!(text::line_width(line) <= width + 2, "{width}: {}", plain(line));
            }
        }
        assert!(!plain(&arena_sheet(&arena(), 0, false, 70, &EN, 0).lines[arena_sheet(&arena(), 0, false, 70, &EN, 0).rows[0].0]).contains("step"));
    }

    #[test]
    fn a_finished_run_ends_in_a_line_of_its_own() {
        let mut arena = arena();
        arena.finished = Some(Finished { run_id: "r1".into(), outcome: Some(Finish::Accepted), id: "e1".into(), commit: "abc1234".into(), message: "Committed".into() });
        let text = all(&arena_sheet(&arena, 0, false, 100, &EN, 0));
        assert!(text.contains("Accepted · Committed · abc1234"), "{text}");
        assert!(all(&arena_sheet(&arena, 0, true, 100, &EN, 0)).contains("stop"), "while the turn runs Esc stops it");
    }

    fn council() -> Council {
        let members = ["simplicity", "security", "speed", "maintenance"]
            .iter()
            .enumerate()
            .map(|(i, angle)| Member { id: format!("m{}", i + 1), angle: angle.to_string() })
            .collect();
        let mut council = Council::new("c1".into(), "Which queue?".into(), members);
        council.card("m1", "simplicity", "Use a list".into(), "It is enough".into());
        council.objection("m1", "simplicity", vec![crate::arena::Risk { target: "m2".into(), risk: "grows without limit".into() }]);
        council.verdict = Some(CouncilVerdict {
            council_id: "c1".into(),
            chosen: "m1".into(),
            decision: "Use a list".into(),
            reason: "Simplest.".into(),
            dissent: vec![Dissent { who: "security".into(), why: "a list is unbounded".into() }],
        });
        council
    }

    #[test]
    fn the_council_shows_four_cards_with_their_angle_and_one_line_about_it() {
        for width in [70, 100] {
            let text = all(&council_sheet(&council(), width, &EN, 0));
            for part in ["council", "Which queue?", "Simplicity", "the simplest thing that works", "Security", "Development speed", "what is quickest to build and ship", "Maintenance cost", "what it will cost to live with later"] {
                assert!(text.contains(part), "{width}: {part}");
            }
        }
        let russian = all(&council_sheet(&council(), 100, &RU, 0));
        for part in ["Простота", "самое простое, что работает", "Скорость разработки", "что быстрее всего сделать и выпустить", "Цена поддержки", "во что обойдётся жить с этим потом"] {
            assert!(russian.contains(part), "{part}");
        }
    }

    #[test]
    fn the_core_names_a_member_by_its_angle_and_every_event_carries_that_same_id() {
        // The core's own shape: executor_id and angle are one id, and the interface translates it.
        let ids = ["simplicity", "security", "speed", "maintenance"];
        let mut council = Council::new("c1".into(), "q".into(), ids.iter().map(|id| Member { id: id.to_string(), angle: id.to_string() }).collect());
        council.card("speed", "speed", "Ship the script".into(), "It is done today".into());
        council.objection("security", "security", vec![crate::arena::Risk { target: "speed".into(), risk: "no checks".into() }]);
        council.verdict = Some(CouncilVerdict {
            council_id: "c1".into(),
            chosen: "speed".into(),
            decision: "Ship the script".into(),
            reason: "Today beats perfect.".into(),
            dissent: vec![Dissent { who: "maintenance".into(), why: "nobody can change it".into() }],
        });
        assert_eq!(council.cards.len(), 4, "a card for a known member is filled in, not added");
        assert_eq!(council.cards.iter().map(|c| council.chosen(c)).collect::<Vec<_>>(), [false, false, true, false]);
        let text = all(&council_sheet(&council, 100, &EN, 0));
        assert!(text.contains("Development speed · chosen"), "{text}");
        assert!(text.contains("→ Development speed: no checks"), "a risk names its target by the translated angle: {text}");
        assert!(text.contains("Maintenance cost: nobody can change it"), "{text}");
        let russian = all(&council_sheet(&council, 100, &RU, 0));
        assert!(russian.contains("Скорость разработки · выбрано") && russian.contains("→ Скорость разработки: no checks") && russian.contains("Цена поддержки: nobody can change it"), "{russian}");
    }

    #[test]
    fn the_chosen_card_is_marked_a_card_to_come_waits_and_risks_name_their_target() {
        let text = all(&council_sheet(&council(), 100, &EN, 0));
        assert!(text.contains("Simplicity · chosen"), "{text}");
        assert!(text.contains("thinking…"), "the cards that have not arrived wait");
        assert!(text.contains("→ Security: grows without limit"), "the risk is addressed to a card: {text}");
        assert!(text.contains("Why: Simplest.") && text.contains("Disagree:") && text.contains("Security: a list is unbounded"));
        // Without a verdict nothing is marked.
        let mut open = council();
        open.verdict = None;
        assert!(!all(&council_sheet(&open, 100, &EN, 0)).contains("chosen"));
    }

    #[test]
    fn a_log_shows_loading_the_steps_and_results_or_why_it_failed() {
        let mut log = ExecLog::open("e1", "style-e1", "s-e1", None);
        let failed = all(&log_sheet(&log, 80, &EN, 0));
        assert!(failed.contains("style-e1") && failed.contains("read only") && failed.contains("The core is not connected"));
        log.state = LogState::Loading;
        assert!(all(&log_sheet(&log, 80, &EN, 0)).contains("Reading the executor's journal"));
        log.session.apply(crate::model::CoreEvent::Step { call: "c1".into(), kind: crate::model::ToolKind::Shell, label: "runs pytest".into() }, std::time::Instant::now());
        log.session.apply(crate::model::CoreEvent::Result { call: "c1".into(), ok: true, summary: "exit 0".into(), diff: None }, std::time::Instant::now());
        log.state = LogState::Ready { older: true };
        let ready = all(&log_sheet(&log, 80, &EN, 0));
        assert!(ready.contains("runs pytest") && ready.contains("exit 0") && ready.contains("Earlier messages"), "{ready}");
    }

    #[test]
    fn a_patch_is_shown_a_file_at_a_time_and_a_cut_is_marked() {
        let patch = "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old a\n+new a\ndiff --git a/dir/b.rs b/dir/b.rs\n--- a/dir/b.rs\n+++ b/dir/b.rs\n@@ -1 +1 @@\n-old b\n+new b\n[... 120 characters omitted ...]";
        let diff = ArenaDiff { run_id: "r1".into(), id: "e1".into(), patch: patch.into(), files: vec!["a.py".into(), "dir/b.rs".into()] };
        let text = patch_lines(&diff, 80, "diff cut off").iter().map(plain).collect::<Vec<_>>().join("\n");
        assert!(text.contains(" a.py") && text.contains(" dir/b.rs"));
        assert!(text.contains("new a") && text.contains("old b") && text.contains("new b"));
        assert!(text.contains("diff cut off"), "{text}");
        let bare = ArenaDiff { run_id: "r1".into(), id: "e1".into(), patch: "@@ -1 +1 @@\n-x\n+y".into(), files: vec!["only.py".into()] };
        assert!(patch_lines(&bare, 80, "cut").iter().map(plain).collect::<String>().contains("only.py"));
    }
}
