// Text in cells: styled spans, word wrapping by display width, a small
// Markdown subset (**bold** and `code`), and a painter that clips to a
// rectangle and accepts positions off the left edge (for sliding panels).

use ratatui::buffer::Buffer;
use ratatui::layout::Rect;
use ratatui::style::{Color, Style};
use unicode_width::UnicodeWidthChar;

use crate::theme;

pub type Line = Vec<(String, Style)>;

pub fn width(s: &str) -> usize {
    s.chars().map(|c| c.width().unwrap_or(0)).sum()
}

pub fn line_width(line: &Line) -> usize {
    line.iter().map(|(s, _)| width(s)).sum()
}

/// Cuts `s` to at most `max` columns, ending in "…" when it was cut.
pub fn fit(s: &str, max: usize) -> String {
    if width(s) <= max {
        return s.to_string();
    }
    let mut out = String::new();
    let mut used = 0;
    for c in s.chars() {
        let w = c.width().unwrap_or(0);
        if used + w + 1 > max {
            break;
        }
        out.push(c);
        used += w;
    }
    out.push('…');
    out
}

/// The end of `s` that fits in `max` columns, with `…` where the start was cut: a line being
/// typed keeps its newest characters in view.
pub fn fit_end(s: &str, max: usize) -> String {
    if width(s) <= max {
        return s.to_string();
    }
    let mut kept: Vec<char> = Vec::new();
    let mut used = 1;
    for c in s.chars().rev() {
        let w = c.width().unwrap_or(0);
        if used + w > max {
            break;
        }
        kept.push(c);
        used += w;
    }
    kept.push('…');
    kept.reverse();
    kept.into_iter().collect()
}

/// Greedy word wrap of styled spans into lines of at most `max` columns.
pub fn wrap(spans: &Line, max: usize) -> Vec<Line> {
    let max = max.max(4);
    let mut lines: Vec<Line> = vec![Vec::new()];
    let mut used = 0;
    for (word, style) in words(spans) {
        let w = width(&word);
        let is_space = word.chars().all(char::is_whitespace);
        if is_space {
            if used > 0 && used + w <= max {
                push(lines.last_mut().unwrap(), &word, style);
                used += w;
            }
            continue;
        }
        if used + w > max && used > 0 {
            trim_end(lines.last_mut().unwrap());
            lines.push(Vec::new());
            used = 0;
        }
        if w <= max {
            push(lines.last_mut().unwrap(), &word, style);
            used += w;
            continue;
        }
        for c in word.chars() {
            let cw = c.width().unwrap_or(0);
            if used + cw > max {
                lines.push(Vec::new());
                used = 0;
            }
            push(lines.last_mut().unwrap(), &c.to_string(), style);
            used += cw;
        }
    }
    lines
}

fn words(spans: &Line) -> Vec<(String, Style)> {
    let mut out = Vec::new();
    for (text, style) in spans {
        let mut current = String::new();
        let mut in_space = None;
        for c in text.chars() {
            let space = c == ' ';
            if in_space.is_some_and(|s| s != space) {
                out.push((std::mem::take(&mut current), *style));
            }
            in_space = Some(space);
            current.push(c);
        }
        if !current.is_empty() {
            out.push((current, *style));
        }
    }
    out
}

fn push(line: &mut Line, text: &str, style: Style) {
    if let Some((last, last_style)) = line.last_mut()
        && *last_style == style
    {
        last.push_str(text);
        return;
    }
    line.push((text.to_string(), style));
}

fn trim_end(line: &mut Line) {
    while let Some((text, _)) = line.last_mut() {
        let trimmed = text.trim_end().len();
        if trimmed == 0 {
            line.pop();
            continue;
        }
        text.truncate(trimmed);
        break;
    }
}

/// Writes into a buffer inside `clip`; positions may be negative.
pub struct Painter<'a> {
    pub buf: &'a mut Buffer,
    pub clip: Rect,
}

