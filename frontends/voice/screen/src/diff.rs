// A unified diff for reading: line numbers of both files, syntax colours,
// tinted added and removed rows, the changed stretch of a rewritten line
// marked, and long lines wrapped. The core sends the diff of each write in
// `action_result.patch` and of an overwrite in an approval's `subject.diff`.

use ratatui::style::{Color, Modifier, Style};
use unicode_width::UnicodeWidthChar;

use crate::highlight;
use crate::text::{self, Line};
use crate::theme::{self, DIFF_ADD, DIFF_ADD_STRONG, DIFF_DEL, DIFF_DEL_STRONG, DUSK, MOSS, NIGHT, ROSEHIP};

/// Windows at least this wide show the old file beside the new one.
pub const SPLIT_MIN_WIDTH: usize = 84;

#[derive(Clone, Copy, Debug, PartialEq)]
pub enum Kind {
    Context,
    Added,
    Removed,
}

/// A run of text in one colour; `strong` marks the part of a rewritten line
/// that differs from the line it replaced.
#[derive(Clone, Debug, PartialEq)]
pub struct Seg {
    pub text: String,
    pub colour: Color,
    pub italic: bool,
    pub strong: bool,
}

#[derive(Clone, Debug, PartialEq)]
pub struct Row {
    pub kind: Kind,
    pub old_no: Option<u32>,
    pub new_no: Option<u32>,
    pub segs: Vec<Seg>,
}

#[derive(Clone, Debug, PartialEq)]
pub enum Entry {
    /// A hunk begins at this line of the new file.
    Hunk(u32),
    Row(Row),
    /// The core cut the patch off (`\ patch truncated`).
    Cut,
}

#[derive(Clone, Debug, Default, PartialEq)]
pub struct Patch {
    pub entries: Vec<Entry>,
}

/// The file extension, which the highlighter takes as a language.
pub fn language(path: &str) -> &str {
    path.rsplit('/').next().and_then(|name| name.rsplit_once('.')).map_or("", |(_, ext)| ext)
}

impl Patch {
    /// Reads the hunks of a unified diff. Everything before the first hunk
    /// header (`---` and `+++` lines) is file naming and is skipped.
    pub fn parse(text: &str, language: &str) -> Patch {
        let mut patch = Patch::default();
        let mut hunk: Option<RawHunk> = None;
        for line in text.lines() {
            if let Some((old, new)) = hunk_header(line) {
                patch.finish(hunk.take(), language);
                hunk = Some(RawHunk { old, new, rows: Vec::new() });
                continue;
            }
            let Some(open) = hunk.as_mut() else { continue };
            let expanded = |rest: &str| rest.replace('\t', "    ");
            match line.chars().next() {
                Some('+') => open.rows.push((Kind::Added, expanded(&line[1..]))),
                Some('-') => open.rows.push((Kind::Removed, expanded(&line[1..]))),
                Some(' ') => open.rows.push((Kind::Context, expanded(&line[1..]))),
                // A context line whose trailing space was stripped on the way.
                None => open.rows.push((Kind::Context, String::new())),
                Some('\\') if line.contains("truncated") => {
                    patch.finish(hunk.take(), language);
                    patch.entries.push(Entry::Cut);
                }
                _ => {}
            }
        }
        patch.finish(hunk, language);
        patch
    }

    pub fn append(&mut self, other: Patch) {
        self.entries.extend(other.entries);
    }

    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    fn finish(&mut self, hunk: Option<RawHunk>, language: &str) {
        let Some(hunk) = hunk.filter(|h| !h.rows.is_empty()) else { return };
        let rows = build_rows(hunk, language);
        let first = rows.first().and_then(|row| row.new_no.or(row.old_no)).unwrap_or(1);
        self.entries.push(Entry::Hunk(first));
        self.entries.extend(rows.into_iter().map(Entry::Row));
    }
}

struct RawHunk {
    old: u32,
    new: u32,
    rows: Vec<(Kind, String)>,
}

/// `@@ -a[,b] +c[,d] @@` gives the first line of the hunk in each file; the
/// side of an empty range names the line before it, but no row is numbered
/// from an empty side, so `max(1)` only guards `0` for a new file.
fn hunk_header(line: &str) -> Option<(u32, u32)> {
    let rest = line.strip_prefix("@@ -")?;
    let (old, rest) = rest.split_once(' ')?;
    let new = rest.strip_prefix('+')?.split_once(' ')?.0;
    let start = |range: &str| range.split(',').next()?.parse::<u32>().ok();
    Some((start(old)?, start(new)?))
}

