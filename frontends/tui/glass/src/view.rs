// Draws the cells: text over the glass cards. Pictures are placed separately
// (pictures.rs); this module also records where the clickable things are.

use std::time::Instant;

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Modifier, Style};

use crate::anim::Ease;
use crate::app::{App, Modal, Phase};
use crate::dialog::{Answer, Ask, Body};
use crate::diff;
use crate::layout::{self, BROWSER_ASPECT, MARGIN_X};
use crate::model::{CallState, Helper, HelperState, Item, ToolKind};
use crate::markdown;
use crate::math;
use crate::models::{ModelPicker, Role, Row};
use crate::slash;
use crate::strings::{self, strings};
use crate::text::{self, Line, Painter};
use crate::water::TextView;
use crate::theme::{self, DUSK, FOG, HOVER, INK, INK_SOFT, MIST, MOON, MOSS, NIGHT, ROSEHIP};

/// Typewriter speed for the agent's words, characters per second.
const REVEAL_PER_SECOND: f32 = 220.0;
/// A fast model outruns the typewriter; the reveal never trails the text by more than this many characters.
const MAX_REVEAL_LAG: usize = 160;

fn revealed_chars(total: usize, elapsed: std::time::Duration) -> usize {
    let by_time = (elapsed.as_secs_f32() * REVEAL_PER_SECOND) as usize;
    by_time.max(total.saturating_sub(MAX_REVEAL_LAG))
}
/// The most choices a dialog shows at once; the rest scroll.
const MAX_LIST_ROWS: u16 = 14;
/// The most lines of a dialog's explanation (or an approval's diff) shown
/// above its answer.
const MAX_BODY_LINES: usize = 12;
/// The most rows of an overwrite's diff shown at once; the rest scroll.
const MAX_DIFF_LINES: usize = 30;
/// A helper's latest calls in the helpers panel; earlier ones are in its
/// service session.
const HELPER_CALLS_SHOWN: usize = 6;
/// Rows of the model picker, padding included.
pub const MODELS_HEIGHT: u16 = 30;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Hit {
    File(usize),
    /// A helper, by its place in the list.
    Helper(usize),
    HelpersTitle,
    Thumb,
    /// An attached picture, by the N of its marker.
    Attachment(u32),
    PopupClose,
    HelpersClose,
    DiffClose,
    /// A visible row of a choice dialog.
    Choice(usize),
    /// A row of the slash menu, by its index in the suggestions.
    Command(usize),
    /// A button of a confirmation.
    Answer(Answer),
    Input,
    NewSession,
    Provider,
    Models,
    /// A row of the model picker, by its index in `ModelPicker::rows()`.
    ModelRow(usize),
    /// The smart/fast switch of the model picker.
    Role(Role),
}

impl Hit {
    pub fn clickable(self) -> bool {
        !matches!(self, Hit::Input)
    }
}

pub struct Drawn {
    pub hits: Vec<(Rect, Hit)>,
    /// Where the text cursor goes, when the input has the keyboard.
    pub cursor: Option<(u16, u16)>,
    /// The glass under the conversation, sized to its text.
    pub reading: Option<Rect>,
    /// Where the conversation's lines are, for the pointer.
    pub text: Option<TextView>,
    /// The display width and the plain text of every line of the conversation.
    pub widths: Vec<usize>,
    pub plain: Vec<String>,
    /// The display formulas showing in the conversation, to be drawn as pictures.
    pub formulas: Vec<FormulaSpot>,
}

/// A display formula's picture: where it goes and how much of it shows (the rest is scrolled off).
#[derive(Clone, Debug, PartialEq)]
pub struct FormulaSpot {
    pub tex: String,
    pub fit: math::Fit,
    pub col: u16,
    /// Screen row of the first visible row of the picture.
    pub row: u16,
    /// Rows of the picture above the visible part.
    pub hidden: u16,
    pub shown: u16,
}

fn st(color: Color) -> Style {
    Style::default().fg(color)
}

fn span(s: impl Into<String>, color: Color) -> (String, Style) {
    (s.into(), st(color))
}

pub fn draw(app: &App, buf: &mut Buffer, now: Instant) -> Drawn {
    let mut drawn = Drawn { hits: Vec::new(), cursor: None, reading: None, text: None, widths: Vec::new(), plain: Vec::new(), formulas: Vec::new() };
    let area = app.grid.full();
    let ms = now.saturating_duration_since(app.started).as_millis();
    let mut p = Painter { buf, clip: area };
    let since = match app.phase {
        Phase::Splash => {
            draw_splash(&mut p, app, ms);
            return drawn;
        }
        Phase::Main { since } => since,
    };

    draw_left(&mut p, app, now, &mut drawn);
    draw_browser_label(&mut p, app, now, &mut drawn);
    draw_helpers(&mut p, app, now, ms, &mut drawn);
    draw_diff(&mut p, app, now, &mut drawn);
    drawn.reading = draw_transcript(&mut p, app, now, ms, &mut drawn);
    draw_input(&mut p, app, now, &mut drawn);
    draw_strip(&mut p, app, now, &mut drawn);
    draw_menu(&mut p, app, now, &mut drawn);
    draw_hints(&mut p, app);
    draw_status(&mut p, app, now, &mut drawn);
    intro_cascade(p.buf, area, now.saturating_duration_since(since).as_millis() as f32);

    let modal = app.modal_p.value(now).clamp(0.0, 1.0);
    if modal > 0.0 {
        text::fade(p.buf, area, modal * 0.82, NIGHT);
        drawn.hits.clear();
    }
    match &app.modal {
        Some(Modal::Ask(ask)) => draw_ask(&mut p, app, ask.as_ref(), modal, ms, &mut drawn),
        Some(Modal::Models(picker)) => draw_models(&mut p, app, picker.as_ref(), modal, ms, &mut drawn),
        // Only once it has landed: while it grows or shrinks the title would
        // hang where the browser is not.
        Some(Modal::Browser) if app.modal_p.target() == 1.0 && !app.modal_p.running(now) => {
            draw_popup_title(&mut p, app, &mut drawn)
        }
        Some(Modal::Image(id)) if app.modal_p.target() == 1.0 && !app.modal_p.running(now) => {
            draw_image_title(&mut p, app, *id, &mut drawn)
        }
        _ => {}
    }
    drawn
}

/// Text appears row by row from the top, rising out of the night.
fn intro_cascade(buf: &mut Buffer, area: Rect, elapsed_ms: f32) {
    if elapsed_ms > 1600.0 {
        return;
    }
    for y in area.top()..area.bottom() {
        let t = ((elapsed_ms - 120.0 - y as f32 * 9.0) / 320.0).clamp(0.0, 1.0);
        text::fade(buf, Rect::new(area.x, y, area.width, 1), 1.0 - Ease::OutCubic.apply(t), NIGHT);
    }
}

/// The photograph alone while the core starts; if it cannot start, why.
fn draw_splash(p: &mut Painter, app: &App, ms: u128) {
    let s = strings();
    let bottom = app.grid.rows as i32 - 3;
    let centre = |w: usize| (app.grid.cols as i32 - w as i32) / 2;
    if let Some(reason) = &app.core_error {
        let width = (app.grid.cols as usize).saturating_sub(8).min(100);
        let mut lines = vec![vec![span(s.core_failed, ROSEHIP)]];
        lines.extend(text::wrap(&vec![span(reason.as_str(), MIST)], width).into_iter().take(4));
        lines.push(vec![span("ctrl+c", MIST), span(format!(" {}", s.quit), DUSK)]);
        let top = bottom - lines.len() as i32 + 1;
        for (i, line) in lines.iter().enumerate() {
            p.line(centre(text::line_width(line)), top + i as i32, line);
        }
        return;
    }
    p.line(centre(5), bottom, &text::shimmer("Kvist", ms, DUSK, MOON));
    if ms > 3000 {
        p.text(centre(text::width(s.core_starting)), bottom + 1, s.core_starting, st(DUSK));
    }
}

