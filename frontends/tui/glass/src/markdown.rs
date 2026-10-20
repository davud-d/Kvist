// Draws the agent's Markdown (CommonMark plus tables, task lists and
// strikethrough) as styled, wrapped lines: headings, lists, quotes, rules,
// tables and fenced code with syntax colours. pulldown-cmark does the parsing;
// text that is still arriving renders too, since an open fence or list is a
// valid document.

use pulldown_cmark::{CodeBlockKind, Event, Options, Parser, Tag, TagEnd};
use ratatui::style::{Modifier, Style};

use crate::highlight;
use crate::math;
use crate::text::{self, Line};
use crate::theme::{CODE_BG, DUSK, FOG, LINK, MIST, MOON};

/// Longest horizontal rule.
const RULE_WIDTH: usize = 48;
/// Narrowest a table column is squeezed to before the table overflows.
const MIN_COLUMN: usize = 4;

fn style(colour: ratatui::style::Color) -> Style {
    Style::default().fg(colour)
}

/// How tall a display formula is drawn, as a multiple of the cell height; the text around it is
/// about 0.8 of a cell, and a formula reads better a little larger.
const FORMULA_EM: f32 = 1.3;

/// A display formula's place among the lines `render_with_math` returns: the rows from `line` on are
/// empty, and the picture goes over them.
#[derive(Clone, Debug, PartialEq)]
pub struct Formula {
    pub line: usize,
    pub tex: String,
    pub fit: math::Fit,
}

/// `source` as lines at most `width` columns wide, display formulas as text.
#[cfg(test)]
pub fn render(source: &str, width: usize) -> Vec<Line> {
    render_with_math(source, width, None).0
}

/// Like `render`, but with the cell size in pixels a display formula is reserved rows for and drawn
/// as a picture; one RaTeX cannot read, or no cell size, is written as text.
pub fn render_with_math(source: &str, width: usize, cell: Option<(u32, u32)>) -> (Vec<Line>, Vec<Formula>) {
    let width = width.max(12);
    let mut lines: Vec<Line> = Vec::new();
    let mut formulas = Vec::new();
    let mut gap_due = false;
    for piece in math::split(source) {
        let text = match piece {
            math::Piece::Text(text) => text,
            math::Piece::Display(tex) => {
                let fit = cell.and_then(|cell| math::fit(&tex, cell.1 as f32 * FORMULA_EM, width as u32 * cell.0, cell));
                let Some(fit) = fit else {
                    gap_due = true;
                    separate(&mut lines);
                    lines.extend(render_text(math::tex_to_text(&tex).trim(), width));
                    continue;
                };
                separate(&mut lines);
                formulas.push(Formula { line: lines.len(), tex: tex.trim().to_string(), fit });
                lines.extend(std::iter::repeat_n(Vec::new(), fit.rows as usize));
                gap_due = true;
                continue;
            }
        };
        let rendered = render_text(&text, width);
        if rendered.is_empty() {
            continue;
        }
        if gap_due {
            lines.push(Vec::new());
            gap_due = false;
        }
        lines.extend(rendered);
    }
    (lines, formulas)
}

/// A blank line between a block and what comes next, unless there is one already.
fn separate(lines: &mut Vec<Line>) {
    if lines.last().is_some_and(|line| !line.is_empty()) {
        lines.push(Vec::new());
    }
}

fn render_text(source: &str, width: usize) -> Vec<Line> {
    let mut renderer = Renderer::new(width);
    let options = Options::ENABLE_TABLES | Options::ENABLE_STRIKETHROUGH | Options::ENABLE_TASKLISTS;
    for event in Parser::new_ext(source, options) {
        renderer.event(event);
    }
    renderer.finish()
}

struct ListItem {
    marker: String,
    /// The marker is drawn on the item's first line only.
    used: bool,
}

#[derive(Default)]
struct Table {
    rows: Vec<Vec<String>>,
    row: Vec<String>,
    header_rows: usize,
}

struct Renderer {
    width: usize,
    out: Vec<Line>,
    inline: Line,
    styles: Vec<Style>,
    /// Bullets and numbers of the lists around the text, outermost first.
    lists: Vec<Option<u64>>,
    items: Vec<ListItem>,
    quotes: usize,
    code: Option<(String, String)>,
    table: Option<Table>,
    links: Vec<String>,
}