fn build_rows(hunk: RawHunk, language: &str) -> Vec<Row> {
    let code = |keep: fn(Kind) -> bool| {
        let lines: Vec<&str> = hunk.rows.iter().filter(|(kind, _)| keep(*kind)).map(|(_, text)| text.as_str()).collect();
        if lines.is_empty() { Vec::new() } else { highlight::highlight(language, &lines.join("\n")) }
    };
    // Each side is coloured as one piece of code, so a string or comment
    // that spans lines keeps its colour.
    let mut old_tokens = code(|kind| kind != Kind::Added).into_iter();
    let mut new_tokens = code(|kind| kind != Kind::Removed).into_iter();
    let (mut old_no, mut new_no) = (hunk.old.max(1), hunk.new.max(1));
    let mut rows = Vec::with_capacity(hunk.rows.len());
    for (kind, text) in &hunk.rows {
        let tokens = match kind {
            Kind::Removed => old_tokens.next(),
            Kind::Added => new_tokens.next(),
            Kind::Context => {
                old_tokens.next();
                new_tokens.next()
            }
        };
        let segs = match tokens {
            Some(tokens) => tokens.into_iter().map(|t| Seg { text: t.text, colour: t.colour, italic: t.italic, strong: false }).collect(),
            None => vec![Seg { text: text.clone(), colour: theme::FOG, italic: false, strong: false }],
        };
        let (old, new) = match kind {
            Kind::Context => (Some(old_no), Some(new_no)),
            Kind::Removed => (Some(old_no), None),
            Kind::Added => (None, Some(new_no)),
        };
        old_no += u32::from(old.is_some());
        new_no += u32::from(new.is_some());
        rows.push(Row { kind: *kind, old_no: old, new_no: new, segs });
    }
    mark_rewrites(&mut rows);
    rows
}

/// In each run of removed rows followed by added rows, pairs the n-th of
/// each and marks what differs between the two lines. Lines that share
/// less than a quarter of their text are replaced outright, not marked.
fn mark_rewrites(rows: &mut [Row]) {
    let mut at = 0;
    while at < rows.len() {
        if rows[at].kind != Kind::Removed {
            at += 1;
            continue;
        }
        let removed_end = at + rows[at..].iter().take_while(|r| r.kind == Kind::Removed).count();
        let added_end = removed_end + rows[removed_end..].iter().take_while(|r| r.kind == Kind::Added).count();
        for pair in 0..(removed_end - at).min(added_end - removed_end) {
            let (old, new) = (at + pair, removed_end + pair);
            let (a, b) = (plain(&rows[old]), plain(&rows[new]));
            let prefix = a.iter().zip(&b).take_while(|(x, y)| x == y).count();
            let suffix = a[prefix..].iter().rev().zip(b[prefix..].iter().rev()).take_while(|(x, y)| x == y).count();
            if (prefix + suffix) * 4 < a.len().min(b.len()) {
                continue;
            }
            rows[old].segs = mark(std::mem::take(&mut rows[old].segs), prefix, a.len() - suffix);
            rows[new].segs = mark(std::mem::take(&mut rows[new].segs), prefix, b.len() - suffix);
        }
        at = added_end.max(at + 1);
    }
}

fn plain(row: &Row) -> Vec<char> {
    row.segs.iter().flat_map(|seg| seg.text.chars()).collect()
}

/// Splits `segs` so that the characters from `from` to `to` are `strong`.
fn mark(segs: Vec<Seg>, from: usize, to: usize) -> Vec<Seg> {
    let mut out = Vec::with_capacity(segs.len() + 2);
    let mut seen = 0;
    for seg in segs {
        let chars: Vec<char> = seg.text.chars().collect();
        let mut start = 0;
        while start < chars.len() {
            let strong = (from..to).contains(&(seen + start));
            let mut end = start;
            while end < chars.len() && (from..to).contains(&(seen + end)) == strong {
                end += 1;
            }
            out.push(Seg { text: chars[start..end].iter().collect(), strong, ..seg.clone() });
            start = end;
        }
        seen += chars.len();
    }
    out
}