/// Columns a sliding card is moved by when its tween is at `v`.
fn slide_cols(rect: Rect, v: f32) -> i32 {
    ((1.0 - v) * (rect.width + MARGIN_X + 1) as f32).round() as i32
}

fn draw_left(p: &mut Painter, app: &App, now: Instant, drawn: &mut Drawn) {
    let Some(card) = app.layout.left else { return };
    let off = -slide_cols(card, app.left_in.value(now));
    let s = strings();
    let x = card.x as i32 + 2 + off;
    let right = card.right() as i32 - 2 + off;
    let mut y = card.y as i32 + 1;
    let session = &app.session;

    if !session.files.is_empty() {
        let (added, removed) = session.totals();
        p.text(x, y, s.files, st(MIST));
        p.line_right(right, y, &counts(added, removed));
        y += 1;
        for (index, file) in session.files.iter().enumerate() {
            let row = Rect::new(card.x, y as u16, card.width, 1);
            let open = app.diff.as_ref().is_some_and(|view| view.path == file.path);
            if open || app.hover == Some(Hit::File(index)) {
                p.fill(card.x as i32 + 1 + off, y, card.width as i32 - 2, HOVER);
            }
            let since = now.saturating_duration_since(file.touched).as_millis() as f32;
            let grow = Ease::OutCubic.apply(since / 450.0);
            let count = |from: u32, to: u32| (from as f32 + (to as f32 - from as f32) * grow).round() as u32;
            let numbers = counts(count(file.previous.0, file.added), count(file.previous.1, file.removed));
            let numbers_w = text::line_width(&numbers) as i32;
            let glow = 1.0 - (since / 1200.0).min(1.0);
            let (icon, icon_color) = theme::file_icon(&file.path);
            let (dir, name) = file.path.rsplit_once('/').unwrap_or(("", &file.path));
            let room = (right - x - numbers_w - 3).max(4) as usize;
            let name = text::fit(name, room);
            let mut cx = p.text(x, y, icon, st(icon_color));
            cx = p.text(cx + 1, y, &name, st(theme::mix(FOG, MOON, glow)));
            let dir_room = (right - numbers_w - 2 - cx - 2).max(0) as usize;
            if let Some(dir) = folder_label(dir, dir_room) {
                p.text(cx + 2, y, &dir, st(DUSK));
            }
            p.line_right(right, y, &numbers);
            drawn.hits.push((row, Hit::File(index)));
            y += 1;
        }
    }

    if session.helpers.is_empty() {
        return;
    }
    if !session.files.is_empty() {
        y += 1;
    }
    let title_row = Rect::new(card.x, y as u16, card.width, 1);
    let title_color = if app.hover == Some(Hit::HelpersTitle) { FOG } else { MIST };
    let end = p.text(x, y, s.helpers, st(title_color));
    p.text(end + 1, y, theme::I_CHEVRON, st(DUSK));
    drawn.hits.push((title_row, Hit::HelpersTitle));
    y += 1;
    let ms = now.saturating_duration_since(app.started).as_millis();
    for (index, helper) in session.helpers.iter().enumerate() {
        let row = Rect::new(card.x, y as u16, card.width, 1);
        if app.hover == Some(Hit::Helper(index)) {
            p.fill(card.x as i32 + 1 + off, y, card.width as i32 - 2, HOVER);
        }
        let (mark, color) = helper_mark(helper.state, ms);
        let cx = p.text(x, y, mark, st(color));
        let cx = p.text(cx + 1, y, &helper_name(index), st(FOG));
        let task = if helper.state == HelperState::Running { helper_doing(helper) } else { state_word(helper.state) };
        let room = (right - cx - 2).max(0) as usize;
        if room > 3 {
            p.text(cx + 2, y, &text::fit(task, room), st(DUSK));
        }
        drawn.hits.push((row, Hit::Helper(index)));
        y += 1;
    }
}

fn helper_name(index: usize) -> String {
    format!("{} {}", strings().helper_name, index + 1)
}

/// The diff of the file chosen in the left card: its name and counts, then
/// every edit of this session as numbered, coloured rows.
fn draw_diff(p: &mut Painter, app: &App, now: Instant, drawn: &mut Drawn) {
    let (Some(panel), Some(view)) = (app.layout.diff, app.diff.as_ref()) else { return };
    let open = app.diff_in.value(now);
    if open <= 0.001 {
        return;
    }
    let s = strings();
    let off = slide_cols(panel, open);
    let x = panel.x as i32 + 2 + off;
    let right = panel.right() as i32 - 2 + off;
    let top = panel.y as i32 + 1;
    let file = app.session.files.iter().find(|file| file.path == view.path);

    let close_hover = app.hover == Some(Hit::DiffClose);
    p.text(right - 1, top, theme::I_CLOSE, st(if close_hover { MOON } else { DUSK }));
    drawn.hits.push((Rect::new((right - 2).max(0) as u16, top.max(0) as u16, 3, 1), Hit::DiffClose));
    let numbers = file.map(|file| counts(file.added, file.removed)).unwrap_or_default();
    let numbers_w = text::line_width(&numbers) as i32;
    p.line_right(right - 3, top, &numbers);

    let (icon, icon_color) = theme::file_icon(&view.path);
    let (dir, name) = view.path.rsplit_once('/').unwrap_or(("", &view.path));
    let room = (right - 3 - numbers_w - x - 4).max(4) as usize;
    let cx = p.text(x, top, icon, st(icon_color));
    let cx = p.text(cx + 1, top, &text::fit(name, room), Style::default().fg(FOG).add_modifier(Modifier::BOLD));
    let dir_room = (right - 3 - numbers_w - cx - 3).max(0) as usize;
    if let Some(dir) = folder_label(dir, dir_room) {
        p.text(cx + 2, top, &dir, st(DUSK));
    }

    let body_top = top + 2;
    let rows = app.diff_rows();
    if view.lines.is_empty() {
        let width = (right - x).max(8) as usize;
        for (i, line) in text::wrap(&vec![span(s.diff_none, DUSK)], width).iter().enumerate() {
            p.line(x, body_top + i as i32, line);
        }
        return;
    }
    for (i, line) in view.lines.iter().skip(view.scroll).take(rows).enumerate() {
        p.line(x, body_top + i as i32, line);
    }
    let edits = file.map_or(0, |file| file.patches.len());
    let from = view.scroll + 1;
    let to = (view.scroll + rows).min(view.lines.len());
    let footer = vec![
        span(format!("{}: {edits}", s.diff_edits), MIST),
        span(format!("    {from}–{to}/{}", view.lines.len()), DUSK),
        span(format!("    {}", s.diff_scroll), MIST),
        span("    esc", MIST),
        span(format!(" {}", s.diff_close), DUSK),
    ];
    p.line(x, panel.bottom() as i32 - 2, &footer);
}

/// "+12 −3", leaving out a side that is zero.
fn counts(added: u32, removed: u32) -> Line {
    let mut line = Vec::new();
    if added > 0 {
        line.push(span(format!("+{added}"), MOSS));
    }
    if removed > 0 {
        let gap = if added > 0 { " " } else { "" };
        line.push(span(format!("{gap}−{removed}"), ROSEHIP));
    }
    line
}