impl Renderer {
    fn new(width: usize) -> Self {
        Renderer {
            width,
            out: Vec::new(),
            inline: Vec::new(),
            styles: vec![style(FOG)],
            lists: Vec::new(),
            items: Vec::new(),
            quotes: 0,
            code: None,
            table: None,
            links: Vec::new(),
        }
    }

    fn base(&self) -> Style {
        *self.styles.last().unwrap_or(&style(FOG))
    }

    fn event(&mut self, event: Event) {
        match event {
            Event::Start(tag) => self.start(tag),
            Event::End(tag) => self.end(tag),
            Event::Text(text) => match self.code.as_mut() {
                Some((_, code)) => code.push_str(&text),
                None => self.inline.push((text.to_string(), self.base())),
            },
            Event::Code(code) => {
                let base = self.base();
                self.inline.push((code.to_string(), base.fg(MOON)));
            }
            // A single newline in the source is a line break: the model writes lists and steps that way.
            Event::SoftBreak | Event::HardBreak => self.flush(),
            Event::Rule => {
                self.flush();
                self.gap();
                let mut line = self.prefix(false);
                line.push(("─".repeat(self.room().min(RULE_WIDTH)), style(DUSK)));
                self.out.push(line);
            }
            Event::TaskListMarker(done) => {
                let mark = if done { "☑ " } else { "☐ " };
                self.inline.push((mark.to_string(), style(MIST)));
            }
            Event::Html(html) | Event::InlineHtml(html) => self.inline.push((html.to_string(), style(DUSK))),
            _ => {}
        }
    }

    fn start(&mut self, tag: Tag) {
        match tag {
            Tag::Paragraph => {
                self.gap();
            }
            Tag::Heading { level, .. } => {
                self.gap();
                let colour = if (level as usize) <= 2 { MOON } else { FOG };
                self.styles.push(style(colour).add_modifier(Modifier::BOLD));
            }
            Tag::BlockQuote(_) => {
                self.flush();
                self.gap();
                self.quotes += 1;
                self.styles.push(style(MIST));
            }
            Tag::CodeBlock(kind) => {
                self.flush();
                let language = match kind {
                    CodeBlockKind::Fenced(info) => info.split_whitespace().next().unwrap_or("").to_string(),
                    CodeBlockKind::Indented => String::new(),
                };
                self.code = Some((language, String::new()));
            }
            Tag::List(first) => {
                self.flush();
                self.gap();
                self.lists.push(first);
            }
            Tag::Item => {
                self.flush();
                let marker = match self.lists.last_mut() {
                    Some(Some(number)) => {
                        let marker = format!("{number}. ");
                        *number += 1;
                        marker
                    }
                    _ => "• ".to_string(),
                };
                self.items.push(ListItem { marker, used: false });
            }
            Tag::Emphasis => self.styles.push(self.base().add_modifier(Modifier::ITALIC)),
            Tag::Strong => self.styles.push(self.base().add_modifier(Modifier::BOLD)),
            Tag::Strikethrough => self.styles.push(self.base().add_modifier(Modifier::CROSSED_OUT)),
            Tag::Link { dest_url, .. } => {
                self.links.push(dest_url.to_string());
                self.styles.push(self.base().fg(LINK).add_modifier(Modifier::UNDERLINED));
            }
            Tag::Image { dest_url, .. } => {
                self.links.push(dest_url.to_string());
                self.inline.push(("🖼 ".to_string(), style(MIST)));
                self.styles.push(self.base().fg(LINK));
            }
            Tag::Table(_) => {
                self.flush();
                self.gap();
                self.table = Some(Table::default());
            }
            _ => {}
        }
    }

