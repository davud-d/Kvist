// The Kvist core as a child process speaking `--frontend stdio-multi`
// (docs/UI_API.md §8, §8c): one JSON object per line each way. Events are read
// on a thread; the interface polls them between frames.

use std::fs::File;
use std::io::{self, BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::mpsc::{self, Receiver};
use std::thread;

use serde_json::Value;

/// A checkout's interpreter and entry point, for running from the source tree.
const CHECKOUT: &str = concat!(env!("CARGO_MANIFEST_DIR"), "/../../..");

pub enum Message {
    Event(Value),
    /// The core's stdout closed; the text says how it ended.
    Gone(String),
}

/// A session's journal as `--session-op history` returns it (docs/UI_API.md §8).
pub struct History {
    /// The §7 event objects, oldest first.
    pub events: Vec<Value>,
    /// Older events exist than the page holds.
    pub has_more: bool,
}

/// Newest journal events asked for when a session is redrawn.
const HISTORY_LIMIT: &str = "500";

pub struct Core {
    child: Child,
    argv: Vec<String>,
    workspace: PathBuf,
    /// `None` once closed, which tells the core to finish.
    stdin: Option<ChildStdin>,
    pub messages: Receiver<Message>,
    pub log: PathBuf,
}

/// The command that starts the core: `KVIST_CORE_CMD` (a JSON array of
/// strings, as the launcher passes it to the Ink app) or the checkout's
/// `.venv/bin/python kernel/__main__.py`.
pub fn command(env_value: Option<&str>) -> Result<Vec<String>, String> {
    let Some(raw) = env_value else {
        let root = Path::new(CHECKOUT);
        return Ok(vec![
            root.join(".venv/bin/python").display().to_string(),
            root.join("kernel/__main__.py").display().to_string(),
        ]);
    };
    match serde_json::from_str::<Vec<String>>(raw) {
        Ok(argv) if !argv.is_empty() && argv.iter().all(|a| !a.is_empty()) => Ok(argv),
        _ => Err(format!("KVIST_CORE_CMD must be a non-empty JSON array of strings, got {raw}")),
    }
}

impl Core {
    pub fn spawn(argv: &[String], workspace: &Path) -> io::Result<Core> {
        let log = std::env::temp_dir().join(format!("kvist-glass-core-{}.log", std::process::id()));
        let mut child = Command::new(&argv[0])
            .args(&argv[1..])
            // `--wake`: this interface shows turns the core starts by itself, so
            // helpers and background jobs may outlive an answer.
            .args(["--workspace", &workspace.display().to_string(), "--frontend", "stdio-multi", "--wake"])
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(File::create(&log)?)
            .spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| io::Error::other("the core has no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| io::Error::other("the core has no stdout"))?;
        let (sender, messages) = mpsc::channel();
        thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let message = match line {
                    Ok(line) if line.trim().is_empty() => continue,
                    Ok(line) => match serde_json::from_str::<Value>(&line) {
                        Ok(event) => Message::Event(event),
                        Err(error) => Message::Gone(format!("unreadable line from the core ({error})")),
                    },
                    Err(error) => Message::Gone(format!("reading the core failed ({error})")),
                };
                let gone = matches!(message, Message::Gone(_));
                if sender.send(message).is_err() || gone {
                    return;
                }
            }
            // The receiver may already be gone when the interface quits first.
            let _ = sender.send(Message::Gone("the core exited".to_string()));
        });
        Ok(Core { child, argv: argv.to_vec(), workspace: workspace.to_path_buf(), stdin: Some(stdin), messages, log })
    }

    /// Runs a one-shot core process (`--session-op …`) on a thread and sends
    /// what `parse` makes of its stdout. The running core is busy with its
    /// own stream, and these operations are not part of it.
    fn operation<T: Send + 'static>(
        &self,
        extra: Vec<String>,
        input: Option<String>,
        parse: fn(&str) -> Result<T, String>,
    ) -> Receiver<Result<T, String>> {
        let argv = self.argv.clone();
        let workspace = self.workspace.clone();
        let (sender, results) = mpsc::channel();
        thread::spawn(move || {
            let mut command = Command::new(&argv[0]);
            command
                .args(&argv[1..])
                .args(["--workspace", &workspace.display().to_string()])
                .args(&extra)
                .stdin(if input.is_some() { Stdio::piped() } else { Stdio::null() })
                .stdout(Stdio::piped())
                .stderr(Stdio::null());
            let parsed = command.spawn().map_err(|error| error.to_string()).and_then(|mut child| {
                if let (Some(text), Some(mut stdin)) = (input, child.stdin.take()) {
                    stdin.write_all(text.as_bytes()).map_err(|error| error.to_string())?;
                }
                let output = child.wait_with_output().map_err(|error| error.to_string())?;
                parse(&String::from_utf8_lossy(&output.stdout))
            });
            // The receiver is gone when the person moved on.
            let _ = sender.send(parsed);
        });
        results
    }

    /// A session's journal (`--session-op history`, the newest events).
    pub fn history(&self, session: &str) -> Receiver<Result<History, String>> {
        let extra = ["--session-op", "history", "--session-name", session, "--history-limit", HISTORY_LIMIT];
        self.operation(extra.map(String::from).to_vec(), None, parse_history)
    }

    /// The workspace's earlier inputs, oldest first (`--session-op input-history`).
    pub fn input_history(&self) -> Receiver<Result<Vec<String>, String>> {
        self.operation(vec!["--session-op".into(), "input-history".into()], None, parse_entries)
    }

    /// Records an input in the workspace's history; nothing waits for it.
    pub fn remember_input(&self, text: &str) {
        drop(self.operation(vec!["--session-op".into(), "input-history-add".into()], Some(text.to_string()), |_| Ok(())));
    }

    pub fn send(&mut self, message: &Value) -> io::Result<()> {
        let stdin = self.stdin.as_mut().ok_or_else(|| io::Error::other("the core's input is closed"))?;
        let mut line = serde_json::to_vec(message)?;
        line.push(b'\n');
        stdin.write_all(&line)?;
        stdin.flush()
    }
}