fn tint(kind: Kind, strong: bool) -> Option<Color> {
    match (kind, strong) {
        (Kind::Context, _) => None,
        (Kind::Added, false) => Some(DIFF_ADD),
        (Kind::Added, true) => Some(DIFF_ADD_STRONG),
        (Kind::Removed, false) => Some(DIFF_DEL),
        (Kind::Removed, true) => Some(DIFF_DEL_STRONG),
    }
}

fn seg_style(kind: Kind, seg: &Seg) -> Style {
    let colour = match kind {
        Kind::Context => theme::mix(seg.colour, NIGHT, 0.35),
        Kind::Added => seg.colour,
        Kind::Removed => theme::mix(seg.colour, NIGHT, 0.2),
    };
    let mut style = Style::default().fg(colour);
    if let Some(bg) = tint(kind, seg.strong) {
        style = style.bg(bg);
    }
    if seg.italic {
        style = style.add_modifier(Modifier::ITALIC);
    }
    style
}

fn row_bg(kind: Kind) -> Style {
    tint(kind, false).map_or_else(Style::default, |bg| Style::default().bg(bg))
}

/// `segs` cut into pieces of at most `width` columns.
fn wrap_segs(segs: &[Seg], width: usize) -> Vec<Vec<Seg>> {
    let width = width.max(1);
    let mut rows: Vec<Vec<Seg>> = vec![Vec::new()];
    let mut used = 0;
    for seg in segs {
        let mut piece = String::new();
        for c in seg.text.chars() {
            let w = c.width().unwrap_or(0);
            if used + w > width && used > 0 {
                rows.last_mut().unwrap().push(Seg { text: std::mem::take(&mut piece), ..seg.clone() });
                rows.push(Vec::new());
                used = 0;
            }
            piece.push(c);
            used += w;
        }
        if !piece.is_empty() {
            rows.last_mut().unwrap().push(Seg { text: piece, ..seg.clone() });
        }
    }
    rows
}

fn number_width(patch: &Patch) -> usize {
    let top = patch
        .entries
        .iter()
        .filter_map(|entry| match entry {
            Entry::Row(row) => row.old_no.max(row.new_no),
            _ => None,
        })
        .max()
        .unwrap_or(0);
    top.to_string().len().max(2)
}

fn number(no: Option<u32>, width: usize, colour: Color) -> (String, Style) {
    (format!("{:>width$}", no.map_or(String::new(), |n| n.to_string())), Style::default().fg(colour))
}

fn marker(kind: Kind) -> (String, Style) {
    let (glyph, colour) = match kind {
        Kind::Context => (" ", DUSK),
        Kind::Added => ("+", MOSS),
        Kind::Removed => ("−", ROSEHIP),
    };
    (glyph.to_string(), Style::default().fg(colour).add_modifier(Modifier::BOLD))
}

fn pad(line: &mut Line, to: usize, style: Style) {
    let used = text::line_width(line);
    if used < to {
        line.push((" ".repeat(to - used), style));
    }
}

fn separator(entry: &Entry, width: usize, cut: &str) -> Line {
    let label = match entry {
        Entry::Hunk(start) => format!(" ⋯ {start}"),
        _ => format!(" ⋯ {cut}"),
    };
    let mut line = vec![(text::fit(&label, width), Style::default().fg(DUSK))];
    pad(&mut line, width, Style::default());
    line
}

/// One column of rows: old and new numbers, a marker, then the code.
pub fn inline(patch: &Patch, width: usize, cut: &str) -> Vec<Line> {
    let nw = number_width(patch);
    let gutter = 2 * nw + 5;
    let text_w = width.saturating_sub(gutter).max(8);
    let number_colour = |kind: Kind| match kind {
        Kind::Added => theme::mix(MOSS, NIGHT, 0.45),
        Kind::Removed => theme::mix(ROSEHIP, NIGHT, 0.45),
        Kind::Context => DUSK,
    };
    let mut lines = Vec::new();
    for (index, entry) in patch.entries.iter().enumerate() {
        let Entry::Row(row) = entry else {
            if index > 0 {
                lines.push(Vec::new());
            }
            if !matches!(entry, Entry::Hunk(start) if index == 0 && *start <= 1) {
                lines.push(separator(entry, width, cut));
            }
            continue;
        };
        for (n, piece) in wrap_segs(&row.segs, text_w).into_iter().enumerate() {
            let bg = row_bg(row.kind);
            let mut line: Line = vec![(" ".into(), bg)];
            if n == 0 {
                let colour = number_colour(row.kind);
                line.push(with_bg(number(row.old_no, nw, colour), bg));
                line.push((" ".into(), bg));
                line.push(with_bg(number(row.new_no, nw, colour), bg));
                line.push((" ".into(), bg));
                line.push(with_bg(marker(row.kind), bg));
            } else {
                line.push((" ".repeat(2 * nw + 2), bg));
                line.push(("↪".into(), bg.fg(DUSK)));
            }
            line.push((" ".into(), bg));
            line.extend(piece.iter().map(|seg| (seg.text.clone(), seg_style(row.kind, seg))));
            pad(&mut line, width, bg);
            lines.push(line);
        }
    }
    lines
}

