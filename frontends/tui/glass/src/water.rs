// Selecting text is pouring water on the glass. The left button lets a drop
// fall where it is pressed; dragging pours: the water flows along the text
// after the pointer (it lags behind a fast one and catches up), and the pool
// has a surface — a rounded edge that lights on one side and darkens on the
// other, a ripple that spreads from the drop and slow caustics inside.
//
// `Pour` is the state (what is selected, how far the water has flowed) and
// `render` turns its `Shape` into a translucent picture. Kitty draws that
// picture above the cell backgrounds and below the text, so the words stay
// sharp under the water.

use std::collections::hash_map::DefaultHasher;
use std::hash::{Hash, Hasher};
use std::time::Instant;

use image::{Rgba, RgbaImage};
use ratatui::layout::Rect;

/// Water poured per second even when the pointer is far ahead, in cells.
const MIN_FLOW: f32 = 70.0;
/// How fast the flow closes the gap to the pointer, per second.
const CHASE: f32 = 9.0;
/// How long a ripple lives.
const RIPPLE: f32 = 1.4;
/// How long the pool takes to drain away.
const DRAIN: f32 = 0.55;
/// A press without a drag leaves only the drop's ripple.
const DROP_ONLY: f32 = 0.9;
/// Pixels of the render per field cell; the terminal scales the picture up.
const STEP: u32 = 3;

/// A cell of the text: the line's index among all lines, and the column in
/// display cells from where the line's text starts.
#[derive(Clone, Copy, Debug, PartialEq, Eq, PartialOrd, Ord)]
pub struct Pos {
    pub line: usize,
    pub col: usize,
}

/// Where the lines are on screen: the column the text starts at, the row of
/// the first shown line and its index, and how many lines are shown.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct TextView {
    pub x: i32,
    pub top: i32,
    pub first: usize,
    pub rows: usize,
}

impl TextView {
    /// The text cell under a screen cell, clamped into the shown lines.
    /// `widths` are the display widths of all lines.
    pub fn locate(&self, column: u16, row: u16, widths: &[usize]) -> Option<Pos> {
        if self.rows == 0 || widths.is_empty() {
            return None;
        }
        let shown = (row as i32 - self.top).clamp(0, self.rows as i32 - 1) as usize;
        let line = (self.first + shown).min(widths.len() - 1);
        let last = widths[line].saturating_sub(1);
        let col = ((column as i32 - self.x).max(0) as usize).min(last);
        Some(Pos { line, col })
    }

    pub fn holds(&self, column: u16, row: u16) -> bool {
        let (column, row) = (column as i32, row as i32);
        row >= self.top && row < self.top + self.rows as i32 && column >= self.x
    }
}

#[derive(Clone, Copy, Debug, PartialEq)]
enum Stage {
    /// The button is down.
    Pouring,
    Settled,
    Draining(Instant),
}

pub struct Pour {
    anchor: Pos,
    head: Pos,
    /// A drag, or a word or line chosen by a repeated click, made it a selection.
    chosen: bool,
    dropped: Instant,
    stepped: Instant,
    stage: Stage,
    /// Cells of water poured so far, counted from the anchor.
    front: f32,
    /// The water has not yet reached the pointer.
    flowing: bool,
}

/// Water over one line: `a..b` in screen columns, fractional where the flow
/// has stopped inside a cell.
#[derive(Clone, Debug, PartialEq)]
pub struct Span {
    pub row: i32,
    pub a: f32,
    pub b: f32,
}

/// Everything `render` needs, in screen cells.
#[derive(Clone, Debug, PartialEq)]
pub struct Shape {
    pub spans: Vec<Span>,
    /// Where the drop fell: column, row (centre of the cell).
    pub drop: (f32, f32),
    /// Seconds since the drop, while its ripple lives.
    pub ripple: Option<f32>,
    /// 1 = full water, 0 = gone.
    pub level: f32,
}

impl Pour {
    pub fn begin(at: Pos, now: Instant) -> Self {
        Pour { anchor: at, head: at, chosen: false, dropped: now, stepped: now, stage: Stage::Pouring, front: 0.0, flowing: false }
    }

    /// A word or a line picked with a repeated click: poured at once, not by a drag.
    pub fn choose(from: Pos, to: Pos, now: Instant) -> Self {
        let mut pour = Pour::begin(from, now);
        pour.head = to;
        pour.chosen = true;
        pour.stage = Stage::Settled;
        pour
    }

