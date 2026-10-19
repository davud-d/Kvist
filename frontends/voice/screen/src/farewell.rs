// What stays on the terminal after the alternate screen is left: the Kvist mark, "Kvist agent
// stopped." and the session to come back to. The same words as the Ink app's farewell.ts and
// the classic terminal's `render_exit_message`. Kept as a copy in frontends/tui/glass and
// frontends/voice/screen; a change here is made in the other by hand.

use std::path::Path;
use std::process::Command;

use serde_json::Value;

const DIM: &str = "\x1b[2m";
const BOLD: &str = "\x1b[1m";
const RED: &str = "\x1b[31m";
const RESET: &str = "\x1b[0m";
/// The colours of the wordmark: "Kvi" in blue, "st" in cyan.
const SHADE: (u8, u8, u8) = (0x1f, 0x6f, 0xe0);
const MAIN: (u8, u8, u8) = (0x12, 0xc8, 0xff);
/// Width of the large mark in cells, and the narrowest window that leaves room beside it.
const LARGE_WIDTH: usize = 48;

const K: [&str; 6] = ["#    #", "#   # ", "#  #  ", "###   ", "#   # ", "#    #"];
const V: [&str; 6] = ["     ", "     ", "#   #", "#   #", " # # ", "  #  "];
const I: [&str; 6] = ["#", " ", "#", "#", "#", "#"];
const S: [&str; 6] = ["     ", " ####", "#    ", "#####", "    #", " ####"];
const T: [&str; 6] = ["   ", " # ", "###", " # ", " # ", " ##"];

/// The pixel rows of a word, one space between letters.
fn pixels(letters: &[&[&str; 6]]) -> Vec<String> {
    (0..6).map(|row| letters.iter().map(|glyph| glyph[row]).collect::<Vec<_>>().join(" ")).collect()
}

/// The large mark paints a pixel as "██" (a cell is about twice as tall as wide); the compact
/// one packs two pixel rows into one text row.
fn paint(grid: &[String], large: bool) -> Vec<String> {
    if large {
        return grid.iter().map(|line| line.chars().map(|c| if c == '#' { "██" } else { "  " }).collect()).collect();
    }
    let rows: Vec<Vec<bool>> = grid.iter().map(|line| line.chars().map(|c| c == '#').collect()).collect();
    rows.chunks(2)
        .map(|pair| {
            (0..pair[0].len())
                .map(|col| match (pair[0][col], pair.get(1).is_some_and(|below| below[col])) {
                    (true, true) => '█',
                    (true, false) => '▀',
                    (false, true) => '▄',
                    (false, false) => ' ',
                })
                .collect()
        })
        .collect()
}

fn colour((r, g, b): (u8, u8, u8)) -> String {
    format!("\x1b[38;2;{r};{g};{b}m")
}

/// The farewell for a window `columns` wide. `session` is the session to resume, if there is one.
pub fn text(session: Option<&str>, columns: u16) -> String {
    let large = columns as usize >= LARGE_WIDTH + 2;
    let shade = paint(&pixels(&[&K, &V, &I]).iter().map(|line| format!("{line} ")).collect::<Vec<_>>(), large);
    let main = paint(&pixels(&[&S, &T]), large);
    let mut out = String::from("\n");
    for (shade, main) in shade.iter().zip(&main) {
        out.push_str(&format!("{}{shade}{}{main}{RESET}\n", colour(SHADE), colour(MAIN)));
    }
    out.push_str(&format!("\n{DIM}Kvist agent{RESET} {RED}stopped.{RESET}\n"));
    if let Some(name) = session {
        out.push_str(&format!("{DIM}To continue, start the agent and resume session {RESET}{BOLD}{DIM}{name}{RESET}{DIM}.{RESET}\n"));
    }
    out.push('\n');
    out
}

/// The core's active session when something happened in it (a journal or a checkpoint), read the
/// way the Ink app does: `--session-op list`. A session nothing happened in has nothing to resume.
pub fn resumable_session(core: &[String], workspace: &Path) -> Option<String> {
    let output = Command::new(core.first()?)
        .args(&core[1..])
        .args(["--workspace", &workspace.display().to_string(), "--session-op", "list"])
        .output()
        .ok()?;
    let stdout = String::from_utf8_lossy(&output.stdout);
    let reply: Value = serde_json::from_str(stdout.trim().lines().last()?).ok()?;
    resumable_in(&reply)
}

fn resumable_in(reply: &Value) -> Option<String> {
    let active = reply.get("active_session")?.as_str()?;
    let used = reply.get("session_items")?.as_array()?.iter().find(|item| item.get("name").and_then(Value::as_str) == Some(active))?;
    let journal = used.get("journal_bytes").and_then(Value::as_u64).unwrap_or(0);
    let checkpoint = used.get("has_checkpoint").and_then(Value::as_bool).unwrap_or(false);
    (journal > 0 || checkpoint).then(|| active.to_string())
}

/// Prints the farewell on the restored screen.
pub fn print(core: &[String], workspace: &Path) {
    let columns = crossterm::terminal::size().map(|(columns, _)| columns).unwrap_or(80);
    print!("{}", text(resumable_session(core, workspace).as_deref(), columns));
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn a_session_with_a_journal_or_a_checkpoint_is_offered() {
        let items = |journal: u64, checkpoint: bool| {
            json!({"active_session": "chat-1", "session_items": [{"name": "chat-1", "journal_bytes": journal, "has_checkpoint": checkpoint}]})
        };
        assert_eq!(resumable_in(&items(10, false)).as_deref(), Some("chat-1"));
        assert_eq!(resumable_in(&items(0, true)).as_deref(), Some("chat-1"));
        assert_eq!(resumable_in(&items(0, false)), None);
        assert_eq!(resumable_in(&json!({"active_session": "other", "session_items": []})), None);
    }

    #[test]
    fn the_text_names_the_session_and_fits_both_window_sizes() {
        let narrow = text(Some("chat-1"), 40);
        assert!(narrow.contains("chat-1") && narrow.contains("stopped."));
        assert!(!text(None, 40).contains("To continue"));
        let wide_rows = text(None, 100).lines().filter(|l| l.contains("██")).count();
        assert_eq!(wide_rows, 6);
        assert_eq!(narrow.lines().filter(|l| l.contains('▀') || l.contains('█')).count(), 3);
    }
}