fn with_bg((text, style): (String, Style), bg: Style) -> (String, Style) {
    (text, Style { bg: bg.bg, ..style })
}

/// The old file on the left and the new one on the right; a removed row
/// sits opposite the added row that replaced it.
pub fn split(patch: &Patch, width: usize, cut: &str) -> Vec<Line> {
    let nw = number_width(patch);
    let left_w = width.saturating_sub(1) / 2;
    let right_w = width.saturating_sub(1) - left_w;
    let mut lines = Vec::new();
    let mut at = 0;
    while at < patch.entries.len() {
        let row = match &patch.entries[at] {
            Entry::Row(row) => row,
            entry => {
                if at > 0 {
                    lines.push(Vec::new());
                }
                if !matches!(entry, Entry::Hunk(start) if at == 0 && *start <= 1) {
                    lines.push(separator(entry, width, cut));
                }
                at += 1;
                continue;
            }
        };
        let rows_from = |from: usize, kind: Kind| -> Vec<&Row> {
            patch.entries[from..]
                .iter()
                .map_while(|entry| match entry {
                    Entry::Row(row) if row.kind == kind => Some(row),
                    _ => None,
                })
                .collect()
        };
        let (olds, news): (Vec<&Row>, Vec<&Row>) = match row.kind {
            Kind::Context => (vec![row], vec![row]),
            Kind::Added => (Vec::new(), rows_from(at, Kind::Added)),
            Kind::Removed => {
                let removed = rows_from(at, Kind::Removed);
                let added = rows_from(at + removed.len(), Kind::Added);
                (removed, added)
            }
        };
        at += if row.kind == Kind::Context { 1 } else { olds.len() + news.len() };
        for pair in 0..olds.len().max(news.len()) {
            let left = half(olds.get(pair).copied(), true, nw, left_w);
            let right = half(news.get(pair).copied(), false, nw, right_w);
            for n in 0..left.len().max(right.len()) {
                let blank = |w: usize| vec![(" ".repeat(w), Style::default())];
                let mut line = left.get(n).cloned().unwrap_or_else(|| blank(left_w));
                line.push(("│".into(), Style::default().fg(DUSK)));
                line.extend(right.get(n).cloned().unwrap_or_else(|| blank(right_w)));
                lines.push(line);
            }
        }
    }
    lines
}

/// One side of a pair, wrapped, each row `width` columns wide.
fn half(row: Option<&Row>, old_side: bool, nw: usize, width: usize) -> Vec<Line> {
    let Some(row) = row else { return Vec::new() };
    let text_w = width.saturating_sub(nw + 4).max(8);
    let bg = row_bg(row.kind);
    let no = if old_side { row.old_no } else { row.new_no };
    let colour = match row.kind {
        Kind::Added => theme::mix(MOSS, NIGHT, 0.45),
        Kind::Removed => theme::mix(ROSEHIP, NIGHT, 0.45),
        Kind::Context => DUSK,
    };
    wrap_segs(&row.segs, text_w)
        .into_iter()
        .enumerate()
        .map(|(n, piece)| {
            let mut line: Line = vec![(" ".into(), bg)];
            if n == 0 {
                line.push(with_bg(number(no, nw, colour), bg));
                line.push((" ".into(), bg));
                line.push(with_bg(marker(row.kind), bg));
            } else {
                line.push((" ".repeat(nw + 1), bg));
                line.push(("↪".into(), bg.fg(DUSK)));
            }
            line.push((" ".into(), bg));
            line.extend(piece.iter().map(|seg| (seg.text.clone(), seg_style(row.kind, seg))));
            pad(&mut line, width, bg);
            line
        })
        .collect()
}