    pub fn drag(&mut self, to: Pos) {
        if to != self.head {
            self.head = to;
            self.chosen |= to != self.anchor;
        }
    }

    pub fn release(&mut self) {
        if self.stage == Stage::Pouring {
            self.stage = Stage::Settled;
        }
    }

    pub fn drain(&mut self, now: Instant) {
        if !matches!(self.stage, Stage::Draining(_)) {
            self.stage = Stage::Draining(now);
        }
    }

    /// Seconds since the drop fell.
    pub fn age(&self, now: Instant) -> f32 {
        now.saturating_duration_since(self.dropped).as_secs_f32()
    }

    pub fn pouring(&self) -> bool {
        self.stage == Stage::Pouring
    }

    pub fn is_empty(&self) -> bool {
        !self.chosen
    }

    /// Nothing left to show: drained, or a drop that only rippled.
    pub fn gone(&self, now: Instant) -> bool {
        match self.stage {
            Stage::Draining(since) => now.saturating_duration_since(since).as_secs_f32() > DRAIN,
            Stage::Settled if !self.chosen => now.saturating_duration_since(self.dropped).as_secs_f32() > DROP_ONLY,
            _ => false,
        }
    }

    /// The pool changes with time while the water flows, ripples or drains.
    pub fn moving(&self, now: Instant) -> bool {
        self.stage != Stage::Settled
            || self.flowing
            || now.saturating_duration_since(self.dropped).as_secs_f32() < RIPPLE
    }

    /// Selected cells in reading order, `(first, last)` both inclusive.
    pub fn range(&self) -> Option<(Pos, Pos)> {
        self.chosen.then(|| (self.anchor.min(self.head), self.anchor.max(self.head)))
    }

    fn forward(&self) -> bool {
        self.head >= self.anchor
    }

    /// Per line of the selection: `(line, from, to)` as half-open cells. A
    /// blank line inside is bridged as wide as the narrower line beside it,
    /// so the pool stays one sheet instead of dripping between paragraphs.
    fn runs(&self, widths: &[usize]) -> Vec<(usize, usize, usize)> {
        let Some((first, last)) = self.range() else { return Vec::new() };
        (first.line..=last.line.min(widths.len().saturating_sub(1)))
            .map(|line| {
                let inside = line > first.line && line < last.line;
                let width = match widths[line] {
                    0 if inside => widths[line - 1].min(widths.get(line + 1).copied().unwrap_or(0)),
                    width => width,
                };
                let from = if line == first.line { first.col } else { 0 };
                let to = if line == last.line { (last.col + 1).min(width.max(1)) } else { width.max(1) };
                (line, from.min(to), to)
            })
            .collect()
    }

    /// Lets the water flow towards the pointer for the time since the last call.
    pub fn advance(&mut self, now: Instant, widths: &[usize]) {
        let dt = now.saturating_duration_since(self.stepped).as_secs_f32().min(0.1);
        self.stepped = now;
        let total: usize = self.runs(widths).iter().map(|(_, a, b)| b - a).sum();
        let total = total as f32;
        match self.stage {
            Stage::Draining(_) => self.front = (self.front - (total + 40.0) * dt * 2.2).max(0.0),
            _ if self.front > total => self.front = total,
            _ => {
                let gap = total - self.front;
                self.front += (gap * (1.0 - (-dt * CHASE).exp()) + MIN_FLOW * dt).min(gap);
            }
        }
        self.flowing = self.front < total - 0.01;
    }

    /// The water as drawn now: the lines it covers, in screen cells.
    pub fn shape(&self, view: &TextView, widths: &[usize], now: Instant) -> Shape {
        let forward = self.forward();
        let mut runs = self.runs(widths);
        if !forward {
            runs.reverse();
        }
        let mut left = self.front;
        let mut spans = Vec::new();
        for (line, a, b) in runs {
            if left <= 0.0 {
                break;
            }
            let take = left.min((b - a) as f32);
            left -= take;
            let (from, to) = if forward { (a as f32, a as f32 + take) } else { (b as f32 - take, b as f32) };
            if line < view.first || line >= view.first + view.rows {
                continue;
            }
            let row = view.top + (line - view.first) as i32;
            spans.push(Span { row, a: view.x as f32 + from, b: view.x as f32 + to });
        }
        let drop_row = self.anchor.line as i32 - view.first as i32 + view.top;
        let age = now.saturating_duration_since(self.dropped).as_secs_f32();
        let level = match self.stage {
            Stage::Draining(since) => 1.0 - now.saturating_duration_since(since).as_secs_f32() / DRAIN,
            Stage::Settled if !self.chosen => 1.0 - age / DROP_ONLY,
            _ => 1.0,
        };
        Shape {
            spans,
            drop: (view.x as f32 + self.anchor.col as f32 + 0.5, drop_row as f32 + 0.5),
            ripple: (age < RIPPLE).then_some(age),
            level: level.clamp(0.0, 1.0),
        }
    }
}