/// The whole folder path if it fits, else its last folder, else nothing:
/// a cut-off "…ests" tells less than no folder at all.
fn folder_label(dir: &str, room: usize) -> Option<String> {
    if dir.is_empty() || room < 3 {
        return None;
    }
    if text::width(dir) <= room {
        return Some(dir.to_string());
    }
    let last = dir.rsplit('/').next().unwrap_or(dir);
    (text::width(last) <= room).then(|| last.to_string())
}

fn helper_mark(state: HelperState, ms: u128) -> (&'static str, Color) {
    match state {
        HelperState::Running => {
            let pulse = ((ms as f32 / 1000.0 * std::f32::consts::TAU / 1.6).sin() + 1.0) / 2.0;
            ("●", theme::mix(DUSK, MOON, pulse))
        }
        HelperState::Done => (theme::I_CHECK, MOSS),
        HelperState::Failed => (theme::I_ERROR, ROSEHIP),
    }
}

fn state_word(state: HelperState) -> &'static str {
    let s = strings();
    match state {
        HelperState::Running => s.helper_running,
        HelperState::Done => s.helper_done,
        HelperState::Failed => s.helper_failed,
    }
}

fn draw_browser_label(p: &mut Painter, app: &App, now: Instant, drawn: &mut Drawn) {
    let (Some(card), Some(thumb), Some(browser)) = (app.layout.browser_card, app.layout.thumb, &app.session.browser) else {
        return;
    };
    let off = slide_cols(card, app.browser_in.value(now));
    if app.image_pending {
        let waiting = format!("{} {}", theme::spinner(now.saturating_duration_since(app.started).as_millis()), strings().drawing);
        let x = thumb.x as i32 + (thumb.width as i32 - waiting.chars().count() as i32) / 2 + off;
        p.text(x, thumb.y as i32 + thumb.height as i32 / 2, &waiting, st(MIST));
    }
    let y = thumb.bottom() as i32;
    let x = card.x as i32 + 2 + off;
    let right = card.right() as i32 - 2 + off;
    let hovered = app.hover == Some(Hit::Thumb);
    let cx = p.text(x, y, theme::I_GLOBE, st(if hovered { MOON } else { MIST }));
    let room = (right - cx - 4).max(0) as usize;
    let title = if browser.title.is_empty() { strings().browser } else { browser.title.as_str() };
    p.text(cx + 1, y, &text::fit(title, room), st(if hovered { FOG } else { MIST }));
    p.text(right - 1, y, theme::I_EXPAND, st(if hovered { MOON } else { DUSK }));
    drawn.hits.push((card, Hit::Thumb));
}

fn draw_helpers(p: &mut Painter, app: &App, now: Instant, ms: u128, drawn: &mut Drawn) {
    let open = app.helpers_in.value(now);
    let Some(panel) = app.layout.helpers else { return };
    if open <= 0.001 {
        return;
    }
    let s = strings();
    let off = slide_cols(panel, open);
    let x = panel.x as i32 + 2 + off;
    let right = panel.right() as i32 - 2 + off;
    let top = panel.y as i32 + 1;
    let cx = p.text(x, top, theme::I_HELPER, st(MOON));
    p.text(cx + 1, top, s.helpers, Style::default().fg(FOG).add_modifier(Modifier::BOLD));
    let close_hover = app.hover == Some(Hit::HelpersClose);
    p.text(right - 1, top, theme::I_CLOSE, st(if close_hover { MOON } else { DUSK }));
    drawn.hits.push((Rect::new((right - 2).max(0) as u16, top as u16, 3, 1), Hit::HelpersClose));

    let width = (right - x).max(8) as usize;
    let mut lines: Vec<Line> = Vec::new();
    if app.session.helpers.is_empty() {
        lines.push(vec![span(s.helpers_idle, DUSK)]);
    }
    for (index, helper) in app.session.helpers.iter().enumerate() {
        lines.extend(helper_block(index, helper, app.helper_focus == Some(index), width, ms));
    }
    let avail = (panel.height as i32 - 4).max(0) as usize;
    let skip = lines.len().saturating_sub(avail);
    for (i, line) in lines.iter().skip(skip).enumerate() {
        p.line(x, top + 2 + i as i32, line);
    }
}

fn helper_block(index: usize, helper: &Helper, focused: bool, width: usize, ms: u128) -> Vec<Line> {
    let (mark, color) = helper_mark(helper.state, ms);
    let name_style = Style::default().fg(if focused { MOON } else { FOG }).add_modifier(Modifier::BOLD);
    let state_color = match helper.state {
        HelperState::Running => MIST,
        HelperState::Done => MOSS,
        HelperState::Failed => ROSEHIP,
    };
    let mut head: Line = vec![span(mark, color), span(" ", FOG), (helper_name(index), name_style)];
    let word = state_word(helper.state);
    let gap = width.saturating_sub(text::line_width(&head) + text::width(word));
    head.push(span(" ".repeat(gap), FOG));
    head.push(span(word, state_color));
    let mut lines = vec![head];
    let indented = |part: &str, color, lines: &mut Vec<Line>| {
        for line in text::wrap(&vec![span(part, color)], width.saturating_sub(2)).into_iter().take(3) {
            if line.is_empty() {
                continue;
            }
            let mut row = vec![span("  ", FOG)];
            row.extend(line);
            lines.push(row);
        }
    };
    indented(&helper.task, MIST, &mut lines);
    let shown = helper.calls.len().saturating_sub(HELPER_CALLS_SHOWN);
    for call in &helper.calls[shown..] {
        let mut row = vec![span("  ", FOG)];
        row.extend(tool_line(call.kind, &call.label, &call.state, width.saturating_sub(2), ms));
        lines.push(row);
        let tail = call.output.lines().filter(|l| !l.trim().is_empty()).collect::<Vec<_>>();
        for out in &tail[tail.len().saturating_sub(2)..] {
            lines.push(vec![span("    ", FOG), span(text::fit(out, width.saturating_sub(4)), DUSK)]);
        }
    }
    if helper.state != HelperState::Running {
        indented(&helper.summary, DUSK, &mut lines);
    }
    lines.push(Vec::new());
    lines
}

/// What a helper is doing right now, for its row in the left card: the
/// label of its running call, else its task.
fn helper_doing(helper: &Helper) -> &str {
    helper
        .calls
        .iter()
        .rev()
        .find(|call| call.state == CallState::Running)
        .map_or(helper.task.as_str(), |call| call.label.as_str())
}

