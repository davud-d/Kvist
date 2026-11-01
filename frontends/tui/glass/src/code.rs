// A write the model is still typing, drawn in the conversation as a code block:
// a header with the language, the last rows of the code (the top edge fades
// where more lies above), and a fold when the call is over. The core streams
// the text as `call_stream` (docs/UI_API.md §7).

use std::time::Instant;

use ratatui::style::{Color, Modifier, Style};
use unicode_width::UnicodeWidthChar;

use crate::anim::Tween;
use crate::diff;
use crate::highlight;
use crate::text::{self, Line};
use crate::theme::{self, DIFF_ADD, DIFF_DEL, DUSK, FOG, MIST, MOSS, NIGHT, ROSEHIP};

/// Rows of code the block shows at most; more is reached with the wheel.
pub const MAX_BODY: usize = 14;
/// Columns of padding on each side of the code.
const PAD: usize = 2;
/// The top rows fade out by these shares when code lies above them.
const FADE: [f32; 2] = [0.65, 0.3];

pub struct CodeBlock {
    pub call: String,
    tool: String,
    path: String,
    fields: Vec<(String, String)>,
    pub finished: Option<Instant>,
    /// 1 while the block is open, easing to 0 as it folds.
    pub open: Tween,
    /// Rows scrolled up from the end; 0 follows the writing.
    pub up: usize,
    rows: Vec<Line>,
    changed: bool,
    width: usize,
}

fn background() -> Color {
    theme::mix(NIGHT, FOG, 0.09)
}

impl CodeBlock {
    pub fn new(call: &str, tool: &str, path: &str) -> CodeBlock {
        CodeBlock {
            call: call.into(),
            tool: tool.into(),
            path: path.into(),
            fields: Vec::new(),
            finished: None,
            open: Tween::settled(0.0),
            up: 0,
            rows: Vec::new(),
            changed: true,
            width: 0,
        }
    }

    /// Adds the lines that begin at `line` of `field`; line 0 starts it over
    /// (the request was retried), and lines that do not follow on are dropped.
    pub fn feed(&mut self, field: &str, line: u32, text: &str) {
        let index = self.fields.iter().position(|(name, _)| name == field).unwrap_or_else(|| {
            self.fields.push((field.into(), String::new()));
            self.fields.len() - 1
        });
        let have = &mut self.fields[index].1;
        if line == 0 {
            have.clear();
        }
        if line as usize != have.matches('\n').count() {
            return;
        }
        have.push_str(text);
        self.changed = true;
    }

    fn text_of(&self, field: &str) -> &str {
        self.fields.iter().find(|(name, _)| name == field).map_or("", |(_, text)| text.as_str())
    }

    /// Lines arriving and lines leaving.
    pub fn counts(&self) -> (u32, u32) {
        let lines = |text: &str| text.lines().count() as u32;
        if self.tool == "replace_in_file" {
            (lines(self.text_of("new_text")), lines(self.text_of("old_text")))
        } else {
            (lines(self.text_of("content")), 0)
        }
    }

    #[cfg(test)]
    pub fn rows_total(&self) -> usize {
        self.rows.len()
    }

    /// Builds the code's rows for a block `width` columns wide, when the text
    /// or the width changed since last time.
    pub fn rebuild(&mut self, width: usize) {
        if !self.changed && self.width == width {
            return;
        }
        self.changed = false;
        self.width = width;
        let inner = width.saturating_sub(2 * PAD).max(8);
        let language = diff::language(&self.path).to_string();
        let mut rows = Vec::new();
        if self.tool == "replace_in_file" {
            rows.extend(side_rows(self.text_of("old_text"), &language, inner, Some(('−', ROSEHIP, DIFF_DEL))));
            rows.extend(side_rows(self.text_of("new_text"), &language, inner, Some(('+', MOSS, DIFF_ADD))));
        } else {
            rows.extend(side_rows(self.text_of("content"), &language, inner, None));
        }
        self.rows = rows;
    }

    /// The most the wheel can scroll up.
    pub fn most_up(&self) -> usize {
        self.rows.len().saturating_sub(MAX_BODY)
    }