/// The cell range of the word around `at`, or the line when `at` is on a blank.
pub fn word_around(line: &str, at: usize) -> (usize, usize) {
    let mut cells = Vec::new();
    for c in line.chars() {
        let w = unicode_width::UnicodeWidthChar::width(c).unwrap_or(0);
        for _ in 0..w {
            cells.push(c);
        }
    }
    let word = |c: char| c.is_alphanumeric() || c == '_';
    let Some(&here) = cells.get(at) else { return (at, at) };
    if !word(here) {
        return (at, at);
    }
    let mut from = at;
    while from > 0 && word(cells[from - 1]) {
        from -= 1;
    }
    let mut to = at;
    while to + 1 < cells.len() && word(cells[to + 1]) {
        to += 1;
    }
    (from, to)
}

/// The text of cells `from..to` of a line (half-open), without the glyphs
/// the interface draws as decoration (icons, the quote bar).
pub fn slice(line: &str, from: usize, to: usize) -> String {
    let mut out = String::new();
    let mut col = 0;
    for c in line.chars() {
        let w = unicode_width::UnicodeWidthChar::width(c).unwrap_or(0);
        if col >= from && col < to && !decoration(c) {
            out.push(c);
        }
        col += w;
    }
    out
}

fn decoration(c: char) -> bool {
    matches!(c, '\u{e000}'..='\u{f8ff}' | '\u{f0000}'..='\u{ffffd}' | '▎')
}

/// The selected text of `lines`, one string per line.
pub fn selected_text(pour: &Pour, lines: &[String]) -> String {
    let Some((first, last)) = pour.range() else { return String::new() };
    let mut rows = Vec::new();
    for (line, text) in lines.iter().enumerate().take(last.line + 1).skip(first.line) {
        let from = if line == first.line { first.col } else { 0 };
        let to = if line == last.line { last.col + 1 } else { usize::MAX };
        rows.push(slice(text, from, to).trim_end().to_string());
    }
    rows.join("\n").trim().to_string()
}

/// Cells the picture covers: the pool and a margin for its soft edge.
pub fn bounds(shape: &Shape, cols: u16, rows: u16) -> Option<Rect> {
    let mut x0 = shape.drop.0 - 3.0;
    let mut x1 = shape.drop.0 + 3.0;
    let mut y0 = shape.drop.1 - 2.0;
    let mut y1 = shape.drop.1 + 2.0;
    for span in &shape.spans {
        x0 = x0.min(span.a);
        x1 = x1.max(span.b);
        y0 = y0.min(span.row as f32);
        y1 = y1.max(span.row as f32 + 1.0);
    }
    let (x0, y0) = ((x0.floor() as i32 - 2).max(0), (y0.floor() as i32 - 1).max(0));
    let (x1, y1) = ((x1.ceil() as i32 + 2).min(cols as i32), (y1.ceil() as i32 + 1).min(rows as i32));
    (x1 > x0 && y1 > y0).then(|| Rect::new(x0 as u16, y0 as u16, (x1 - x0) as u16, (y1 - y0) as u16))
}

/// Changes whenever `render` would draw something different.
pub fn signature(shape: &Shape, clock: f32) -> u64 {
    let mut hasher = DefaultHasher::new();
    for span in &shape.spans {
        (span.row, (span.a * 8.0) as i32, (span.b * 8.0) as i32).hash(&mut hasher);
    }
    ((shape.drop.0 * 2.0) as i32, (shape.drop.1 * 2.0) as i32).hash(&mut hasher);
    shape.ripple.map(|age| (age * 30.0) as i32).hash(&mut hasher);
    ((shape.level * 40.0) as i32).hash(&mut hasher);
    ((clock * 12.0) as i64).hash(&mut hasher);
    hasher.finish()
}