/// Builds every line of the conversation for a column `width` wide.
fn transcript_lines(app: &App, width: usize, now: Instant, ms: u128) -> (Vec<Line>, Vec<markdown::Formula>) {
    let mut lines: Vec<Line> = Vec::new();
    let mut formulas: Vec<markdown::Formula> = Vec::new();
    let mut previous_tool = false;
    let last_index = app.session.items.len().saturating_sub(1);
    for (index, item) in app.session.items.iter().enumerate() {
        let is_tool = matches!(item, Item::Tool { .. });
        if !lines.is_empty() && !(is_tool && previous_tool) {
            lines.push(Vec::new());
        }
        previous_tool = is_tool;
        match item {
            Item::User(text) => {
                for raw in text.split('\n') {
                    for line in text::wrap(&vec![span(raw, FOG)], width - 2) {
                        let mut row = vec![span("▎ ", MOON)];
                        row.extend(line);
                        lines.push(row);
                    }
                }
            }
            Item::Agent { text: words, at } => {
                // Words followed by anything else are shown whole: the work they describe has moved on.
                let shown = if index < last_index {
                    usize::MAX
                } else {
                    revealed_chars(words.chars().count(), now.saturating_duration_since(*at))
                };
                let visible: String = words.chars().take(shown.max(1)).collect();
                let (rendered, found) = markdown::render_with_math(&visible, width, Some((app.grid.cw, app.grid.ch)));
                let base = lines.len();
                formulas.extend(found.into_iter().map(|formula| markdown::Formula { line: formula.line + base, ..formula }));
                lines.extend(rendered);
            }
            Item::Tool { kind, label, state, .. } => lines.push(tool_line(*kind, label, state, width, ms)),
            Item::Error(message) => {
                let mut first = true;
                for line in text::wrap(&vec![span(message.as_str(), ROSEHIP)], width - 2) {
                    let mut row = vec![span(if first { theme::I_ERROR } else { " " }, ROSEHIP), span(" ", FOG)];
                    row.extend(line);
                    lines.push(row);
                    first = false;
                }
            }
        }
    }
    if app.session.busy
        && let Some(since) = app.session.thinking_since
    {
        if !lines.is_empty() {
            lines.push(Vec::new());
        }
        let s = strings();
        let mut row = text::shimmer(s.thinking, ms, DUSK, MOON);
        let secs = now.saturating_duration_since(since).as_secs();
        row.push(span(format!("  {secs} {}", s.seconds), DUSK));
        lines.push(row);
    }
    (lines, formulas)
}

fn tool_line(kind: ToolKind, label: &str, state: &CallState, width: usize, ms: u128) -> Line {
    let icon = match kind {
        ToolKind::Read => theme::I_EYE,
        ToolKind::Search => theme::I_SEARCH,
        ToolKind::Write => theme::I_EDIT,
        ToolKind::Shell => theme::I_TERMINAL,
        ToolKind::Web => theme::I_GLOBE,
        ToolKind::Other => theme::I_CHEVRON,
    };
    let mut tail: Line = Vec::new();
    let (icon, icon_color) = match state {
        CallState::Running => (theme::spinner(ms), MOON),
        CallState::Done { ok: true, summary, diff } => {
            match diff {
                Some((added, removed)) if added + removed > 0 => {
                    tail.push(span("  ", FOG));
                    tail.extend(counts(*added, *removed));
                }
                _ if !summary.is_empty() => tail.push(span(format!("  {summary}"), DUSK)),
                _ => {}
            }
            (icon, MIST)
        }
        CallState::Done { ok: false, summary, .. } => {
            tail.push(span(format!("  {summary}"), ROSEHIP));
            (theme::I_ERROR, ROSEHIP)
        }
    };
    let tail_room = width / 2;
    if text::line_width(&tail) > tail_room {
        let joined: String = tail.iter().map(|(s, _)| s.as_str()).collect();
        let color = tail.first().and_then(|(_, style)| style.fg).unwrap_or(DUSK);
        tail = vec![span(text::fit(&joined, tail_room), color)];
    }
    let room = width.saturating_sub(4 + text::line_width(&tail)).max(8);
    let mut line = vec![span(icon, icon_color), span(" ", FOG), span(text::fit(label, room), MIST)];
    line.extend(tail);
    line
}

pub fn revealing(app: &App, now: Instant) -> bool {
    app.session.items.iter().rev().take(1).any(|item| match item {
        Item::Agent { text, at } => {
            let total = text.chars().count();
            revealed_chars(total, now.saturating_duration_since(*at)) < total
        }
        _ => false,
    })
}

fn draw_transcript(p: &mut Painter, app: &App, now: Instant, ms: u128, drawn: &mut Drawn) -> Option<Rect> {
    let area = app.layout.transcript;
    if app.session.items.is_empty() || area.height < 4 {
        return None;
    }
    let width = area.width.saturating_sub(6) as usize;
    let (lines, formulas) = transcript_lines(app, width, now, ms);
    drawn.widths = lines.iter().map(text::line_width).collect();
    drawn.plain = lines.iter().map(|line| line.iter().map(|(s, _)| s.as_str()).collect()).collect();
    let avail = (area.height - 2) as usize;
    let scroll = app.scroll.min(lines.len().saturating_sub(avail));
    let end = lines.len() - scroll;
    let start = end.saturating_sub(avail);
    let shown = end - start;
    let height = shown as u16 + 2;
    let card = Rect::new(area.x, area.bottom() - height, area.width, height);
    for (i, line) in lines[start..end].iter().enumerate() {
        p.line(card.x as i32 + 3, card.y as i32 + 1 + i as i32, line);
    }
    drawn.formulas = formulas
        .iter()
        .filter_map(|formula| {
            let (top, bottom) = (formula.line.max(start), (formula.line + formula.fit.rows as usize).min(end));
            (top < bottom).then(|| FormulaSpot {
                tex: formula.tex.clone(),
                fit: formula.fit,
                col: card.x + 3,
                row: card.y + 1 + (top - start) as u16,
                hidden: (top - formula.line) as u16,
                shown: (bottom - top) as u16,
            })
        })
        .collect();
    drawn.text = Some(TextView { x: card.x as i32 + 3, top: card.y as i32 + 1, first: start, rows: shown });
    Some(card)
}

/// Lines of the input, wrapped by character, and where the caret sits.
pub fn input_lines(input: &str, cursor: usize, width: usize) -> (Vec<String>, (usize, usize)) {
    let (lines, carets) = input_layout(input, width);
    let caret = carets[cursor.min(carets.len() - 1)];
    (lines, caret)
}

/// The wrapped lines and, for every cursor position 0..=length, the (row,
/// column) where the caret is drawn.
fn input_layout(input: &str, width: usize) -> (Vec<String>, Vec<(usize, usize)>) {
    let width = width.max(4);
    let mut lines = vec![String::new()];
    let mut used = 0;
    let mut carets = Vec::new();
    for c in input.chars() {
        carets.push((lines.len() - 1, used));
        if c == '\n' {
            lines.push(String::new());
            used = 0;
            continue;
        }
        let w = unicode_width::UnicodeWidthChar::width(c).unwrap_or(0);
        if used + w > width {
            lines.push(String::new());
            used = 0;
        }
        lines.last_mut().unwrap().push(c);
        used += w;
    }
    if used >= width {
        lines.push(String::new());
        used = 0;
    }
    carets.push((lines.len() - 1, used));
    (lines, carets)
}

/// The cursor position one drawn row above (`up`) or below the cursor, in the
/// column nearest to where it is; `None` on the first or last row.
pub fn move_row(input: &str, cursor: usize, width: usize, up: bool) -> Option<usize> {
    let (_, carets) = input_layout(input, width);
    let (row, column) = carets[cursor.min(carets.len() - 1)];
    let target = if up { row.checked_sub(1)? } else { row + 1 };
    carets
        .iter()
        .enumerate()
        .filter(|(_, caret)| caret.0 == target && caret.1 <= column)
        .max_by_key(|(index, caret)| (caret.1, *index))
        .map(|(index, _)| index)
}