impl Painter<'_> {
    pub fn text(&mut self, x: i32, y: i32, s: &str, style: Style) -> i32 {
        let mut x = x;
        for c in s.chars() {
            let w = c.width().unwrap_or(0) as i32;
            if w == 0 {
                continue;
            }
            if self.contains(x, y) && self.contains(x + w - 1, y) {
                let mut tmp = [0u8; 4];
                if let Some(cell) = self.buf.cell_mut((x as u16, y as u16)) {
                    cell.set_symbol(c.encode_utf8(&mut tmp)).set_fg(style.fg.unwrap_or(theme::FOG));
                    cell.modifier = style.add_modifier;
                    if let Some(bg) = style.bg {
                        cell.set_bg(bg);
                    }
                }
                if w == 2
                    && let Some(cell) = self.buf.cell_mut(((x + 1) as u16, y as u16))
                {
                    cell.set_symbol("");
                }
            }
            x += w;
        }
        x
    }

    pub fn line(&mut self, x: i32, y: i32, line: &Line) -> i32 {
        line.iter().fold(x, |x, (s, style)| self.text(x, y, s, *style))
    }

    /// Right-aligns `line` so it ends at column `right` (exclusive).
    pub fn line_right(&mut self, right: i32, y: i32, line: &Line) {
        let w = line_width(line) as i32;
        self.line(right - w, y, line);
    }

    /// Empties a rectangle, so a card's picture shows through it and nothing
    /// drawn earlier peeks out from under a dialog.
    pub fn clear(&mut self, rect: Rect) {
        for y in rect.top()..rect.bottom() {
            for x in rect.left()..rect.right() {
                if self.contains(x as i32, y as i32)
                    && let Some(cell) = self.buf.cell_mut((x, y))
                {
                    cell.reset();
                }
            }
        }
    }

    pub fn fill(&mut self, x: i32, y: i32, w: i32, bg: Color) {
        for cx in x..x + w {
            if self.contains(cx, y)
                && let Some(cell) = self.buf.cell_mut((cx as u16, y as u16))
            {
                cell.set_bg(bg);
            }
        }
    }

    fn contains(&self, x: i32, y: i32) -> bool {
        x >= self.clip.x as i32
            && y >= self.clip.y as i32
            && x < (self.clip.x + self.clip.width) as i32
            && y < (self.clip.y + self.clip.height) as i32
    }
}

/// Moves every coloured cell of `area` towards `toward` by `t` (0..1).
pub fn fade(buf: &mut Buffer, area: Rect, t: f32, toward: Color) {
    if t <= 0.0 {
        return;
    }
    for y in area.top()..area.bottom() {
        for x in area.left()..area.right() {
            if let Some(cell) = buf.cell_mut((x, y)) {
                if matches!(cell.fg, Color::Rgb(..)) {
                    cell.fg = theme::mix(cell.fg, toward, t);
                }
                if matches!(cell.bg, Color::Rgb(..)) {
                    cell.bg = theme::mix(cell.bg, toward, t);
                }
            }
        }
    }
}

/// A word whose letters light up as a wave passes over them.
pub fn shimmer(word: &str, ms: u128, base: Color, peak: Color) -> Line {
    let len = word.chars().count() as f32;
    let span = len + 8.0;
    let phase = (ms as f32 / 1000.0 * 9.0) % span - 4.0;
    word.chars()
        .enumerate()
        .map(|(i, c)| {
            let d = i as f32 - phase;
            let glow = (-(d * d) / 3.0).exp();
            (c.to_string(), Style::default().fg(theme::mix(base, peak, glow)))
        })
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(lines: &[Line]) -> Vec<String> {
        lines.iter().map(|l| l.iter().map(|(s, _)| s.as_str()).collect()).collect()
    }

    #[test]
    fn wrap_breaks_between_words() {
        let line = vec![("одна две три четыре".to_string(), Style::default())];
        assert_eq!(plain(&wrap(&line, 9)), vec!["одна две", "три", "четыре"]);
    }

    #[test]
    fn wrap_splits_a_word_longer_than_the_line() {
        let line = vec![("abcdefghij".to_string(), Style::default())];
        assert_eq!(plain(&wrap(&line, 4)), vec!["abcd", "efgh", "ij"]);
    }



    #[test]
    fn fit_cuts_by_display_width_and_marks_the_cut() {
        assert_eq!(fit("абвгдеж", 5), "абвг…");
        assert_eq!(fit("абв", 5), "абв");
    }
}