fn smoothstep(a: f32, b: f32, x: f32) -> f32 {
    let t = ((x - a) / (b - a)).clamp(0.0, 1.0);
    t * t * (3.0 - 2.0 * t)
}

/// One pass of a box blur along rows (`stride` 1) or columns (`stride` = width).
fn box_blur(field: &[f32], w: usize, h: usize, radius: usize, horizontal: bool) -> Vec<f32> {
    let mut out = vec![0.0; field.len()];
    let (lines, length, step, line_step) = if horizontal { (h, w, 1, w) } else { (w, h, w, 1) };
    let norm = 1.0 / (2 * radius + 1) as f32;
    for line in 0..lines {
        let base = line * line_step;
        let at = |i: isize| -> f32 {
            if i < 0 || i >= length as isize { 0.0 } else { field[base + i as usize * step] }
        };
        let mut sum: f32 = (-(radius as isize)..=radius as isize).map(at).sum();
        for i in 0..length {
            out[base + i * step] = sum * norm;
            sum += at(i as isize + radius as isize + 1) - at(i as isize - radius as isize);
        }
    }
    out
}

fn blur(field: Vec<f32>, w: usize, h: usize, radius: usize, passes: usize) -> Vec<f32> {
    let radius = radius.max(1);
    (0..passes).fold(field, |f, _| {
        let f = box_blur(&f, w, h, radius, true);
        box_blur(&f, w, h, radius, false)
    })
}

/// Draws the pool as a translucent picture for the cells in `area` (from
/// `bounds`), one pixel per `STEP` of the real ones. `clock` is seconds, for
/// the caustics.
pub fn render(shape: &Shape, area: Rect, cw: u32, ch: u32, clock: f32) -> RgbaImage {
    let w = ((area.width as u32 * cw) / STEP).max(2) as usize;
    let h = ((area.height as u32 * ch) / STEP).max(2) as usize;
    let (cwf, chf) = (cw as f32 / STEP as f32, ch as f32 / STEP as f32);
    // Field coordinates of a screen cell position.
    let fx = |col: f32| (col - area.x as f32) * cwf;
    let fy = |row: f32| (row - area.y as f32) * chf;

    let mut mass = vec![0.0f32; w * h];
    let mut fill = |x0: f32, y0: f32, x1: f32, y1: f32| {
        let (x0, x1) = (x0.max(0.0) as usize, (x1.max(0.0) as usize).min(w));
        let (y0, y1) = (y0.max(0.0) as usize, (y1.max(0.0) as usize).min(h));
        for y in y0..y1 {
            mass[y * w + x0.min(x1)..y * w + x1].fill(1.0);
        }
    };
    // Water wraps the text a little wider than the letters, and a line's
    // water is a little shorter than the line so neighbours join by the blur.
    let pad_x = 0.42 * cwf;
    let pad_y = -0.04 * chf;
    for span in &shape.spans {
        fill(fx(span.a) - pad_x, fy(span.row as f32) + pad_y, fx(span.b) + pad_x, fy(span.row as f32 + 1.0) - pad_y);
    }
    let (dx, dy) = (fx(shape.drop.0), fy(shape.drop.1));
    // A drop is about as wide as a line is high, landing in a few frames.
    let drop_r = 0.62 * chf * (1.0 - (-(shape.ripple.unwrap_or(RIPPLE)) * 12.0).exp());
    for y in (dy - drop_r).max(0.0) as usize..((dy + drop_r) as usize).min(h) {
        for x in (dx - drop_r).max(0.0) as usize..((dx + drop_r) as usize).min(w) {
            if (x as f32 - dx).powi(2) + (y as f32 - dy).powi(2) <= drop_r * drop_r {
                mass[y * w + x] = 1.0;
            }
        }
    }

    // The soft body of the pool: its edge is where this crosses one half.
    // Surface tension: lines of water merge and corners round off by about
    // a third of a line.
    let body = blur(mass, w, h, (0.4 * chf).round() as usize, 2);
    // A broader copy is the surface the light bends on.
    let mut surface = blur(body.clone(), w, h, (0.3 * chf).round() as usize, 2);
    if let Some(age) = shape.ripple {
        let speed = 22.0 * cwf;
        let front = speed * age;
        let decay = (1.0 - age / RIPPLE).max(0.0).powi(2);
        for y in 0..h {
            for x in 0..w {
                if body[y * w + x] < 0.2 {
                    continue;
                }
                let d = ((x as f32 - dx).powi(2) + (y as f32 - dy).powi(2)).sqrt();
                let ring = d - front;
                let envelope = (-(ring / (2.2 * cwf)).powi(2)).exp();
                surface[y * w + x] += 0.35 * decay * envelope * (ring / (0.7 * cwf)).cos();
            }
        }
    }

    let light = normalise((-0.55, -0.7, 0.55));
    let half = normalise((light.0, light.1, light.2 + 1.0));
    let mut image = RgbaImage::new(w as u32, h as u32);
    for y in 1..h - 1 {
        for x in 1..w - 1 {
            let i = y * w + x;
            let cover = smoothstep(0.30, 0.50, body[i]);
            if cover <= 0.0 {
                continue;
            }
            let gx = (surface[i + 1] - surface[i - 1]) * 0.5;
            let gy = (surface[i + w] - surface[i - w]) * 0.5;
            let normal = normalise((-gx * 34.0, -gy * 34.0, 1.0));
            let lit = dot(normal, light) - light.2;
            let spec = dot(normal, half).max(0.0).powi(36);
            // Nearer the rim the surface bends: the light shows there.
            let rim = 1.0 - smoothstep(0.55, 0.98, body[i]);

            let depth = smoothstep(0.5, 1.0, body[i]);
            let mut c = Layer { rgb: mix3((120.0, 190.0, 235.0), (46.0, 110.0, 200.0), depth), a: 0.10 + 0.09 * depth };
            c = c.under(Layer { rgb: (10.0, 24.0, 64.0), a: ((-lit).max(0.0) * 2.0).min(0.5) * rim });
            let wave = 0.5 + 0.5 * ((x as f32 * 0.09 + (y as f32 * 0.06 + clock * 1.1).sin() * 2.0 + clock * 0.8).sin());
            let wave2 = 0.5 + 0.5 * ((y as f32 * 0.11 - clock * 0.9 + (x as f32 * 0.05 + clock).sin() * 2.0).sin());
            let caustic = (wave * wave2).powi(3) * 0.1;
            c = c.over(Layer { rgb: (214.0, 236.0, 255.0), a: caustic + (lit.max(0.0) * 2.4).min(0.6) * rim + spec * 0.9 });
            let alpha = (c.a * cover * shape.level).clamp(0.0, 1.0);
            image.put_pixel(x as u32, y as u32, Rgba([c.rgb.0 as u8, c.rgb.1 as u8, c.rgb.2 as u8, (alpha * 255.0) as u8]));
        }
    }
    image
}