    fn end(&mut self, tag: TagEnd) {
        match tag {
            TagEnd::Paragraph => self.flush(),
            TagEnd::Heading(_) => {
                self.flush();
                self.styles.pop();
            }
            TagEnd::BlockQuote(_) => {
                self.flush();
                self.quotes = self.quotes.saturating_sub(1);
                self.styles.pop();
            }
            TagEnd::CodeBlock => {
                if let Some((language, code)) = self.code.take() {
                    self.code_block(&language, &code);
                }
            }
            TagEnd::List(_) => {
                self.flush();
                self.lists.pop();
            }
            TagEnd::Item => {
                self.flush();
                self.items.pop();
            }
            TagEnd::Emphasis | TagEnd::Strong | TagEnd::Strikethrough => {
                self.styles.pop();
            }
            TagEnd::Link | TagEnd::Image => {
                self.styles.pop();
                let target = self.links.pop().unwrap_or_default();
                let shown: String = self.inline.iter().map(|(text, _)| text.as_str()).collect();
                // A bare address is its own text; for anything else the address follows in brackets.
                if !target.is_empty() && !shown.ends_with(&target) && !self.in_table() {
                    self.inline.push((format!(" ({target})"), style(DUSK)));
                }
            }
            TagEnd::TableCell => {
                let cell: String = self.inline.drain(..).map(|(text, _)| text).collect();
                if let Some(table) = self.table.as_mut() {
                    table.row.push(cell);
                }
            }
            TagEnd::TableHead => {
                if let Some(table) = self.table.as_mut() {
                    let row = std::mem::take(&mut table.row);
                    table.rows.push(row);
                    table.header_rows = 1;
                }
            }
            TagEnd::TableRow => {
                if let Some(table) = self.table.as_mut() {
                    let row = std::mem::take(&mut table.row);
                    table.rows.push(row);
                }
            }
            TagEnd::Table => {
                if let Some(table) = self.table.take() {
                    self.table_block(&table);
                }
            }
            _ => {}
        }
    }

    fn in_table(&self) -> bool {
        self.table.is_some()
    }

    /// An empty line between top-level blocks.
    fn gap(&mut self) {
        if self.items.is_empty() && self.out.last().is_some_and(|line| !line.is_empty()) {
            self.out.push(Vec::new());
        }
    }

    /// What starts a line here: quote bars, and the list markers or the room
    /// they take.
    fn prefix(&self, with_marker: bool) -> Line {
        let mut line: Line = (0..self.quotes).map(|_| ("▎ ".to_string(), style(DUSK))).collect();
        for (index, item) in self.items.iter().enumerate() {
            let innermost = index + 1 == self.items.len();
            if innermost && with_marker && !item.used {
                line.push((item.marker.clone(), style(MIST)));
            } else {
                line.push((" ".repeat(text::width(&item.marker)), style(FOG)));
            }
        }
        line
    }

    /// Columns left for text after the prefix.
    fn room(&self) -> usize {
        self.width.saturating_sub(text::line_width(&self.prefix(false))).max(8)
    }

    fn flush(&mut self) {
        if self.inline.is_empty() {
            return;
        }
        let inline = std::mem::take(&mut self.inline);
        for (index, wrapped) in text::wrap(&inline, self.room()).into_iter().enumerate() {
            let mut line = self.prefix(index == 0);
            line.extend(wrapped);
            self.out.push(line);
        }
        if let Some(item) = self.items.last_mut() {
            item.used = true;
        }
    }

    fn code_block(&mut self, language: &str, code: &str) {
        self.gap();
        let inner = self.room();
        let content = inner.saturating_sub(2).max(4);
        let pane = Style::default().bg(CODE_BG);
        let padded = |mut spans: Line, used: usize, prefix: &Line| -> Line {
            let mut line = prefix.clone();
            line.push((" ".to_string(), pane));
            line.append(&mut spans);
            line.push((" ".repeat(content.saturating_sub(used) + 1), pane));
            line
        };
        let marked = self.prefix(true);
        let rest = self.prefix(false);
        // The list marker, if any, goes on the block's first row only.
        let mut first = true;
        if !language.is_empty() {
            let label = text::fit(language, content);
            let used = text::width(&label);
            self.out.push(padded(vec![(label, pane.fg(DUSK))], used, &marked));
            first = false;
        }
        for tokens in highlight::highlight(language, code) {
            let mut rows: Vec<(Line, usize)> = vec![(Vec::new(), 0)];
            for token in tokens {
                let mut token_style = pane.fg(token.colour);
                if token.italic {
                    token_style = token_style.add_modifier(Modifier::ITALIC);
                }
                for c in token.text.chars() {
                    let w = unicode_width::UnicodeWidthChar::width(c).unwrap_or(0);
                    if rows.last().is_some_and(|(_, used)| used + w > content) {
                        rows.push((Vec::new(), 0));
                    }
                    let (spans, used) = rows.last_mut().expect("a row always exists");
                    match spans.last_mut() {
                        Some((text, last)) if *last == token_style => text.push(c),
                        _ => spans.push((c.to_string(), token_style)),
                    }
                    *used += w;
                }
            }
            for (spans, used) in rows {
                let row = padded(spans, used, if first { &marked } else { &rest });
                first = false;
                self.out.push(row);
            }
        }
        if let Some(item) = self.items.last_mut() {
            item.used = true;
        }
    }

