// The model's thinking as the conversation shows it (docs/UI_API.md §7, `reasoning_*`): a window of its
// latest lines while it thinks, folded into one line when it ends.

use std::time::{Duration, Instant};

use ratatui::style::Style;

use crate::anim::Ease;
use crate::strings::strings;
use crate::text::{self, Line};
use crate::theme::{self, DUSK, INK_SOFT, MIST, MOON};

/// Lines of thinking shown while the model thinks.
const WINDOW_ROWS: usize = 5;
/// How long the window takes to fold once the thinking ends.
const FOLD: Duration = Duration::from_millis(320);
/// A provider that sends its thinking in bursts is shown by catching up: the part not yet shown is
/// drawn within about this long, never slower than `MIN_RATE`.
const CATCH_UP_SECONDS: f32 = 0.12;
const MIN_RATE: f32 = 90.0;
/// A backlog this small is not a burst: it is drawn at once, so a slow provider is never held back.
const INSTANT_CHARS: usize = 6;
/// The top row of the window is this dim, the bottom one full.
const TOP_FADE: f32 = 0.25;

#[derive(Clone, Debug)]
pub struct Thought {
    text: String,
    chars: usize,
    /// How many characters are drawn; fractional so a frame's share of a slow rate is not lost.
    shown: f32,
    started: Instant,
    last_tick: Instant,
    /// When the thinking ended and for how many seconds the core says it lasted.
    ended: Option<(Instant, f32)>,
}

impl Thought {
    pub fn new(now: Instant) -> Self {
        Thought { text: String::new(), chars: 0, shown: 0.0, started: now, last_tick: now, ended: None }
    }

    pub fn started(&self) -> Instant {
        self.started
    }

    pub fn active(&self) -> bool {
        self.ended.is_none()
    }

    /// A thought that ended with no words (the provider hides its thinking): nothing to show.
    pub fn hidden(&self) -> bool {
        self.ended.is_some() && self.text.trim().is_empty()
    }

    pub fn has_text(&self) -> bool {
        !self.text.trim().is_empty()
    }

    /// `append`: `text` goes after what arrived; otherwise the thought is `text` as a whole.
    pub fn push(&mut self, text: &str, append: bool) {
        if append {
            self.text.push_str(text);
        } else {
            self.text = text.to_string();
        }
        self.chars = self.text.chars().count();
        self.shown = self.shown.min(self.chars as f32);
    }

    pub fn tick(&mut self, now: Instant) {
        let dt = now.saturating_duration_since(self.last_tick).as_secs_f32();
        self.last_tick = now;
        if self.ended.is_some() {
            return;
        }
        let backlog = self.chars as f32 - self.shown;
        if backlog <= INSTANT_CHARS as f32 {
            self.shown = self.chars as f32;
            return;
        }
        let rate = (backlog / CATCH_UP_SECONDS).max(MIN_RATE);
        self.shown = (self.shown + rate * dt).min(self.chars as f32);
    }

    pub fn finish(&mut self, now: Instant, seconds: f32) {
        self.shown = self.chars as f32;
        self.ended = Some((now, seconds));
    }

    /// True while something about it still changes by itself: the window folding.
    pub fn folding(&self, now: Instant) -> bool {
        self.ended.is_some_and(|(at, _)| !self.hidden() && now.saturating_duration_since(at) < FOLD)
    }

    pub fn lines(&self, width: usize, now: Instant, ms: u128) -> Vec<Line> {
        let s = strings();
        let (header, open) = match self.ended {
            None => {
                let mut row = text::shimmer(s.thinking, ms, DUSK, MOON);
                let secs = now.saturating_duration_since(self.started).as_secs();
                row.push((format!("  {secs} {}", s.seconds), Style::default().fg(DUSK)));
                (row, 1.0)
            }
            Some((at, seconds)) => {
                let label = format!("{} {} {}", s.thought, seconds.round() as u64, s.seconds);
                let t = now.saturating_duration_since(at).as_secs_f32() / FOLD.as_secs_f32();
                (vec![(label, Style::default().fg(DUSK))], 1.0 - Ease::OutCubic.apply(t))
            }
        };
        let mut lines = vec![header];
        lines.extend(self.window(width, open));
        lines
    }