#[derive(Clone, Copy)]
struct Layer {
    rgb: (f32, f32, f32),
    a: f32,
}

impl Layer {
    /// `top` painted over this layer.
    fn over(self, top: Layer) -> Layer {
        let a = top.a + self.a * (1.0 - top.a);
        if a <= 0.0 {
            return Layer { rgb: self.rgb, a: 0.0 };
        }
        let mix = |t: f32, b: f32| (t * top.a + b * self.a * (1.0 - top.a)) / a;
        Layer { rgb: (mix(top.rgb.0, self.rgb.0), mix(top.rgb.1, self.rgb.1), mix(top.rgb.2, self.rgb.2)), a }
    }

    /// `bottom` laid under this layer.
    fn under(self, bottom: Layer) -> Layer {
        bottom.over(self)
    }
}

fn mix3(a: (f32, f32, f32), b: (f32, f32, f32), t: f32) -> (f32, f32, f32) {
    (a.0 + (b.0 - a.0) * t, a.1 + (b.1 - a.1) * t, a.2 + (b.2 - a.2) * t)
}

fn dot(a: (f32, f32, f32), b: (f32, f32, f32)) -> f32 {
    a.0 * b.0 + a.1 * b.1 + a.2 * b.2
}

fn normalise(v: (f32, f32, f32)) -> (f32, f32, f32) {
    let length = dot(v, v).sqrt().max(1e-6);
    (v.0 / length, v.1 / length, v.2 / length)
}

#[cfg(test)]
mod tests {
    use std::time::Duration;

    use super::*;

    fn pos(line: usize, col: usize) -> Pos {
        Pos { line, col }
    }