    fn table_block(&mut self, table: &Table) {
        let columns = table.rows.iter().map(Vec::len).max().unwrap_or(0);
        if columns == 0 {
            return;
        }
        let mut widths: Vec<usize> = (0..columns)
            .map(|c| table.rows.iter().filter_map(|row| row.get(c)).map(|cell| text::width(cell)).max().unwrap_or(0).max(1))
            .collect();
        let room = self.room();
        let separators = 3 * (columns - 1);
        while widths.iter().sum::<usize>() + separators > room {
            let Some((widest, _)) = widths.iter().enumerate().max_by_key(|(_, w)| **w) else { break };
            if widths[widest] <= MIN_COLUMN {
                break;
            }
            widths[widest] -= 1;
        }
        let prefix = self.prefix(false);
        for (index, row) in table.rows.iter().enumerate() {
            let header = index < table.header_rows;
            let mut line = prefix.clone();
            for (c, width) in widths.iter().enumerate() {
                if c > 0 {
                    line.push((" │ ".to_string(), style(DUSK)));
                }
                let cell = text::fit(row.get(c).map(String::as_str).unwrap_or(""), *width);
                let pad = width.saturating_sub(text::width(&cell));
                let cell_style = if header { style(MOON).add_modifier(Modifier::BOLD) } else { style(FOG) };
                line.push((format!("{cell}{}", " ".repeat(pad)), cell_style));
            }
            self.out.push(line);
            if header {
                let mut rule = prefix.clone();
                rule.push((widths.iter().map(|w| "─".repeat(*w)).collect::<Vec<_>>().join("─┼─"), style(DUSK)));
                self.out.push(rule);
            }
        }
    }