fn draw_input(p: &mut Painter, app: &App, now: Instant, drawn: &mut Drawn) {
    let card = app.layout.input;
    if let Some((notice, at)) = &app.session.closed {
        draw_closed(p, app, card, notice, *at, now, drawn);
        return;
    }
    let dy = (((1.0 - app.input_rise.value(now)) * 8.0).round()) as i32;
    let x = card.x as i32 + 3;
    let y = card.y as i32 + 1 + dy + app.layout.attach_rows as i32;
    let width = card.width.saturating_sub(6) as usize;
    let (lines, caret) = input_lines(&app.input, app.cursor, width);
    if app.input.is_empty() {
        p.text(x + 1, y, strings().placeholder, st(DUSK));
    }
    for (i, line) in lines.iter().enumerate().take(8) {
        p.text(x, y + i as i32, line, st(FOG));
    }
    // The terminal's own thin bar cursor blinks here (term.rs sets its shape and colour).
    if app.modal.is_none() {
        drawn.cursor = Some(((x + caret.1 as i32).max(0) as u16, (y + caret.0 as i32).max(0) as u16));
    }
    drawn.hits.push((card, Hit::Input));
}

/// What the pictures over the input leave to draw in cells: a hint on the one
/// pointed at, and how many did not fit. The pictures themselves are kitty's.
fn draw_strip(p: &mut Painter, app: &App, now: Instant, drawn: &mut Drawn) {
    let (shown, hidden) = app.strip(now);
    let dy = (((1.0 - app.input_rise.value(now)) * 8.0).round()) as i32;
    for (rect, attachment) in &shown {
        let hovered = app.hover == Some(Hit::Attachment(attachment.id));
        if hovered {
            p.text(rect.right() as i32 - 2, rect.y as i32 + dy, theme::I_EXPAND, st(MOON));
        }
        drawn.hits.push((*rect, Hit::Attachment(attachment.id)));
    }
    if hidden > 0 {
        let card = app.layout.input;
        let x = shown.last().map_or(card.x + 3, |(rect, _)| rect.right() + 2);
        let y = card.y as i32 + 1 + dy + (layout::STRIP_ROWS as i32 - 1) / 2;
        p.text(x as i32, y, &format!("+{hidden}"), st(MIST));
    }
}

/// The slash menu over the input: the commands the typed prefix leaves, the
/// chosen one lit. Its text fades in as the glass behind it frosts.
fn draw_menu(p: &mut Painter, app: &App, now: Instant, drawn: &mut Drawn) {
    let shown = app.menu();
    let card = layout::menu(app.layout.input, shown.len());
    if card.height == 0 {
        return;
    }
    p.clear(card);
    let rows = card.height as usize - 2;
    let chosen = app.chosen();
    let first = slash::window_start(shown.len(), chosen, rows);
    let russian = strings::russian();
    let x = card.x as i32 + 3;
    let right = card.right() as i32 - 3;
    let hint_x = x + slash::name_width() as i32 + 4;
    for (index, command) in shown.iter().enumerate().skip(first).take(rows) {
        let y = card.y as i32 + 1 + (index - first) as i32;
        let picked = index == chosen;
        if picked {
            p.fill(card.x as i32 + 1, y, card.width as i32 - 2, MOON);
        }
        let name = Style::default().fg(if picked { INK } else { FOG }).add_modifier(if picked { Modifier::BOLD } else { Modifier::empty() });
        p.text(x, y, &format!("/{}", command.name), name);
        let room = (right - hint_x).max(0) as usize;
        if room >= 8 {
            p.text(hint_x, y, &text::fit(command.hint(russian), room), st(if picked { INK_SOFT } else { DUSK }));
        }
        drawn.hits.push((Rect::new(card.x + 1, y.max(0) as u16, card.width - 2, 1), Hit::Command(index)));
    }
    if shown.len() > rows {
        let counter = format!("{}/{}", chosen + 1, shown.len());
        p.text(right - counter.len() as i32, card.bottom() as i32 - 1, &counter, st(DUSK));
    }
    let open = app.menu_in.value(now).clamp(0.0, 1.0);
    text::fade(p.buf, card, 1.0 - open, NIGHT);
}

/// A closed session: the core's notice where the input was, and one button.
/// Nothing can be typed; Enter does what the button does.
fn draw_closed(p: &mut Painter, app: &App, card: Rect, notice: &str, at: Instant, now: Instant, drawn: &mut Drawn) {
    let y = card.y as i32 + 1;
    let x = card.x as i32 + 3;
    let right = card.right() as i32 - 3;
    let label = format!(" {} ", strings().new_session);
    let button_w = text::width(&label) as i32;
    let button_x = right - button_w;
    let hint_x = button_x - 7;
    let cx = p.text(x, y, theme::I_CLOSED, st(DUSK));
    p.text(cx + 2, y, &text::fit(notice, (hint_x - cx - 4).max(4) as usize), st(MIST));
    p.text(hint_x, y, "enter", st(DUSK));
    let hovered = app.hover == Some(Hit::NewSession);
    let bg = if hovered { FOG } else { MOON };
    p.text(button_x, y, &label, Style::default().fg(INK).bg(bg).add_modifier(Modifier::BOLD));
    let appear = Ease::OutCubic.apply(now.saturating_duration_since(at).as_secs_f32() / 0.35);
    text::fade(p.buf, Rect::new(card.x, card.y, card.width, card.height), 1.0 - appear, NIGHT);
    drawn.hits.push((Rect::new(button_x.max(0) as u16, y as u16, button_w as u16, 1), Hit::NewSession));
}

fn draw_hints(p: &mut Painter, app: &App) {
    if !app.session.items.is_empty() || app.modal.is_some() || app.session.closed.is_some() {
        return;
    }
    let s = strings();
    let mut line: Line = Vec::new();
    for (i, (key, word)) in [("enter", s.hint_send), ("ctrl+p", s.hint_model), ("ctrl+t", s.hint_helpers), ("ctrl+v", s.hint_image)]
        .iter()
        .enumerate()
    {
        if i > 0 {
            line.push(span("    ", DUSK));
        }
        line.push(span(*key, MIST));
        line.push(span(format!(" {word}"), DUSK));
    }
    let x = (app.grid.cols as i32 - text::line_width(&line) as i32) / 2;
    p.line(x, app.layout.hints_row as i32, &line);
}

/// Folder and branch on the left; provider and model on the right (each
/// opens its picker); a notice in the middle.
fn draw_status(p: &mut Painter, app: &App, now: Instant, drawn: &mut Drawn) {
    let y = app.layout.status_row as i32;
    let folder = short_path(&app.workspace, (app.grid.cols / 4) as usize);
    let mut left = vec![span(theme::I_FOLDER, DUSK), span(format!(" {folder}"), MIST)];
    if !app.branch.is_empty() {
        left.extend([span("    ", DUSK), span(theme::I_BRANCH, DUSK), span(format!(" {}", app.branch), MIST)]);
    }
    let left_end = p.line(MARGIN_X as i32 + 1, y, &left);

    let agent = &app.agent;
    let lit = |hit: Hit| if app.hover == Some(hit) { MOON } else { MIST };
    let effort = format!("   {}", agent.effort);
    let right_edge = app.grid.cols as i32 - MARGIN_X as i32 - 1;
    let effort_x = right_edge - text::width(&effort) as i32;
    p.text(effort_x, y, &effort, st(DUSK));
    let model_x = effort_x - text::width(&agent.fast) as i32;
    p.text(model_x, y, &agent.fast, st(lit(Hit::Models)));
    drawn.hits.push((Rect::new(model_x.max(0) as u16, y as u16, text::width(&agent.fast) as u16, 1), Hit::Models));
    let provider = format!("{} {}  ", theme::I_CHIP, agent.provider);
    let provider_x = model_x - text::width(&provider) as i32;
    p.text(provider_x, y, &provider, st(lit(Hit::Provider)));
    drawn.hits.push((Rect::new(provider_x.max(0) as u16, y as u16, text::width(&provider) as u16 - 2, 1), Hit::Provider));

    let Some(toast) = &app.toast else { return };
    let room = (provider_x - left_end - 6).max(0) as usize;
    let age = now.saturating_duration_since(toast.at).as_secs_f32();
    let fade = ((age - 3.5) / 1.0).clamp(0.0, 1.0);
    let (icon, color) = if toast.error { (theme::I_ERROR, ROSEHIP) } else { (theme::I_CHECK, MOSS) };
    let body = text::fit(&toast.text.replace('\n', " "), room.saturating_sub(2));
    let line = vec![span(icon, theme::mix(color, NIGHT, fade)), span(format!(" {body}"), theme::mix(FOG, NIGHT, fade))];
    let x = left_end + 3 + (room as i32 - text::line_width(&line) as i32).max(0) / 2;
    p.line(x, y, &line);
}