    fn pour(from: Pos, to: Pos) -> Pour {
        let mut pour = Pour::begin(from, Instant::now());
        pour.drag(to);
        pour
    }

    #[test]
    fn a_press_without_a_drag_selects_nothing() {
        let pour = Pour::begin(pos(2, 5), Instant::now());
        assert!(pour.is_empty());
        assert_eq!(selected_text(&pour, &vec!["x".to_string(); 4]), "");
    }

    #[test]
    fn a_drag_selects_both_end_cells() {
        let lines: Vec<String> = vec!["hello world".into()];
        assert_eq!(selected_text(&pour(pos(0, 2), pos(0, 6)), &lines), "llo w");
        assert_eq!(selected_text(&pour(pos(0, 6), pos(0, 2)), &lines), "llo w");
    }

    #[test]
    fn a_selection_over_lines_joins_them_with_newlines() {
        let lines: Vec<String> = vec!["first line".into(), "".into(), "third".into()];
        assert_eq!(selected_text(&pour(pos(0, 6), pos(2, 2)), &lines), "line\n\nthi");
    }

    #[test]
    fn decoration_glyphs_are_not_copied() {
        let lines: Vec<String> = vec!["▎ \u{ea70} read it".into()];
        assert_eq!(selected_text(&pour(pos(0, 0), pos(0, 20)), &lines), "read it");
    }

    #[test]
    fn a_word_is_found_around_a_cell() {
        assert_eq!(word_around("let foo_bar = 1;", 6), (4, 10));
        assert_eq!(word_around("let foo_bar = 1;", 3), (3, 3));
        assert_eq!(word_around("привет мир", 2), (0, 5));
    }

    #[test]
    fn the_water_flows_to_the_pointer_and_stops_there() {
        let widths = [20, 20];
        let start = Instant::now();
        let mut pour = Pour::begin(pos(0, 4), start);
        pour.drag(pos(1, 3));
        let mut now = start;
        pour.advance(now + Duration::from_millis(16), &widths);
        assert!(pour.flowing);
        for _ in 0..60 {
            now += Duration::from_millis(16);
            pour.advance(now, &widths);
        }
        assert!(!pour.flowing);
        let view = TextView { x: 3, top: 5, first: 0, rows: 2 };
        let shape = pour.shape(&view, &widths, now);
        assert_eq!(shape.spans.len(), 2);
        assert_eq!((shape.spans[0].a, shape.spans[0].b), (7.0, 23.0));
        assert_eq!((shape.spans[1].a, shape.spans[1].b), (3.0, 7.0));
    }

    #[test]
    fn a_backward_drag_pours_from_the_anchor() {
        let widths = [10, 10];
        let start = Instant::now();
        let mut pour = Pour::begin(pos(1, 2), start);
        pour.drag(pos(0, 6));
        pour.advance(start + Duration::from_millis(50), &widths);
        let view = TextView { x: 0, top: 0, first: 0, rows: 2 };
        let shape = pour.shape(&view, &widths, start + Duration::from_millis(50));
        // The first span is the anchor's line, filled from its right end.
        assert_eq!(shape.spans[0].row, 1);
        assert_eq!(shape.spans[0].b, 3.0);
    }

    #[test]
    fn the_pointer_is_located_in_the_shown_lines() {
        let view = TextView { x: 4, top: 2, first: 10, rows: 3 };
        let widths = vec![5; 20];
        assert_eq!(view.locate(5, 3, &widths), Some(pos(11, 1)));
        assert_eq!(view.locate(0, 0, &widths), Some(pos(10, 0)));
        assert_eq!(view.locate(90, 50, &widths), Some(pos(12, 4)));
    }

    #[test]
    fn a_drop_without_a_drag_is_gone_after_its_ripple() {
        let start = Instant::now();
        let mut pour = Pour::begin(pos(0, 0), start);
        pour.release();
        assert!(!pour.gone(start + Duration::from_millis(300)));
        assert!(pour.gone(start + Duration::from_secs(2)));
    }