    /// The block as lines of the conversation, `width` columns each: the
    /// header, the window of code, a closing row. A folding block loses
    /// its rows from the bottom up.
    pub fn lines(&self, width: usize, now: Instant) -> Vec<Line> {
        let bg = Style::default().bg(background());
        let body = self.rows.len().min(MAX_BODY);
        let end = self.rows.len() - self.up.min(self.most_up());
        let start = end - body;
        let mut lines = vec![self.header(width, bg)];
        for (i, row) in self.rows[start..end].iter().enumerate() {
            let mut row = row.clone();
            if start > 0 && i < FADE.len() {
                row = fade(row, FADE[i]);
            }
            let mut line: Line = vec![(" ".repeat(PAD), bg)];
            line.extend(row);
            lines.push(padded(line, width, bg));
        }
        lines.push(padded(Vec::new(), width, bg));
        let shown = (self.open.value(now) * lines.len() as f32).round() as usize;
        lines.truncate(shown);
        lines
    }

    fn header(&self, width: usize, bg: Style) -> Line {
        let (icon, color) = theme::file_icon(&self.path);
        let mut line: Line = vec![
            (" ".repeat(PAD), bg),
            (icon.to_string(), bg.fg(color)),
            (" ".to_string(), bg),
            (language_name(&self.path), bg.fg(MIST).add_modifier(Modifier::BOLD)),
            (format!("  {}", text::fit(&self.path, width / 2)), bg.fg(DUSK)),
        ];
        let (added, removed) = self.counts();
        let mut tail: Line = Vec::new();
        if added > 0 {
            tail.push((format!("+{added}"), bg.fg(MOSS)));
        }
        if removed > 0 {
            tail.push((format!(" −{removed}"), bg.fg(ROSEHIP)));
        }
        let gap = width.saturating_sub(text::line_width(&line) + text::line_width(&tail) + PAD);
        line.push((" ".repeat(gap), bg));
        line.extend(tail);
        padded(line, width, bg)
    }
}

fn padded(mut line: Line, width: usize, bg: Style) -> Line {
    let used = text::line_width(&line);
    if used < width {
        line.push((" ".repeat(width - used), bg));
    }
    line
}

fn fade(row: Line, share: f32) -> Line {
    row.into_iter()
        .map(|(text, style)| {
            let fg = style.fg.unwrap_or(FOG);
            (text, style.fg(theme::mix(fg, background(), share)))
        })
        .collect()
}

/// One side of a write as coloured rows, long lines cut at `width` columns.
/// `marker` is the change sign, its colour and the row's tint.
fn side_rows(code: &str, language: &str, width: usize, marker: Option<(char, Color, Color)>) -> Vec<Line> {
    if code.is_empty() {
        return Vec::new();
    }
    let bg = marker.map_or(background(), |(_, _, tint)| tint);
    let gutter = if marker.is_some() { 2 } else { 0 };
    let mut rows = Vec::new();
    for tokens in highlight::highlight(language, code) {
        let spans: Line = tokens
            .into_iter()
            .map(|token| {
                let style = Style::default().fg(token.colour).bg(bg);
                (token.text, if token.italic { style.add_modifier(Modifier::ITALIC) } else { style })
            })
            .collect();
        for (n, piece) in cut(&spans, width.saturating_sub(gutter).max(4)).into_iter().enumerate() {
            let mut row: Line = Vec::new();
            if let Some((sign, color, _)) = marker {
                let sign = if n == 0 { sign.to_string() } else { " ".to_string() };
                row.push((format!("{sign} "), Style::default().fg(color).bg(bg).add_modifier(Modifier::BOLD)));
            }
            row.extend(piece);
            rows.push(row_to_width(row, width, bg));
        }
    }
    rows
}

fn row_to_width(mut row: Line, width: usize, bg: Color) -> Line {
    let used = text::line_width(&row);
    if used < width {
        row.push((" ".repeat(width - used), Style::default().bg(bg)));
    }
    row
}

/// `spans` cut into pieces of at most `width` columns, keeping spaces
/// (indentation is part of code).
fn cut(spans: &Line, width: usize) -> Vec<Line> {
    let mut rows: Vec<Line> = vec![Vec::new()];
    let mut used = 0;
    for (text, style) in spans {
        let mut piece = String::new();
        for c in text.chars() {
            let w = c.width().unwrap_or(0);
            if used + w > width && used > 0 {
                rows.last_mut().unwrap().push((std::mem::take(&mut piece), *style));
                rows.push(Vec::new());
                used = 0;
            }
            piece.push(c);
            used += w;
        }
        if !piece.is_empty() {
            rows.last_mut().unwrap().push((piece, *style));
        }
    }
    rows
}