/// The path whole if it fits in `max` columns, else "…/" and as many of its
/// last folders as fit (at least the last one).
fn short_path(path: &str, max: usize) -> String {
    if text::width(path) <= max {
        return path.to_string();
    }
    let parts: Vec<&str> = path.split('/').filter(|p| !p.is_empty()).collect();
    let mut tail = parts.last().copied().unwrap_or(path).to_string();
    for part in parts.iter().rev().skip(1) {
        let longer = format!("{part}/{tail}");
        if text::width(&longer) + 2 > max {
            break;
        }
        tail = longer;
    }
    format!("…/{tail}")
}

/// Columns the dialog for `ask` wants: wide when it shows a diff.
pub fn ask_width(ask: &Ask) -> u16 {
    if ask.diff.is_some() { layout::DIFF_DIALOG_WIDTH } else { layout::DIALOG_WIDTH }
}

/// The most lines of explanation `ask` shows above its answer.
fn body_cap(ask: &Ask) -> usize {
    if ask.diff.is_some() { MAX_DIFF_LINES } else { MAX_BODY_LINES }
}

/// Rows a dialog needs for `ask`, padding included.
pub fn ask_height(ask: &Ask, cols: u16) -> u16 {
    let body = body_lines(ask, ask_text_width(ask, cols)).len().min(body_cap(ask)) as u16;
    let body = if body > 0 { body + 1 } else { 0 };
    let answer = match &ask.body {
        Body::Choice { choices, .. } => 2 + (choices.len() as u16).clamp(1, MAX_LIST_ROWS),
        Body::Text { .. } => 1 + u16::from(!ask.help.is_empty()),
        Body::Confirm { .. } => 1,
    };
    // padding, title, gap, body, answer, gap, footer, padding
    1 + 1 + 1 + body + answer + 1 + 1 + 1
}

/// Rows of choices the dialog in `card` shows for `ask`.
pub fn ask_list_rows(ask: &Ask, card: Rect) -> usize {
    let body = body_lines(ask, card.width.saturating_sub(6) as usize).len().min(ask_body_rows(ask, card));
    let body = if body > 0 { body + 1 } else { 0 };
    (card.height as usize).saturating_sub(8 + body)
}

fn ask_text_width(ask: &Ask, cols: u16) -> usize {
    layout::dialog(&crate::layout::Grid { cols, rows: 100, cw: 1, ch: 1 }, ask_width(ask), 1).width.saturating_sub(6) as usize
}

/// The explanation, coloured by line: a command ("$ …") in moonlight, text
/// that looks like a diff in moss and rosehip; for an overwrite, the file and
/// its diff, the old file beside the new one when `width` allows.
fn body_lines(ask: &Ask, width: usize) -> Vec<Line> {
    let Some(patch) = &ask.diff else {
        return ask
            .text
            .split('\n')
            .flat_map(|paragraph| {
                let color = match paragraph.chars().next() {
                    Some('$') => MOON,
                    Some('+') if !paragraph.starts_with("+++") => MOSS,
                    Some('-') if !paragraph.starts_with("---") => ROSEHIP,
                    _ => MIST,
                };
                text::wrap(&vec![span(paragraph, color)], width)
            })
            .collect();
    };
    let (icon, icon_color) = theme::file_icon(&ask.text);
    let mut lines = vec![vec![span(icon, icon_color), span(format!(" {}", ask.text), MOON)], Vec::new()];
    let cut = strings().diff_cut;
    lines.extend(if width >= diff::SPLIT_MIN_WIDTH { diff::split(patch, width, cut) } else { diff::inline(patch, width, cut) });
    lines
}

fn ask_body_rows(ask: &Ask, card: Rect) -> usize {
    let answer = match &ask.body {
        Body::Choice { choices, .. } => 2 + choices.len().clamp(1, MAX_LIST_ROWS as usize),
        Body::Text { .. } => 1 + usize::from(!ask.help.is_empty()),
        Body::Confirm { .. } => 1,
    };
    (card.height as usize).saturating_sub(7 + answer).min(body_cap(ask))
}

pub fn ask_text_max_scroll(ask: &Ask, card: Rect) -> usize {
    body_lines(ask, card.width.saturating_sub(6) as usize).len().saturating_sub(ask_body_rows(ask, card))
}