    #[test]
    fn rendering_leaves_the_outside_clear_and_the_inside_wet() {
        let shape = Shape {
            spans: vec![Span { row: 3, a: 4.0, b: 20.0 }],
            drop: (4.5, 3.5),
            ripple: None,
            level: 1.0,
        };
        let area = bounds(&shape, 80, 24).unwrap();
        let image = render(&shape, area, 10, 20, 0.0);
        let at = |col: f32, row: f32| {
            let x = ((col - area.x as f32) * 10.0 / STEP as f32) as u32;
            let y = ((row - area.y as f32) * 20.0 / STEP as f32) as u32;
            image.get_pixel(x, y)[3]
        };
        assert!(at(12.0, 3.5) > 20, "inside the water");
        assert_eq!(at(area.x as f32 + 0.2, area.y as f32 + 0.2), 0, "far outside");
    }

    /// `WATER_PREVIEW=/path/sheet.png cargo test water_preview -- --ignored`
    /// lays the pool over a night-blue gradient with a line of letters, at
    /// several moments of its life, to be looked at.
    #[test]
    #[ignore = "writes a picture for a person to look at"]
    fn water_preview() {
        let Ok(path) = std::env::var("WATER_PREVIEW") else { return };
        let (cw, ch) = (10u32, 20u32);
        let spans = vec![
            Span { row: 2, a: 9.0, b: 41.0 },
            Span { row: 3, a: 3.0, b: 44.0 },
            Span { row: 4, a: 3.0, b: 27.0 },
        ];
        let moments = [(Some(0.12), 0.2), (Some(0.5), 0.6), (None, 1.7)];
        let mut sheet = RgbaImage::new(48 * cw, (7 * ch) * moments.len() as u32);
        let zoom = 2;
        for (index, (ripple, clock)) in moments.iter().enumerate() {
            let shape = Shape { spans: spans.clone(), drop: (9.5, 2.5), ripple: *ripple, level: 1.0 };
            let area = bounds(&shape, 48, 7).unwrap();
            let pool = render(&shape, area, cw, ch, *clock);
            let top = index as u32 * 7 * ch;
            for y in 0..7 * ch {
                for x in 0..48 * cw {
                    let t = x as f32 / (48 * cw) as f32;
                    sheet.put_pixel(x, top + y, Rgba([(18.0 + 22.0 * t) as u8, (26.0 + 30.0 * t) as u8, (44.0 + 40.0 * t) as u8, 255]));
                }
            }
            let (ox, oy) = (area.x as u32 * cw, top + area.y as u32 * ch);
            let (pw, ph) = (area.width as u32 * cw, area.height as u32 * ch);
            for y in 0..ph {
                for x in 0..pw {
                    let src = pool.get_pixel((x / STEP).min(pool.width() - 1), (y / STEP).min(pool.height() - 1));
                    let under = sheet.get_pixel_mut(ox + x, oy + y);
                    let a = src[3] as f32 / 255.0;
                    for c in 0..3 {
                        under[c] = (src[c] as f32 * a + under[c] as f32 * (1.0 - a)) as u8;
                    }
                }
            }
            // Stand-in letters: bars of the text colour where words would be.
            for span in &spans {
                for column in (span.a as u32..span.b as u32).filter(|c| c % 6 != 5) {
                    for y in 5..14 {
                        for x in 2..7 {
                            sheet.put_pixel(column * cw + x, top + span.row as u32 * ch + y, Rgba([240, 244, 250, 255]));
                        }
                    }
                }
            }
        }
        let big = image::imageops::resize(&sheet, sheet.width() * zoom, sheet.height() * zoom, image::imageops::FilterType::Nearest);
        big.save(path).unwrap();
    }

    #[test]
    #[ignore = "prints how long a screen-sized pool takes to draw"]
    fn water_render_cost() {
        let spans: Vec<Span> = (1..28).map(|row| Span { row, a: 4.0, b: 100.0 }).collect();
        let shape = Shape { spans, drop: (4.5, 1.5), ripple: Some(0.4), level: 1.0 };
        let area = bounds(&shape, 120, 36).unwrap();
        let started = Instant::now();
        for _ in 0..10 {
            render(&shape, area, 12, 24, 1.0);
        }
        eprintln!("{} cells, {:?} per frame", area.width as u32 * area.height as u32, started.elapsed() / 10);
    }

    #[test]
    fn a_blank_line_inside_is_bridged_by_the_narrower_neighbour() {
        let widths = [30, 0, 12];
        let mut pour = Pour::begin(pos(0, 0), Instant::now());
        pour.drag(pos(2, 11));
        let runs = pour.runs(&widths);
        assert_eq!(runs, vec![(0, 0, 30), (1, 0, 12), (2, 0, 12)]);
    }
}