impl Drop for Core {
    /// Closing stdin is the core's signal to finish (it cancels an open dialog
    /// and exits); a core that does not is killed. The log is removed after a
    /// clean exit and kept after any other, for whoever looks into it.
    fn drop(&mut self) {
        drop(self.stdin.take());
        let deadline = std::time::Instant::now() + std::time::Duration::from_millis(1500);
        while std::time::Instant::now() < deadline {
            match self.child.try_wait() {
                Ok(Some(status)) => {
                    if status.success()
                        && let Err(error) = std::fs::remove_file(&self.log)
                    {
                        eprintln!("kvist-glass: could not remove {}: {error}", self.log.display());
                    }
                    return;
                }
                Ok(None) => thread::sleep(std::time::Duration::from_millis(30)),
                Err(error) => {
                    eprintln!("kvist-glass: could not wait for the core: {error}");
                    break;
                }
            }
        }
        if let Err(error) = self.child.kill() {
            eprintln!("kvist-glass: could not stop the core: {error}");
        }
    }
}

/// The reply of `--session-op history`: JSON on the last line of stdout.
fn parse_history(stdout: &str) -> Result<History, String> {
    let line = stdout.trim().lines().next_back().unwrap_or("");
    let reply: Value = serde_json::from_str(line).map_err(|error| format!("unreadable session history ({error})"))?;
    if reply.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(reply.get("error").and_then(Value::as_str).unwrap_or("no session history").to_string());
    }
    let events = reply
        .get("events")
        .and_then(Value::as_array)
        .map(|entries| entries.iter().filter_map(|entry| entry.get("event").cloned()).collect())
        .unwrap_or_default();
    Ok(History { events, has_more: reply.get("has_more").and_then(Value::as_bool) == Some(true) })
}

/// The reply of `--session-op input-history`: `{"ok":true,"entries":[…]}`.
fn parse_entries(stdout: &str) -> Result<Vec<String>, String> {
    let line = stdout.trim().lines().next_back().unwrap_or("");
    let reply: Value = serde_json::from_str(line).map_err(|error| format!("unreadable input history ({error})"))?;
    if reply.get("ok").and_then(Value::as_bool) != Some(true) {
        return Err(reply.get("error").and_then(Value::as_str).unwrap_or("no input history").to_string());
    }
    let entries = reply.get("entries").and_then(Value::as_array);
    Ok(entries.map(|all| all.iter().filter_map(|entry| entry.as_str().map(str::to_string)).collect()).unwrap_or_default())
}

/// `message` of a `command_result` is "English / Русский"; this picks one half.
pub fn localized(message: &str, russian: bool) -> &str {
    match message.split_once(" / ") {
        Some((english, russian_text)) => if russian { russian_text } else { english },
        None => message,
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_core_command_comes_from_the_launcher_or_the_checkout() {
        assert_eq!(command(Some(r#"["kvist-core"]"#)), Ok(vec!["kvist-core".to_string()]));
        assert!(command(Some("[]")).is_err());
        assert!(command(Some("kvist-core")).is_err());
        let default = command(None).unwrap();
        assert!(default[0].ends_with(".venv/bin/python") && default[1].ends_with("kernel/__main__.py"));
    }

    #[test]
    fn a_history_reply_gives_its_events_oldest_first() {
        let reply = r#"{"ok": true, "has_more": true, "events": [
            {"seq": 1, "turn": 1, "event": {"type": "user", "text": "hi"}},
            {"seq": 2, "turn": 1, "event": {"type": "turn_end"}}]}"#;
        let history = parse_history(&format!("noise\n{}", reply.replace('\n', " "))).unwrap();
        assert!(history.has_more);
        let kinds: Vec<&str> = history.events.iter().filter_map(|e| e["type"].as_str()).collect();
        assert_eq!(kinds, ["user", "turn_end"]);
    }

    #[test]
    fn the_input_history_reply_lists_entries_oldest_first() {
        assert_eq!(parse_entries(r#"{"ok": true, "entries": ["a", "b"]}"#).unwrap(), ["a", "b"]);
        assert!(parse_entries(r#"{"ok": false, "error": "x"}"#).is_err());
    }

    #[test]
    fn a_failed_history_reply_says_why() {
        assert_eq!(parse_history(r#"{"ok": false, "error": "unknown session"}"#).err().as_deref(), Some("unknown session"));
        assert!(parse_history("").is_err());
    }

    #[test]
    fn a_bilingual_message_shows_one_language() {
        assert_eq!(localized("Role set to: coder / Роль: coder", true), "Роль: coder");
        assert_eq!(localized("Role set to: coder / Роль: coder", false), "Role set to: coder");
        assert_eq!(localized("Cancelled.", true), "Cancelled.");
    }
}