/// A file's patches as the side panel shows them, rebuilt only when the
/// patches or the width change.
pub struct View {
    pub path: String,
    pub scroll: usize,
    pub lines: Vec<Line>,
    /// How many of the file's patches `lines` was built from, and at what width.
    built: Option<(usize, usize)>,
}

impl View {
    pub fn new(path: String) -> View {
        View { path, scroll: 0, lines: Vec::new(), built: None }
    }

    /// Rebuilds `lines` for `patches` (one per write, oldest first) shown
    /// `width` columns wide.
    pub fn sync(&mut self, patches: &[String], width: usize, cut: &str) {
        if self.built == Some((patches.len(), width)) {
            return;
        }
        let mut all = Patch::default();
        for text in patches {
            all.append(Patch::parse(text, language(&self.path)));
        }
        self.lines = inline(&all, width, cut);
        self.built = Some((patches.len(), width));
    }

    /// Scrolls by `delta` rows, never past the last row of a view `rows` tall.
    pub fn scroll_by(&mut self, delta: i32, rows: usize) {
        let most = self.lines.len().saturating_sub(rows);
        self.scroll = (self.scroll as i32 + delta).clamp(0, most as i32) as usize;
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const PATCH: &str = "--- a/a.py\n+++ b/a.py\n@@ -10,4 +10,4 @@\n keep\n-total = price * 2\n+total = price * 3\n keep too\n";

    fn rows(patch: &Patch) -> Vec<&Row> {
        patch.entries.iter().filter_map(|e| if let Entry::Row(r) = e { Some(r) } else { None }).collect()
    }

    fn text_of(line: &Line) -> String {
        line.iter().map(|(s, _)| s.as_str()).collect()
    }

    #[test]
    fn rows_are_numbered_in_the_old_and_the_new_file() {
        let patch = Patch::parse(PATCH, "py");
        assert_eq!(patch.entries[0], Entry::Hunk(10));
        let numbers: Vec<_> = rows(&patch).iter().map(|r| (r.kind, r.old_no, r.new_no)).collect();
        assert_eq!(
            numbers,
            [
                (Kind::Context, Some(10), Some(10)),
                (Kind::Removed, Some(11), None),
                (Kind::Added, None, Some(11)),
                (Kind::Context, Some(12), Some(12)),
            ]
        );
    }

    #[test]
    fn file_naming_lines_and_a_missing_newline_note_are_not_rows() {
        let patch = Patch::parse("--- a/x\n+++ b/x\n@@ -1 +1 @@\n-a\n\\ No newline at end of file\n+b", "");
        assert_eq!(rows(&patch).len(), 2);
    }

    #[test]
    fn a_removed_line_that_starts_with_dashes_is_still_a_removal() {
        let patch = Patch::parse("@@ -1,2 +1,1 @@\n--- not a header\n kept", "");
        assert_eq!(rows(&patch)[0].kind, Kind::Removed);
        assert_eq!(plain(rows(&patch)[0]).iter().collect::<String>(), "-- not a header");
    }

    #[test]
    fn a_rewritten_line_marks_only_the_part_that_changed() {
        let patch = Patch::parse(PATCH, "py");
        let strong = |row: &Row| row.segs.iter().filter(|s| s.strong).map(|s| s.text.as_str()).collect::<String>();
        let rows = rows(&patch);
        assert_eq!((strong(rows[1]), strong(rows[2])), ("2".to_string(), "3".to_string()));
        assert_eq!(plain(rows[2]).iter().collect::<String>(), "total = price * 3");
    }

    #[test]
    fn lines_with_little_in_common_are_replaced_not_marked() {
        let patch = Patch::parse("@@ -1 +1 @@\n-alpha beta\n+x", "");
        assert!(rows(&patch).iter().all(|r| r.segs.iter().all(|s| !s.strong)));
    }

    #[test]
    fn an_empty_context_line_and_tabs_survive() {
        let patch = Patch::parse("@@ -1,3 +1,3 @@\n a\n\n-\tb\n+\tc", "");
        let rows = rows(&patch);
        assert_eq!(rows.len(), 4);
        assert_eq!(rows[1].kind, Kind::Context);
        assert_eq!(plain(rows[2]).iter().collect::<String>(), "    b");
    }

    #[test]
    fn a_new_file_starts_at_its_first_line_and_a_cut_patch_says_so() {
        let patch = Patch::parse("--- /dev/null\n+++ b/n.py\n@@ -0,0 +1,2 @@\n+a\n+b\n\\ patch truncated", "py");
        assert_eq!(patch.entries[0], Entry::Hunk(1));
        assert_eq!(rows(&patch).iter().map(|r| r.new_no).collect::<Vec<_>>(), [Some(1), Some(2)]);
        assert_eq!(patch.entries.last(), Some(&Entry::Cut));
    }

    #[test]
    fn a_patch_without_hunks_is_empty() {
        assert!(Patch::parse("--- a/x\n+++ b/x\n", "").is_empty());
        assert!(Patch::parse("", "").is_empty());
    }

    #[test]
    fn the_inline_view_fills_every_row_to_the_width_and_wraps_long_lines() {
        let long = format!("@@ -1 +1 @@\n+{}", "x".repeat(60));
        let lines = inline(&Patch::parse(&long, ""), 40, "cut");
        assert!(lines.len() >= 2);
        assert!(lines.iter().all(|line| text::line_width(line) == 40), "{:?}", lines.iter().map(text_of).collect::<Vec<_>>());
        assert!(text_of(&lines[1]).contains('↪'));
        let joined: String = lines.iter().map(text_of).collect();
        assert_eq!(joined.matches('x').count(), 60);
    }

    #[test]
    fn added_and_removed_rows_are_tinted_and_context_is_not() {
        let lines = inline(&Patch::parse(PATCH, "py"), 60, "cut");
        let bg = |at: usize| lines[at].last().and_then(|(_, style)| style.bg);
        // [0] is the first hunk's context row: the header of a hunk at line 10 comes first.
        assert_eq!(text_of(&lines[0]).trim(), "⋯ 10");
        assert_eq!(bg(1), None);
        assert_eq!(bg(2), Some(DIFF_DEL));
        assert_eq!(bg(3), Some(DIFF_ADD));
    }

    #[test]
    fn the_split_view_puts_a_removal_opposite_its_replacement() {
        let lines = split(&Patch::parse(PATCH, "py"), 91, "cut");
        let texts: Vec<String> = lines.iter().map(text_of).collect();
        assert!(lines.iter().all(|line| text::line_width(line) == 91), "{texts:?}");
        let both = texts.iter().find(|t| t.contains("price * 2")).unwrap();
        assert!(both.contains("price * 3") && both.find("price * 2") < both.find('│'));
    }

    #[test]
    fn an_uneven_replacement_leaves_the_short_side_empty() {
        let lines = split(&Patch::parse("@@ -1,2 +1,1 @@\n-one\n-two\n+uno", ""), 60, "cut");
        let texts: Vec<String> = lines.iter().map(text_of).collect();
        assert_eq!(texts.len(), 2);
        assert!(texts[1].contains("two") && !texts[1].split('│').nth(1).unwrap().contains(char::is_alphanumeric));
    }

    #[test]
    fn a_view_rebuilds_only_when_the_patches_or_the_width_change() {
        let mut view = View::new("a.py".into());
        view.sync(&[PATCH.to_string()], 60, "cut");
        let first = view.lines.len();
        view.lines.clear();
        view.sync(&[PATCH.to_string()], 60, "cut");
        assert!(view.lines.is_empty());
        view.sync(&[PATCH.to_string(), PATCH.to_string()], 60, "cut");
        assert!(view.lines.len() > first);
    }

    #[test]
    fn scrolling_stops_at_both_ends() {
        let mut view = View::new("a.py".into());
        view.sync(&[PATCH.to_string()], 60, "cut");
        let total = view.lines.len();
        view.scroll_by(50, 2);
        assert_eq!(view.scroll, total - 2);
        view.scroll_by(-50, 2);
        assert_eq!(view.scroll, 0);
    }

    #[test]
    fn the_language_is_the_extension() {
        assert_eq!(language("kernel/helpers.py"), "py");
        assert_eq!(language("Makefile"), "");
        assert_eq!(language("a.d/Makefile"), "");
    }
}