    /// The last rows of the thinking so far; `open` (1 down to 0) is how much of the window is left.
    fn window(&self, width: usize, open: f32) -> Vec<Line> {
        if open <= 0.0 {
            return Vec::new();
        }
        let visible: String = self.text.chars().take(self.shown as usize).collect();
        let inner = width.saturating_sub(2);
        let wrapped: Vec<Line> = visible
            .split('\n')
            .filter(|paragraph| !paragraph.trim().is_empty())
            .flat_map(|paragraph| text::wrap(&vec![(paragraph.trim().to_string(), Style::default())], inner))
            .collect();
        let have = wrapped.len().min(WINDOW_ROWS);
        let rows = (have as f32 * open).ceil() as usize;
        let skip = wrapped.len() - rows.min(wrapped.len());
        wrapped
            .into_iter()
            .skip(skip)
            .enumerate()
            .map(|(i, line)| {
                let place = if rows <= 1 { 1.0 } else { i as f32 / (rows - 1) as f32 };
                let fade = (TOP_FADE + (1.0 - TOP_FADE) * place) * open;
                let color = theme::mix(INK_SOFT, MIST, fade);
                let mut row: Line = vec![("▏ ".to_string(), Style::default().fg(INK_SOFT))];
                row.extend(line.into_iter().map(|(word, style)| (word, style.fg(color))));
                row
            })
            .collect()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn plain(lines: &[Line]) -> Vec<String> {
        lines.iter().map(|l| l.iter().map(|(s, _)| s.as_str()).collect()).collect()
    }

    fn later(start: Instant, ms: u64) -> Instant {
        start + Duration::from_millis(ms)
    }

    #[test]
    fn a_small_backlog_is_drawn_at_once() {
        let t0 = Instant::now();
        let mut thought = Thought::new(t0);
        thought.push("hello", true);
        thought.tick(later(t0, 1));
        assert_eq!(plain(&thought.lines(40, later(t0, 1), 0))[1], "▏ hello");
    }

    #[test]
    fn a_burst_is_caught_up_within_a_second_not_at_once() {
        let t0 = Instant::now();
        let mut thought = Thought::new(t0);
        thought.push(&"word ".repeat(200), true);
        thought.tick(later(t0, 16));
        let first = thought.shown;
        assert!(first > 0.0 && first < 1000.0, "the first frame draws part of the burst: {first}");
        for ms in (32..900).step_by(16) {
            thought.tick(later(t0, ms));
        }
        assert_eq!(thought.shown, thought.chars as f32, "caught up in under a second");
    }

    #[test]
    fn the_window_keeps_the_latest_rows() {
        let t0 = Instant::now();
        let mut thought = Thought::new(t0);
        let text = (1..=9).map(|n| format!("line {n}")).collect::<Vec<_>>().join("\n");
        thought.push(&text, true);
        thought.tick(later(t0, 1000));
        let rows = plain(&thought.lines(40, later(t0, 1000), 0));
        assert_eq!(rows.len(), 1 + WINDOW_ROWS);
        assert_eq!(rows.last().unwrap(), "▏ line 9");
    }

    #[test]
    fn a_rewound_thought_replaces_the_text() {
        let t0 = Instant::now();
        let mut thought = Thought::new(t0);
        thought.push("first idea", true);
        thought.push("second", false);
        thought.tick(later(t0, 1));
        assert_eq!(plain(&thought.lines(40, later(t0, 1), 0))[1], "▏ second");
    }

    #[test]
    fn it_folds_into_one_line_when_it_ends() {
        let t0 = Instant::now();
        let mut thought = Thought::new(t0);
        thought.push("some thinking", true);
        thought.finish(later(t0, 2000), 2.0);
        assert!(thought.folding(later(t0, 2100)));
        assert!(plain(&thought.lines(40, later(t0, 2100), 0)).len() > 1, "still folding");
        let done = plain(&thought.lines(40, later(t0, 2400), 0));
        assert_eq!(done.len(), 1);
        assert!(!thought.folding(later(t0, 2400)));
    }

    #[test]
    fn a_thought_with_no_words_is_hidden_once_it_ends() {
        let t0 = Instant::now();
        let mut thought = Thought::new(t0);
        assert!(!thought.hidden(), "while it runs it shows its header");
        thought.finish(later(t0, 500), 0.5);
        assert!(thought.hidden());
    }
}