fn draw_ask(p: &mut Painter, app: &App, ask: Option<&Ask>, open: f32, ms: u128, drawn: &mut Drawn) {
    if open < 0.05 {
        return;
    }
    let s = strings();
    let card = app.dialog_rect;
    let dy = ((1.0 - open) * 1.4).round() as i32;
    p.clear(Rect::new(card.x, (card.y as i32 + dy).max(0) as u16, card.width, card.height));
    let x = card.x as i32 + 3;
    let right = card.right() as i32 - 3;
    let top = card.y as i32 + 1 + dy;
    let Some(ask) = ask else {
        let line = vec![span(theme::spinner(ms), MOON), span(format!(" {}", s.waiting), MIST)];
        p.line(x, top, &line);
        return;
    };
    let title = if ask.title.is_empty() { s.question } else { ask.title.as_str() };
    p.text(x, top, &text::fit(title, (right - x - 5).max(4) as usize), Style::default().fg(FOG).add_modifier(Modifier::BOLD));
    p.text(right - 3, top, "esc", st(DUSK));
    let width = (right - x).max(8) as usize;
    let mut y = top + 2;
    let body = body_lines(ask, width);
    let offset = ask.text_scroll.min(ask_text_max_scroll(ask, card));
    for line in body.iter().skip(offset).take(ask_body_rows(ask, card)) {
        p.line(x, y, line);
        y += 1;
    }
    if !body.is_empty() {
        y += 1;
    }
    match &ask.body {
        Body::Choice { query, choices, selected, scroll } => {
            if query.is_empty() {
                p.fill(x, y, 1, MOON);
                p.text(x + 1, y, s.search, st(DUSK));
            } else {
                let end = p.text(x, y, query, st(FOG));
                p.fill(end, y, 1, MOON);
            }
            y += 2;
            let visible = ask.visible();
            if visible.is_empty() {
                p.text(x, y, s.nothing_found, st(DUSK));
            }
            let rows = ((card.bottom() as i32 + dy - 3) - y).max(1) as usize;
            for (row, &index) in visible.iter().enumerate().skip(*scroll).take(rows) {
                let line_y = y + (row - scroll) as i32;
                let choice = &choices[index];
                let chosen = index == *selected;
                if chosen {
                    p.fill(card.x as i32 + 1, line_y, card.width as i32 - 2, MOON);
                }
                let (label_color, detail_color) = if chosen { (INK, INK_SOFT) } else { (FOG, DUSK) };
                let label_style = Style::default().fg(label_color).add_modifier(if chosen { Modifier::BOLD } else { Modifier::empty() });
                let cx = p.text(x, line_y, &text::fit(&choice.label, width.saturating_sub(4)), label_style);
                let room = (right - cx - 2).max(0) as usize;
                if room > 4 && !choice.detail.is_empty() {
                    p.text(cx + 2, line_y, &text::fit(&choice.detail, room), st(detail_color));
                }
                drawn.hits.push((Rect::new(card.x + 1, line_y.max(0) as u16, card.width - 2, 1), Hit::Choice(row)));
            }
        }
        Body::Text { value, secret } => {
            let shown = if *secret { "•".repeat(value.chars().count()) } else { value.clone() };
            let shown = text::fit(&shown, width.saturating_sub(2));
            let end = p.text(x, y, &shown, st(FOG));
            p.fill(end, y, 1, MOON);
            if !ask.help.is_empty() {
                p.text(x, y + 1, &text::fit(&ask.help, width), st(DUSK));
            }
        }
        Body::Confirm { .. } => {
            let mut bx = x;
            for answer in ask.body.answers() {
                let label = match answer {
                    Answer::Yes => s.yes,
                    Answer::Always => s.always,
                    Answer::No => s.no,
                };
                let label = format!("  {label}  ");
                let picked = ask.picked() == Some(answer);
                let hovered = app.hover == Some(Hit::Answer(answer));
                let bg = if picked { MOON } else if hovered { HOVER } else { theme::INK_SOFT };
                let fg = if picked { INK } else { FOG };
                let end = p.text(bx, y, &label, Style::default().fg(fg).bg(bg).add_modifier(Modifier::BOLD));
                drawn.hits.push((Rect::new(bx.max(0) as u16, y.max(0) as u16, (end - bx) as u16, 1), Hit::Answer(answer)));
                bx = end + 2;
            }
        }
    }
    let mut footer = match ask.body {
        Body::Choice { .. } => vec![span("enter", MIST), span(format!(" {}", s.choose), DUSK), span("    ↑↓", MIST), span("    esc", MIST)],
        Body::Text { .. } => vec![span("enter", MIST), span(format!(" {}", s.confirm), DUSK), span("    esc", MIST)],
        Body::Confirm { .. } => vec![span("←→", MIST), span("    enter", MIST), span(format!(" {}", s.confirm), DUSK), span("    esc", MIST)],
    };
    if ask_text_max_scroll(ask, card) > 0 {
        footer.push(span("  PgUp/PgDn", MIST));
    }
    p.line(x, card.bottom() as i32 - 2 + dy, &footer);
}

/// Rows of models the picker in `card` shows: padding, title, the role
/// switch, the search field, the gaps and the footer take ten.
pub fn models_list_rows(card: Rect) -> usize {
    card.height.saturating_sub(10) as usize
}

/// The model picker across providers: the smart/fast switch, a search
/// field, then each connected provider's models under its name.
fn draw_models(p: &mut Painter, app: &App, picker: Option<&ModelPicker>, open: f32, ms: u128, drawn: &mut Drawn) {
    if open < 0.05 {
        return;
    }
    let s = strings();
    let card = app.dialog_rect;
    let dy = ((1.0 - open) * 1.4).round() as i32;
    p.clear(Rect::new(card.x, (card.y as i32 + dy).max(0) as u16, card.width, card.height));
    let x = card.x as i32 + 3;
    let right = card.right() as i32 - 3;
    let top = card.y as i32 + 1 + dy;
    p.text(x, top, s.model_title, Style::default().fg(FOG).add_modifier(Modifier::BOLD));
    p.text(right - 3, top, "esc", st(DUSK));
    let Some(picker) = picker else {
        p.line(x, top + 2, &vec![span(theme::spinner(ms), MOON), span(format!(" {}", s.loading_models), MIST)]);
        return;
    };

    let mut tab_x = x;
    for (role, label) in [(Role::Smart, s.smart), (Role::Fast, s.fast)] {
        let picked = picker.role == role;
        let hovered = app.hover == Some(Hit::Role(role));
        let bg = if picked { MOON } else if hovered { HOVER } else { INK_SOFT };
        let label = format!("  {label}  ");
        let end = p.text(tab_x, top + 2, &label, Style::default().fg(if picked { INK } else { FOG }).bg(bg).add_modifier(Modifier::BOLD));
        drawn.hits.push((Rect::new(tab_x.max(0) as u16, (top + 2) as u16, (end - tab_x) as u16, 1), Hit::Role(role)));
        tab_x = end + 1;
    }
    p.text(tab_x + 1, top + 2, "tab", st(DUSK));

    let search_y = top + 4;
    if picker.query.is_empty() {
        p.fill(x, search_y, 1, MOON);
        p.text(x + 1, search_y, s.search, st(DUSK));
    } else {
        let end = p.text(x, search_y, &picker.query, st(FOG));
        p.fill(end, search_y, 1, MOON);
    }

    let list_top = search_y + 2;
    let rows = picker.rows();
    if rows.is_empty() {
        p.text(x, list_top, s.nothing_found, st(DUSK));
    }
    for (index, row) in rows.iter().enumerate().skip(picker.scroll).take(models_list_rows(card)) {
        let y = list_top + (index - picker.scroll) as i32;
        match *row {
            Row::Header(provider) => {
                let entry = &picker.providers[provider];
                let end = p.text(x, y, &entry.label, Style::default().fg(MIST).add_modifier(Modifier::BOLD));
                if entry.current {
                    p.text(end + 2, y, s.current, st(MOSS));
                }
            }
            Row::Model(provider, model) => {
                let entry = &picker.providers[provider];
                let id = &entry.models[model];
                let chosen = index == picker.selected;
                if chosen {
                    p.fill(card.x as i32 + 1, y, card.width as i32 - 2, MOON);
                }
                let style = Style::default().fg(if chosen { INK } else { FOG }).add_modifier(if chosen { Modifier::BOLD } else { Modifier::empty() });
                p.text(x + 2, y, &text::fit(id, (right - x - 22).max(8) as usize), style);
                let roles: Vec<&str> = [(Role::Smart, &entry.smart, s.smart), (Role::Fast, &entry.fast, s.fast)]
                    .iter()
                    .filter(|(_, used, _)| *used == id)
                    .map(|(_, _, label)| *label)
                    .collect();
                if !roles.is_empty() {
                    let tag = roles.join(", ").to_lowercase();
                    let color = if chosen { INK_SOFT } else if entry.current { MOON } else { DUSK };
                    p.text(right - text::width(&tag) as i32, y, &tag, st(color));
                }
                drawn.hits.push((Rect::new(card.x + 1, y.max(0) as u16, card.width - 2, 1), Hit::ModelRow(index)));
            }
        }
    }
    let footer = vec![
        span("enter", MIST),
        span(format!(" {}", s.choose), DUSK),
        span("    tab", MIST),
        span(format!(" {}", s.role_switch), DUSK),
        span("    ↑↓", MIST),
        span("    esc", MIST),
    ];
    p.line(x, card.bottom() as i32 - 2 + dy, &footer);
}