    fn finish(mut self) -> Vec<Line> {
        self.flush();
        if let Some((language, code)) = self.code.take() {
            self.code_block(&language, &code);
        }
        while self.out.last().is_some_and(|line| line.is_empty()) {
            self.out.pop();
        }
        self.out
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(lines: &[Line]) -> Vec<String> {
        lines.iter().map(|line| line.iter().map(|(text, _)| text.as_str()).collect::<String>().trim_end().to_string()).collect()
    }

    fn rendered(source: &str) -> Vec<String> {
        plain(&render(source, 40))
    }

    #[test]
    fn a_display_formula_reserves_rows_and_is_reported_where_they_start() {
        let (lines, formulas) = render_with_math("before\n\\[\n\\sqrt{9}=3\n\\]\nafter", 40, Some((10, 20)));
        let [formula] = formulas.as_slice() else { panic!("one formula, got {formulas:?}") };
        assert_eq!(plain(&lines)[0], "before");
        assert_eq!(formula.line, 2, "a blank line separates it from the text before");
        assert_eq!(formula.tex, "\\sqrt{9}=3");
        let reserved = &plain(&lines)[formula.line..formula.line + formula.fit.rows as usize];
        assert!(reserved.iter().all(String::is_empty));
        assert_eq!(plain(&lines)[formula.line + formula.fit.rows as usize + 1], "after");
    }

    #[test]
    fn a_formula_ending_the_answer_keeps_its_rows() {
        let (lines, formulas) = render_with_math("so\n$$x^2$$", 40, Some((10, 20)));
        assert_eq!(lines.len(), formulas[0].line + formulas[0].fit.rows as usize);
    }

    #[test]
    fn without_a_cell_size_or_when_ratex_cannot_read_it_a_formula_is_text() {
        assert_eq!(render("so\n\\[\\sqrt{9}\\approx3\\]", 40).iter().map(|l| l.iter().map(|(t, _)| t.as_str()).collect::<String>()).collect::<Vec<_>>(), ["so", "", "√9≈3"]);
        let (lines, formulas) = render_with_math("\\[\\notacommand{\\]", 40, Some((10, 20)));
        assert!(formulas.is_empty());
        assert_eq!(plain(&lines), ["\\notacommand{}"]);
    }

    #[test]
    fn a_fenced_block_loses_its_backticks_and_keeps_its_lines() {
        let lines = rendered("запусти:\n```sh\nghostty --config-file=config.ghostty\n```\nготово");
        assert_eq!(lines, ["запусти:", "", " sh", " ghostty --config-file=config.ghostty", "", "готово"]);
        assert!(lines.iter().all(|line| !line.contains('`')));
    }

    #[test]
    fn code_lines_sit_on_the_code_pane_to_its_full_width() {
        let lines = render("```\nab\n```", 20);
        assert_eq!(lines.len(), 1);
        assert!(lines[0].iter().all(|(_, style)| style.bg == Some(CODE_BG)));
        assert_eq!(text::line_width(&lines[0]), 20);
    }

    #[test]
    fn a_long_code_line_wraps_inside_the_pane() {
        let lines = render(&format!("```\n{}\n```", "x".repeat(50)), 20);
        assert_eq!(lines.len(), 3);
        assert!(lines.iter().all(|line| text::line_width(line) <= 20));
    }

    #[test]
    fn headings_are_bold_and_set_off_by_blank_lines() {
        let lines = render("текст\n\n## Заголовок\n\nещё", 40);
        assert_eq!(plain(&lines), ["текст", "", "Заголовок", "", "ещё"]);
        assert!(lines[2][0].1.add_modifier.contains(Modifier::BOLD));
    }

    #[test]
    fn lists_show_one_marker_and_indent_what_wraps_and_nests() {
        let lines = rendered("- первый пункт, который длинный и переносится на вторую строку\n- второй\n  - вложенный\n\n1. раз\n2. два");
        assert_eq!(
            lines,
            [
                "• первый пункт, который длинный и",
                "  переносится на вторую строку",
                "• второй",
                "  • вложенный",
                "",
                "1. раз",
                "2. два",
            ]
        );
    }

    #[test]
    fn single_newlines_stay_line_breaks() {
        assert_eq!(rendered("раз\nдва\nтри"), ["раз", "два", "три"]);
    }

    #[test]
    fn quotes_get_a_bar_and_rules_a_line() {
        assert_eq!(rendered("> цитата\n\n---\n\nконец"), ["▎ цитата", "", "────────────────────────────────────────", "", "конец"]);
    }

    #[test]
    fn inline_styles_are_applied_and_links_show_their_address() {
        let lines = render("**жирно** и `код` и [сайт](https://a.b)", 60);
        assert_eq!(plain(&lines), ["жирно и код и сайт (https://a.b)"]);
        let line = &lines[0];
        assert!(line[0].1.add_modifier.contains(Modifier::BOLD));
        assert_eq!(line[2].1.fg, Some(MOON));
    }

    #[test]
    fn a_bare_link_is_not_repeated() {
        assert_eq!(rendered("<https://a.b>"), ["https://a.b"]);
    }

    #[test]
    fn tables_align_their_columns_under_a_ruled_header() {
        let lines = rendered("| имя | число |\n|---|---|\n| a | 10 |\n| bbb | 2 |");
        assert_eq!(lines, ["имя │ число", "────┼──────", "a   │ 10", "bbb │ 2"]);
    }

    #[test]
    fn an_unfinished_fence_still_draws_what_has_arrived() {
        let lines = rendered("```rust\nfn main() {");
        assert_eq!(lines, [" rust", " fn main() {"]);
    }

    #[test]
    fn task_lists_show_boxes() {
        assert_eq!(rendered("- [x] сделано\n- [ ] нет"), ["• ☑ сделано", "• ☐ нет"]);
    }
}