pub fn language_name(path: &str) -> String {
    match diff::language(path) {
        "py" => "Python".into(),
        "rs" => "Rust".into(),
        "ts" | "mts" => "TypeScript".into(),
        "tsx" => "TSX".into(),
        "jsx" => "JSX".into(),
        "js" | "mjs" | "cjs" => "JavaScript".into(),
        "json" => "JSON".into(),
        "md" => "Markdown".into(),
        "html" => "HTML".into(),
        "css" => "CSS".into(),
        "sh" | "bash" | "zsh" => "Shell".into(),
        "fish" => "Fish".into(),
        "toml" => "TOML".into(),
        "yaml" | "yml" => "YAML".into(),
        "go" => "Go".into(),
        "c" | "h" => "C".into(),
        "cpp" | "hpp" | "cc" => "C++".into(),
        "java" => "Java".into(),
        "" => "Text".into(),
        other => other.to_uppercase(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::time::Duration;

    fn plain(line: &Line) -> String {
        line.iter().map(|(s, _)| s.as_str()).collect()
    }

    fn open(block: &mut CodeBlock, now: Instant) {
        block.open = Tween::settled(1.0);
        block.rebuild(60);
        let _ = now;
    }

    #[test]
    fn a_write_fills_the_block_and_a_retry_starts_it_over() {
        let now = Instant::now();
        let mut block = CodeBlock::new("c1", "write_file", "app/main.py");
        block.feed("content", 0, "import os\nx = 1\n");
        block.feed("content", 2, "y = 2\n");
        block.feed("content", 9, "skipped\n");
        open(&mut block, now);
        let lines = block.lines(60, now);
        assert!(plain(&lines[0]).contains("Python") && plain(&lines[0]).contains("+3"));
        assert!(plain(&lines[1]).contains("import os") && plain(&lines[3]).contains("y = 2"));
        block.feed("content", 0, "z\n");
        block.rebuild(60);
        assert_eq!(block.rows_total(), 1);
    }

    #[test]
    fn a_long_write_shows_the_end_with_a_fading_edge_and_the_wheel_goes_back() {
        let now = Instant::now();
        let mut block = CodeBlock::new("c1", "write_file", "a.py");
        let code: String = (1..=40).map(|n| format!("line_{n} = {n}\n")).collect();
        block.feed("content", 0, &code);
        open(&mut block, now);
        let lines = block.lines(60, now);
        assert_eq!(lines.len(), MAX_BODY + 2);
        assert!(plain(&lines[1]).contains("line_27") && plain(&lines[MAX_BODY]).contains("line_40"));
        assert_ne!(lines[1][1].1.fg, lines[MAX_BODY][1].1.fg, "the top row is dimmer than the last");
        block.up = 100;
        let back = block.lines(60, now);
        assert!(plain(&back[1]).contains("line_1 "), "{}", plain(&back[1]));
    }

    #[test]
    fn a_replacement_marks_what_goes_out_and_what_comes_in() {
        let now = Instant::now();
        let mut block = CodeBlock::new("c2", "replace_in_file", "a.py");
        block.feed("old_text", 0, "x = 1\n");
        block.feed("new_text", 0, "x = 2\ny = 3\n");
        open(&mut block, now);
        let lines = block.lines(60, now);
        assert!(plain(&lines[1]).starts_with("  − "));
        assert!(plain(&lines[2]).starts_with("  + ") && plain(&lines[3]).starts_with("  + "));
        assert_eq!(block.counts(), (2, 1));
    }

    #[test]
    fn a_folding_block_loses_its_rows_from_the_bottom() {
        let now = Instant::now();
        let mut block = CodeBlock::new("c1", "write_file", "a.py");
        block.feed("content", 0, "a\nb\nc\n");
        open(&mut block, now);
        assert_eq!(block.lines(60, now).len(), 5);
        block.open.go(0.0, now, 100, crate::anim::Ease::Burst);
        let half = block.lines(60, now + Duration::from_millis(40));
        assert!(!half.is_empty() && half.len() < 5 && plain(&half[0]).contains("Python"));
        assert!(block.lines(60, now + Duration::from_millis(200)).is_empty());
    }

    #[test]
    fn long_lines_wrap_and_indentation_stays() {
        let rows = side_rows("    deep = 1\n", "py", 6, None);
        assert_eq!(plain(&rows[0]).trim_end(), "    de");
    }
}