fn draw_popup_title(p: &mut Painter, app: &App, drawn: &mut Drawn) {
    let Some(browser) = &app.session.browser else { return };
    let rect = layout::browser_full(&app.grid, app.web_aspect.unwrap_or(BROWSER_ASPECT));
    let y = rect.y as i32 - 1;
    p.clear(Rect::new(rect.x.saturating_sub(1), y.max(0) as u16, rect.width + 2, 1));
    let cx = p.text(rect.x as i32, y, theme::I_GLOBE, st(MOON));
    p.text(cx + 1, y, &browser.title, st(FOG));
    let hovered = app.hover == Some(Hit::PopupClose);
    let close_x = rect.right() as i32 - 1;
    p.text(close_x, y, theme::I_CLOSE, st(if hovered { MOON } else { MIST }));
    drawn.hits.push((Rect::new(close_x.max(1) as u16 - 1, y.max(0) as u16, 3, 1), Hit::PopupClose));
}

fn draw_image_title(p: &mut Painter, app: &App, id: u32, drawn: &mut Drawn) {
    let Some(attachment) = app.attachment(id) else { return };
    let rect = layout::browser_full(&app.grid, attachment.aspect);
    let y = rect.y as i32 - 1;
    p.clear(Rect::new(rect.x.saturating_sub(1), y.max(0) as u16, rect.width + 2, 1));
    let cx = p.text(rect.x as i32, y, theme::I_EYE, st(MOON));
    p.text(cx + 1, y, &attachment.name, st(FOG));
    let hovered = app.hover == Some(Hit::PopupClose);
    let close_x = rect.right() as i32 - 1;
    p.text(close_x, y, theme::I_CLOSE, st(if hovered { MOON } else { MIST }));
    drawn.hits.push((Rect::new(close_x.max(1) as u16 - 1, y.max(0) as u16, 3, 1), Hit::PopupClose));
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn the_reveal_never_trails_a_fast_model_by_more_than_the_lag_limit() {
        let start = std::time::Duration::ZERO;
        assert_eq!(revealed_chars(100, start), 0);
        assert_eq!(revealed_chars(MAX_REVEAL_LAG + 500, start), 500);
        let later = std::time::Duration::from_secs(10);
        assert_eq!(revealed_chars(MAX_REVEAL_LAG + 500, later), (10.0 * REVEAL_PER_SECOND) as usize);
    }

    /// A fake provider at tens of thousands of tokens per second: every round of words, call and
    /// result lands in the same instant, so nothing but the redraw can be what trails the work.
    fn burst_of_rounds(rounds: usize) -> (App, Instant) {
        use crate::model::CoreEvent;
        let now = Instant::now();
        let mut app = App::new(layout::Grid { cols: 120, rows: 36, cw: 10, ch: 20 }, now, std::path::Path::new("/tmp"));
        for round in 0..rounds {
            let words = format!("{} END{round}", "word ".repeat(2500));
            app.session.apply(CoreEvent::Say(words), now);
            let call = format!("c{round}");
            app.session.apply(CoreEvent::Step { call: call.clone(), kind: ToolKind::Shell, label: "pytest".into() }, now);
            app.session.apply(CoreEvent::Result { call, ok: true, summary: String::new(), diff: None }, now);
        }
        (app, now)
    }

    #[test]
    fn at_fifty_thousand_tokens_a_second_every_earlier_message_is_whole_at_once() {
        let rounds = 40;
        let (app, now) = burst_of_rounds(rounds);
        let (lines, _) = transcript_lines(&app, 100, now, 0);
        let drawn: String = lines.iter().flatten().map(|(text, _)| text.as_str()).collect::<Vec<_>>().join(" ");
        for round in 0..rounds - 1 {
            assert!(drawn.contains(&format!("END{round}")), "message {round} still being typed");
        }
    }

    #[test]
    fn the_last_message_of_a_burst_trails_by_the_lag_limit_at_most() {
        let (app, now) = burst_of_rounds(1);
        let Some(Item::Agent { text, at }) = app.session.items.iter().rev().find(|item| matches!(item, Item::Agent { .. })) else { panic!("no message") };
        let total = text.chars().count();
        assert!(total - revealed_chars(total, now.saturating_duration_since(*at)) <= MAX_REVEAL_LAG);
    }

    #[test]
    fn zero_counts_are_left_out() {
        let plain = |line: Line| line.into_iter().map(|(s, _)| s).collect::<String>();
        assert_eq!(plain(counts(41, 0)), "+41");
        assert_eq!(plain(counts(0, 3)), "−3");
        assert_eq!(plain(counts(5, 1)), "+5 −1");
    }

    #[test]
    fn a_folder_that_does_not_fit_shows_its_last_part_or_nothing() {
        assert_eq!(folder_label("kernel/tests", 20).as_deref(), Some("kernel/tests"));
        assert_eq!(folder_label("kernel/tests", 6).as_deref(), Some("tests"));
        assert_eq!(folder_label("kernel/tests", 4), None);
    }

    #[test]
    fn a_long_path_keeps_its_last_folders() {
        assert_eq!(short_path("~/Kvist", 20), "~/Kvist");
        assert_eq!(short_path("/tmp/claude/scratchpad/workspace", 24), "…/scratchpad/workspace");
        assert_eq!(short_path("/a/very-long-folder-name", 8), "…/very-long-folder-name");
    }

    #[test]
    fn a_dialog_grows_with_its_choices_up_to_a_limit() {
        let ask = |n: usize| {
            let options: Vec<_> = (0..n).map(|i| json!({"value": i.to_string(), "label": i.to_string()})).collect();
            Ask::from_request(&json!({"request_id": "r", "kind": "choice", "options": options})).unwrap()
        };
        let small = ask_height(&ask(3), 160);
        assert_eq!(ask_height(&ask(4), 160), small + 1);
        assert_eq!(ask_height(&ask(40), 160), ask_height(&ask(80), 160));
        let card = layout::dialog(&layout::Grid { cols: 160, rows: 60, cw: 1, ch: 1 }, layout::DIALOG_WIDTH, small);
        assert_eq!(ask_list_rows(&ask(3), card), 3);
    }

    #[test]
    fn the_caret_follows_wrapped_input() {
        let (lines, caret) = input_lines("abcdef", 6, 4);
        assert_eq!(lines, vec!["abcd", "ef"]);
        assert_eq!(caret, (1, 2));
    }

    #[test]
    fn a_full_line_puts_the_caret_on_the_next_one() {
        let (lines, caret) = input_lines("abcd", 4, 4);
        assert_eq!(lines, vec!["abcd", ""]);
        assert_eq!(caret, (1, 0));
    }

    #[test]
    fn up_and_down_keep_the_column_and_stop_at_the_ends() {
        let text = "abcdef\nxy\nklmnop";
        // From the middle of the last line (column 3) up to the short line, then up again.
        assert_eq!(move_row(text, 13, 20, true), Some(9));
        assert_eq!(move_row(text, 9, 20, true), Some(2));
        assert_eq!(move_row(text, 2, 20, true), None);
        assert_eq!(move_row(text, 2, 20, false), Some(9));
        assert_eq!(move_row(text, 9, 20, false), Some(12));
        assert_eq!(move_row(text, 12, 20, false), None);
    }

    #[test]
    fn up_and_down_follow_wrapped_rows_too() {
        assert_eq!(move_row("abcdefgh", 6, 4, true), Some(2));
        assert_eq!(move_row("abcdefgh", 2, 4, false), Some(6));
    }

    #[test]
    fn newlines_start_new_lines() {
        let (lines, caret) = input_lines("ab\ncd", 1, 10);
        assert_eq!(lines, vec!["ab", "cd"]);
        assert_eq!(caret, (0, 1));
    }
}
